"""
agent_albert_mcp.py — Boucle agentique gouvernée : Albert (modèle) + fork MCP (outils).

v9 : grounding computationnel + durcissement d'orchestration.
  - `aggregate_resource_data` ajouté à la whitelist : le CALCUL (somme, moyenne,
    min, max, comptage) est confié à une primitive déterministe côté outil au lieu
    d'être exécuté par le modèle. Motivation : hallucination d'agrégation observée
    sur Q1 (Ministral renvoie 1 022 300 000 € au lieu de 781 237 000 € alors qu'il
    a la bonne ressource et la bonne colonne). La règle de prompt correspondante
    n'est activée QUE si le serveur MCP expose réellement l'outil (USE_AGG=1 défaut).
  - validate_call durcie : un identifiant de DATASET (24 hex) passé comme
    `resource_id` à query_resource_data / aggregate_resource_data est REFUSÉ avec un
    message d'aiguillage (utilise d'abord list_dataset_resources). Un resource_id
    non-UUID est refusé. Évite de gaspiller des étapes sur un appel impossible.
  - Signaux d'orchestration INFORMATIFS, non prescriptifs (enseignement v7/v8 :
    l'injonction « explore d'autres ressources » DÉSTABILISAIT une trajectoire qui
    convergeait). Un 404 tabulaire devient le FAIT « ressource non tabularisée »
    (flag `tabular_unavailable`) ; un 400 de colonne/filtre devient « colonne/filtre
    inexistant, prévisualise sans filtre » (flag `schema_or_filter_error`). On
    informe ; la consigne « ne conclus pas l'absence » reste au niveau système (B).
  - flag `rate_retried` : un 429/5xx a été absorbé par une reprise, mais le run a
    abouti (distinct de `rate_limited`, qui est un échec).
v8 : (intégré ici) signal tabulaire + rate_retried.
v7 : prompt système sélectionnable via PROMPT_VARIANT — A (un seul jeu choisi tôt)
ou B (multi-candidats ; cible le piège du jeu voisin).
v6 : multi-fournisseur — LLM_PROVIDER=albert (défaut) ou mistral.
v5 : assainissement du tool-call ré-expédié (arguments JSON illisibles -> "{}").
v4 : robustesse erreurs API (4xx/5xx n'interrompent plus la campagne).
v3 : prompt ferme + réparation d'arguments. v2 : quotas (429/503) + pacing.

C'est le coeur "versant orchestration" d'Article 2 : le modèle PILOTE les appels,
on les exécute contre le serveur MCP, on reboucle, en journalisant chaque étape.

Gouvernance (section 8.3 de l'article) :
- whitelist d'outils (ALLOWED_TOOLS) ;
- contrainte de périmètre (outils topic bornés à TOPIC_ID) ;
- validation des identifiants de ressource ;
- calcul déterministe délégué à un outil (aggregate_resource_data) ;
- journalisation inspectable par étape (trace.jsonl) ;
- paramètres figés (seed, temperature=0) -> rejouabilité documentée.

Pré-requis : serveur MCP (fork) lancé localement avec aggregate_resource_data
enregistré, p.ex. :
    cd external/datagouv-mcp && MCP_HOST=127.0.0.1 MCP_PORT=8007 ... uv run main.py
Clé Albert via l'environnement (secret Codespaces ALBERT_API_KEY).
Lancer depuis le dossier experiment/ (pour trouver questions.csv).
"""

import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Configuration figée -----------------------------------------------------
PROVIDER = os.getenv("LLM_PROVIDER", "albert").lower()
if PROVIDER == "mistral":
    BASE_URL = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1").rstrip("/")
    API_KEY = os.getenv("MISTRAL_API_KEY") or os.getenv("LLM_API_KEY")
    SEED_PARAM, MAXTOK_PARAM = "random_seed", "max_tokens"
else:  # albert (défaut) ou tout endpoint compatible OpenAI
    BASE_URL = os.getenv("ALBERT_BASE_URL", "https://albert.api.etalab.gouv.fr/v1").rstrip("/")
    API_KEY = os.getenv("ALBERT_API_KEY") or os.getenv("LLM_API_KEY")
    SEED_PARAM, MAXTOK_PARAM = "seed", "max_completion_tokens"

MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8007/mcp")
TOPIC_ID = os.getenv("TOPIC_ID", "univers-culture-deps")

# v9 : LLM_MODELS prioritaire (clarté multi-fournisseur), ALBERT_MODELS conservé
# pour compatibilité. Permet ALBERT_MODELS="mistral-medium-latest" sans ambiguïté.
_models_env = os.getenv("LLM_MODELS") or os.getenv("ALBERT_MODELS")
MODELS = (_models_env.split(",") if _models_env
          else ["openai/gpt-oss-120b",
                "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
                "mistralai/Ministral-3-8B-Instruct-2512"])

QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "questions.csv")
QUESTION_IDS = (set(os.getenv("QUESTION_IDS").split(","))
                if os.getenv("QUESTION_IDS") else None)

NB_RUNS = int(os.getenv("NB_RUNS", "3"))
SEED = int(os.getenv("SEED", "42"))
TEMPERATURE = 0
MAX_TOKENS = 1024
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))
TOOL_RESULT_CHAR_CAP = 6000

RATE_DELAY = float(os.getenv("RATE_DELAY", "1.5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
CHAIN_DELAY = float(os.getenv("CHAIN_DELAY", "2.0"))

# Gouvernance : seuls ces outils sont exposés au modèle.
ALLOWED_TOOLS = {
    "list_topic_elements", "get_topic_catalog", "query_resource_data",
    "list_dataset_resources", "get_dataset_info", "get_resource_info",
    "aggregate_resource_data",            # v9 : calcul déterministe délégué
}
TOPIC_SCOPED_TOOLS = {"list_topic_elements", "get_topic_catalog"}
# Outils prenant un resource_id qui DOIT être un UUID de ressource (pas un dataset_id).
RESOURCE_ID_TOOLS = {"query_resource_data", "aggregate_resource_data"}

RESOURCE_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
DATASET_ID_RE = re.compile(r"^[0-9a-f]{24}$", re.I)

# Prompt A : version d'origine (un seul jeu, choisi tôt).
SYSTEM_PROMPT_A = (
    "Tu es un agent d'analyse documentaire borné au topic data.gouv.fr "
    f"`{TOPIC_ID}`. Méthode : (1) pars du catalogue pour IDENTIFIER le jeu de "
    "données pertinent et récupère son id ; (2) liste SES ressources et choisis "
    "la ressource annuelle pertinente ; (3) interroge CETTE ressource métier pour "
    "obtenir les valeurs. RÈGLES IMPORTANTES : ne retourne PAS au catalogue une "
    "fois que tu as la ressource métier ; les valeurs de filtre sont TOUJOURS du "
    "texte entre guillemets (ex. \"2023\"), jamais des nombres ; dès qu'un "
    "résultat d'outil contient les lignes nécessaires pour répondre, ARRÊTE "
    "d'appeler des outils et écris directement la réponse finale en texte : le "
    "montant (avec son unité), suivi d'une phrase de méthode (jeu, ressource, "
    "colonnes, filtre) et des limites éventuelles. Calcule à partir des lignes "
    "déjà récupérées plutôt que de relancer des requêtes. Ne suppose jamais une "
    "colonne non observée. Si l'information n'est pas dans ce topic, dis-le."
)

# Prompt B : variante d'orchestration — multi-candidats (cible le jeu voisin).
SYSTEM_PROMPT_B = (
    "Tu es un agent d'analyse documentaire borné au topic data.gouv.fr "
    f"`{TOPIC_ID}`. Méthode : (1) pars du catalogue pour IDENTIFIER le ou les "
    "jeux de données pertinents et récupère leur id — pour cela, parcours TOUT le catalogue (toutes ses pages : page 1, page 2, etc., jusqu'à l'absence de page suivante) avant de choisir, car le jeu le plus pertinent peut se situer au-delà de la première page ; (2) liste LEURS ressources "
    "et évalue laquelle est la plus pertinente — compare les candidats, ne "
    "t'arrête pas au premier jeu trouvé ; (3) interroge la ou les ressources "
    "métier retenues pour obtenir les valeurs. RÈGLES IMPORTANTES : si une "
    "ressource candidate est inaccessible ou hors sujet, n'en conclus PAS que la "
    "donnée est absente du topic — évalue d'abord les autres jeux candidats ; ne "
    "retourne PAS au catalogue une fois que tu as la ressource métier ; les "
    "valeurs de filtre sont TOUJOURS du texte entre guillemets (ex. \"2023\"), "
    "jamais des nombres ; dès qu'un résultat d'outil contient les lignes "
    "nécessaires pour répondre, ARRÊTE d'appeler des outils et écris directement "
    "la réponse finale en texte : le montant (avec son unité), suivi d'une phrase "
    "de méthode (jeu, ressource, colonnes, filtre) et des limites éventuelles. "
    "Calcule à partir des lignes déjà récupérées plutôt que de relancer des "
    "requêtes. Ne suppose jamais une colonne non observée. Si l'information n'est "
    "pas dans ce topic, dis-le."
)

# v9 : clause à RETIRER quand l'outil d'agrégation est actif (sinon contradiction
# avec AGG_RULE : on ne veut plus que le modèle calcule de tête).
CALC_SELF_CLAUSE = ("Calcule à partir des lignes déjà récupérées plutôt que de "
                    "relancer des requêtes. ")

# v9 : règle d'agrégation, volontairement GÉNÉRALE (ne nomme ni colonne ni année,
# pour ne pas sur-ajuster le prompt à Q1).
AGG_RULE = (
    " RÈGLE DE CALCUL — pour toute somme, total, moyenne, minimum, maximum ou "
    "comptage sur une colonne, tu DOIS appeler l'outil `aggregate_resource_data` "
    "(resource_id, column, op) au lieu de calculer toi-même à partir des lignes. "
    "Ne donne jamais un total que tu as obtenu de tête. Tu peux d'abord "
    "prévisualiser la ressource avec query_resource_data pour confirmer le nom "
    "exact de la colonne, puis appeler aggregate_resource_data et reporter "
    "exactement le résultat renvoyé par l'outil."
)

PROMPT_VARIANT = os.getenv("PROMPT_VARIANT", "A").upper()
BASE_PROMPT = SYSTEM_PROMPT_B if PROMPT_VARIANT == "B" else SYSTEM_PROMPT_A

OUT_DIR = Path("outputs_agent_albert")


class RateLimited(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, detail=""):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


# --- MCP ---------------------------------------------------------------------
def mcp_rpc(method, params):
    r = requests.post(
        MCP_URL,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": "1", "method": method, "params": params},
        timeout=120,
    )
    r.raise_for_status()
    for line in r.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise RuntimeError(f"Réponse MCP sans ligne data : {r.text[:300]}")


def discover_openai_tools():
    payload = mcp_rpc("tools/list", {})
    tools = payload.get("result", {}).get("tools", [])
    out = []
    for t in tools:
        if t["name"] not in ALLOWED_TOOLS:
            continue
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
        }})
    return out


def call_mcp_tool(name, arguments):
    payload = mcp_rpc("tools/call", {"name": name, "arguments": arguments})
    content = payload.get("result", {}).get("content", [])
    text = content[0]["text"] if content else json.dumps(payload.get("result"))
    return text[:TOOL_RESULT_CHAR_CAP]


def validate_call(name, arguments):
    if name not in ALLOWED_TOOLS:
        return False, f"Outil non autorisé : {name}."
    if name in TOPIC_SCOPED_TOOLS and arguments.get("topic_id") != TOPIC_ID:
        return False, (f"Périmètre interdit : seul le topic {TOPIC_ID} est "
                       f"autorisé pour {name}.")
    # v9 : un resource_id doit être un UUID de RESSOURCE, pas un id de dataset.
    if name in RESOURCE_ID_TOOLS:
        rid = str(arguments.get("resource_id", ""))
        if DATASET_ID_RE.match(rid):
            return False, ("Identifiant de DATASET fourni à la place d'un "
                           "resource_id. Appelle d'abord list_dataset_resources "
                           "avec ce dataset_id, puis utilise l'UUID de la ressource.")
        if not RESOURCE_UUID_RE.match(rid):
            return False, ("resource_id invalide : un identifiant de ressource au "
                           "format UUID est attendu.")
    return True, ""


# --- Albert (avec reprise sur quotas) ----------------------------------------
def albert_chat(model, messages, tools):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model, "messages": messages, "tools": tools,
        "tool_choice": "auto", "temperature": TEMPERATURE,
        SEED_PARAM: SEED, MAXTOK_PARAM: MAX_TOKENS,
    }
    backoff = 5
    retried = False                       # v9 : trace une reprise absorbée
    for attempt in range(MAX_RETRIES + 1):
        t0 = time.time()
        try:
            r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                              json=payload, timeout=180)
        except requests.RequestException as e:
            if attempt >= MAX_RETRIES:
                raise ApiError(0, f"réseau: {e}")
            retried = True
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        latency = round(time.time() - t0, 2)

        if r.status_code in (429, 503, 500, 502, 504):
            if attempt >= MAX_RETRIES:
                raise RateLimited(f"{r.status_code} après {MAX_RETRIES} reprises")
            retried = True
            wait = r.headers.get("Retry-After")
            wait = int(wait) if (wait and wait.isdigit()) else backoff
            print(f"    [{r.status_code}] indisponible, pause {wait}s "
                  f"(reprise {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff = min(backoff * 2, 60)
            continue

        if not r.ok:
            detail = (r.text or "")[:300]
            if attempt < min(MAX_RETRIES, 3):
                retried = True
                print(f"    [{r.status_code}] requête refusée, pause {backoff}s "
                      f"(reprise {attempt + 1})")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            raise ApiError(r.status_code, detail)
        body = r.json()
        usage = body.get("usage") or {}
        impacts = usage.get("impacts") or {}
        time.sleep(RATE_DELAY)
        return {
            "choice": (body.get("choices") or [{}])[0], "latency": latency,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost", 0) or 0,
            "kWh": impacts.get("kWh", 0) or 0,
            "kgCO2eq": impacts.get("kgCO2eq", 0) or 0,
            "rate_retried": retried,
        }


# --- Boucle agentique --------------------------------------------------------
def run_chain(model, qid, question, run, tools, trace_fh, system_prompt):
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": question}]
    totals = {"kWh": 0.0, "kgCO2eq": 0.0, "cost": 0.0}
    tool_sequence, flags, final_answer = [], set(), None
    last_text = ""
    step = 0

    for step in range(1, MAX_STEPS + 1):
        try:
            res = albert_chat(model, messages, tools)
        except RateLimited:
            flags.add("rate_limited")
            break
        except ApiError as e:
            flags.add("api_error")
            trace_fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": model, "question_id": qid, "run": run, "step": step,
                "api_error": {"status": e.status, "detail": e.detail},
            }, ensure_ascii=False) + "\n")
            break

        if res.get("rate_retried"):
            flags.add("rate_retried")
        for k in totals:
            totals[k] += res[k]
        choice = res["choice"]
        msg = choice.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        if msg.get("content"):
            last_text = msg["content"]

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model, "question_id": qid, "run": run, "step": step,
            "finish_reason": choice.get("finish_reason"),
            "assistant_text": (msg.get("content") or "")[:500],
            "tool_calls": [], "latency": res["latency"],
            "kWh": res["kWh"], "kgCO2eq": res["kgCO2eq"], "cost": res["cost"],
        }

        if not tool_calls:
            final_answer = msg.get("content") or ""
            if step == 1:
                flags.add("no_tool_used")
            trace_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            break

        # v5 : assainissement de la copie ré-expédiée à Albert.
        sanitized_tool_calls = []
        for tc in tool_calls:
            tc_clean = dict(tc)
            fn = dict(tc.get("function") or {})
            try:
                json.loads(fn.get("arguments") or "")
            except Exception:
                fn["arguments"] = "{}"
            tc_clean["function"] = fn
            sanitized_tool_calls.append(tc_clean)
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": sanitized_tool_calls})

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                flags.add("param_error")
                rec["tool_calls"].append({"name": name,
                                          "args_raw": tc["function"]["arguments"],
                                          "result": "param_error"})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": "Erreur : arguments JSON invalides."})
                continue

            # Réparation d'un type d'argument fréquent (année en nombre).
            if name in ("query_resource_data", "aggregate_resource_data") and \
                    isinstance(args.get("filter_value"), (int, float, bool)):
                args["filter_value"] = str(args["filter_value"])
                flags.add("arg_coerced")

            ok, err = validate_call(name, args)
            if not ok:
                # v9 : la validation d'identifiant alimente un flag dédié.
                flags.add("id_rejected" if "resource_id" in err.lower()
                          or "dataset" in err.lower() else "tool_rejected")
                rec["tool_calls"].append({"name": name, "args": args,
                                          "result": f"rejected: {err}"})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": f"Appel refusé : {err}"})
                continue

            try:
                result = call_mcp_tool(name, args)
            except Exception as e:
                flags.add("tool_error")
                result = f"Erreur d'exécution outil : {e}"

            # v9 : signaux d'orchestration INFORMATIFS (factuels, non prescriptifs).
            if name in ("query_resource_data", "aggregate_resource_data"):
                low = result.lower()
                if "not found in the tabular api" in low or "resource not available" in low:
                    flags.add("tabular_unavailable")
                    result += ("\n\n[Info] Cette ressource existe sur data.gouv.fr "
                               "mais n'est pas servie par l'API tabulaire (non "
                               "tabularisée).")
                elif "does not exist" in low or "rejected the request" in low:
                    flags.add("schema_or_filter_error")
                    result += ("\n\n[Info] La colonne ou le filtre demandé n'existe "
                               "pas dans cette ressource. Prévisualise-la sans filtre "
                               "pour obtenir les noms exacts des colonnes, ou consulte "
                               "catalog_schema.")

            tool_sequence.append(name)
            rec["tool_calls"].append({"name": name, "args": args,
                                      "result_digest": result[:300]})
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": result})

        trace_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:
        flags.add("early_stop")

    return {
        "final_answer": (final_answer or last_text or "").strip(),
        "n_steps": step,
        "tool_sequence": "|".join(tool_sequence),
        "kWh": totals["kWh"], "kgCO2eq": totals["kgCO2eq"], "cost": totals["cost"],
        "flags": ",".join(sorted(flags)),
    }


def main():
    if not API_KEY:
        raise SystemExit(f"Clé API manquante pour le fournisseur '{PROVIDER}' "
                         "(secret Codespaces : ALBERT_API_KEY ou MISTRAL_API_KEY).")

    OUT_DIR.mkdir(exist_ok=True)
    print("Découverte des outils MCP...")
    tools = discover_openai_tools()
    tool_names = [t["function"]["name"] for t in tools]
    print("Outils exposés :", tool_names)
    if not tools:
        raise SystemExit(f"Aucun outil MCP exposé : serveur up sur {MCP_URL} ?")

    # v9 : la règle d'agrégation n'est ajoutée que si l'outil est réellement exposé.
    agg_available = "aggregate_resource_data" in tool_names
    use_agg = os.getenv("USE_AGG", "1") == "1" and agg_available
    system_prompt = BASE_PROMPT
    if use_agg:
        system_prompt = BASE_PROMPT.replace(CALC_SELF_CLAUSE, "") + AGG_RULE
    elif os.getenv("USE_AGG", "1") == "1" and not agg_available:
        print("  ⚠ aggregate_resource_data non exposé par le serveur MCP : "
              "règle d'agrégation NON activée. Enregistre l'outil dans le fork.")

    print(f"Fournisseur : {PROVIDER} | variante de prompt : {PROMPT_VARIANT} "
          f"| outil d'agrégation : {'oui' if use_agg else 'non'}")

    with open(QUESTIONS_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if QUESTION_IDS:
        rows = [r for r in rows if r["id"] in QUESTION_IDS]

    summary_path = OUT_DIR / "summary.csv"
    write_header = not summary_path.exists()
    fields = ["ts", "model", "question_id", "run", "n_steps", "tool_sequence",
              "kWh", "kgCO2eq", "cost", "flags", "final_answer"]

    with open(OUT_DIR / "trace.jsonl", "a", encoding="utf-8") as trace_fh, \
         open(summary_path, "a", newline="", encoding="utf-8") as sfh:
        writer = csv.DictWriter(sfh, fieldnames=fields)
        if write_header:
            writer.writeheader()

        for model in MODELS:
            for row in rows:
                qid, question = row["id"], row["question"]
                for run in range(1, NB_RUNS + 1):
                    print(f"\n=== {model} | {qid} | run {run} ===\n{question}")
                    try:
                        out = run_chain(model, qid, question, run, tools,
                                        trace_fh, system_prompt)
                    except Exception as e:
                        print(f"  !! exception non gérée, run marqué crashed : {e}")
                        out = {"final_answer": "", "n_steps": 0,
                               "tool_sequence": "", "kWh": 0.0, "kgCO2eq": 0.0,
                               "cost": 0.0, "flags": "crashed"}
                    print(f"  étapes={out['n_steps']} outils={out['tool_sequence']} "
                          f"flags={out['flags']} kWh={out['kWh']:.2e}")
                    print("  réponse:", out["final_answer"][:200])
                    writer.writerow({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "model": model, "question_id": qid, "run": run,
                        "n_steps": out["n_steps"],
                        "tool_sequence": out["tool_sequence"],
                        "kWh": out["kWh"], "kgCO2eq": out["kgCO2eq"],
                        "cost": out["cost"], "flags": out["flags"],
                        "final_answer": out["final_answer"][:1000],
                    })
                    trace_fh.flush(); sfh.flush()
                    time.sleep(CHAIN_DELAY)

    print(f"\nArtefacts : {OUT_DIR}/trace.jsonl  et  {OUT_DIR}/summary.csv")


if __name__ == "__main__":
    main()
