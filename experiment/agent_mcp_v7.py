"""
agent_albert_mcp.py — Boucle agentique gouvernée : Albert (modèle) + fork MCP (outils).

v7 : prompt système sélectionnable via PROMPT_VARIANT — A (défaut, identique à
v6 : un seul jeu choisi tôt) ou B (considère plusieurs jeux candidats et évalue
le meilleur ; cible le piège du jeu voisin). Levier d'orchestration, à comparer
en A/B. Le fournisseur et la variante sont affichés au démarrage.
v6 : multi-fournisseur — LLM_PROVIDER=albert (défaut) ou mistral. Adapte URL,
clé (ALBERT_API_KEY / MISTRAL_API_KEY) et noms de paramètres (random_seed /
max_tokens côté Mistral). Les impacts énergie n'existent que côté Albert ; côté
Mistral ils valent 0 (utiliser EcoLogits hors ligne pour les estimer si besoin).
v5 : assainissement du tool-call renvoyé — si un modèle (p. ex. Ministral-8B)
émet des arguments JSON illisibles, on remplace ces arguments par "{}" dans la
copie ré-expédiée à Albert (sinon l'API rejette l'historique -> 400). La
détection param_error et la réponse d'erreur au modèle sont conservées : le
modèle est informé que son appel était invalide et poursuit une chaîne
observable au lieu de provoquer un rejet opaque.
v4 : robustesse erreurs API — un 4xx/5xx (p. ex. 400 d'Albert) ou une coupure
réseau ne fait PLUS planter la campagne ; le run est marqué `api_error` (ou
`crashed`) et la campagne continue. Étend aussi le backoff aux 500/502/504.
v3 : prompt ferme (stop&answer) + réparation d'arguments. v2 : gestion des quotas Albert (429/503) avec reprise + backoff, MAX_STEPS
paramétrable, temporisation entre appels, filtre QUESTION_IDS, et le drapeau
`rate_limited` n'interrompt plus la campagne.

C'est le coeur "versant orchestration" d'Article 2 : le modèle PILOTE les appels,
on les exécute contre le serveur MCP, on reboucle, en journalisant chaque étape.

Gouvernance (section 8.3 de l'article) :
- whitelist d'outils (ALLOWED_TOOLS) ;
- contrainte de périmètre (outils topic bornés à TOPIC_ID) ;
- journalisation inspectable par étape (trace.jsonl) ;
- paramètres figés (seed, temperature=0) -> rejouabilité documentée.

Pré-requis : serveur MCP (fork) lancé localement, p.ex. :
    cd external/datagouv-mcp && MCP_HOST=127.0.0.1 MCP_PORT=8007 ... uv run main.py
Clé Albert via l'environnement (secret Codespaces ALBERT_API_KEY).
Lancer depuis le dossier experiment/ (pour trouver questions.csv).
"""

import csv
import json
import os
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
# v6 : choix du fournisseur via LLM_PROVIDER (albert par défaut, ou mistral).
# Chaque fournisseur a son URL, sa clé et ses noms de paramètres (Mistral utilise
# random_seed / max_tokens là où une API compatible OpenAI utilise seed /
# max_completion_tokens).
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

MODELS = (os.getenv("ALBERT_MODELS").split(",") if os.getenv("ALBERT_MODELS")
          else ["openai/gpt-oss-120b",
                "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
                "mistralai/Ministral-3-8B-Instruct-2512"])

QUESTIONS_FILE = os.getenv("QUESTIONS_FILE", "questions.csv")
# Filtre optionnel : QUESTION_IDS="Q1,Q4" pour ne traiter que ces questions.
QUESTION_IDS = (set(os.getenv("QUESTION_IDS").split(","))
                if os.getenv("QUESTION_IDS") else None)

NB_RUNS = int(os.getenv("NB_RUNS", "3"))
SEED = int(os.getenv("SEED", "42"))
TEMPERATURE = 0
MAX_TOKENS = 1024
MAX_STEPS = int(os.getenv("MAX_STEPS", "12"))    # plafond d'étapes par chaîne
TOOL_RESULT_CHAR_CAP = 6000                       # borne le contexte réinjecté

# Pacing / quotas Albert
RATE_DELAY = float(os.getenv("RATE_DELAY", "1.5"))   # pause après chaque appel
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))     # reprises sur 429/503
CHAIN_DELAY = float(os.getenv("CHAIN_DELAY", "2.0")) # pause entre chaînes

# Gouvernance : seuls ces outils sont exposés au modèle.
ALLOWED_TOOLS = {
    "list_topic_elements", "get_topic_catalog", "query_resource_data",
    "list_dataset_resources", "get_dataset_info", "get_resource_info",
}
TOPIC_SCOPED_TOOLS = {"list_topic_elements", "get_topic_catalog"}

# Prompt A : version d'origine (un seul jeu, choisi tôt). Conservée intacte
# pour rester comparable aux campagnes précédentes.
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

# Prompt B : variante d'orchestration — considère PLUSIEURS jeux candidats et
# évalue le meilleur au lieu de s'enfermer sur le premier (cible le piège du jeu
# voisin). Volontairement GÉNÉRALE : ne nomme aucun jeu ni aucune année, pour ne
# pas sur-ajuster le prompt à une question précise.
SYSTEM_PROMPT_B = (
    "Tu es un agent d'analyse documentaire borné au topic data.gouv.fr "
    f"`{TOPIC_ID}`. Méthode : (1) pars du catalogue pour IDENTIFIER le ou les "
    "jeux de données pertinents et récupère leur id ; (2) liste LEURS ressources "
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

# Sélection : PROMPT_VARIANT=A (défaut, identique à v6) ou B (multi-candidats).
PROMPT_VARIANT = os.getenv("PROMPT_VARIANT", "A").upper()
SYSTEM_PROMPT = SYSTEM_PROMPT_B if PROMPT_VARIANT == "B" else SYSTEM_PROMPT_A

OUT_DIR = Path("outputs_agent_albert")


class RateLimited(Exception):
    pass


class ApiError(Exception):
    """Erreur API non liée au quota (4xx hors 429/503, réseau...). N'interrompt
    plus la campagne : le run est marqué `api_error` et on poursuit au suivant."""
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
    for attempt in range(MAX_RETRIES + 1):
        t0 = time.time()
        try:
            r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                              json=payload, timeout=180)
        except requests.RequestException as e:
            if attempt >= MAX_RETRIES:
                raise ApiError(0, f"réseau: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        latency = round(time.time() - t0, 2)

        if r.status_code in (429, 503, 500, 502, 504):
            if attempt >= MAX_RETRIES:
                raise RateLimited(f"{r.status_code} après {MAX_RETRIES} reprises")
            wait = r.headers.get("Retry-After")
            wait = int(wait) if (wait and wait.isdigit()) else backoff
            print(f"    [{r.status_code}] indisponible, pause {wait}s "
                  f"(reprise {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff = min(backoff * 2, 60)
            continue

        if not r.ok:
            # 4xx hors quota (p. ex. 400) : souvent transitoire sous charge.
            # On retente quelques fois, puis on signale SANS faire planter la campagne.
            detail = (r.text or "")[:300]
            if attempt < min(MAX_RETRIES, 3):
                print(f"    [{r.status_code}] requête refusée, pause {backoff}s "
                      f"(reprise {attempt + 1})")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            raise ApiError(r.status_code, detail)
        body = r.json()
        usage = body.get("usage") or {}
        impacts = usage.get("impacts") or {}
        time.sleep(RATE_DELAY)  # lissage
        return {
            "choice": (body.get("choices") or [{}])[0], "latency": latency,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost", 0) or 0,
            "kWh": impacts.get("kWh", 0) or 0,
            "kgCO2eq": impacts.get("kgCO2eq", 0) or 0,
        }


# --- Boucle agentique --------------------------------------------------------
def run_chain(model, qid, question, run, tools, trace_fh):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
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

        # v5 : on assainit la copie du tool-call AVANT de la renvoyer à Albert.
        # Si un modèle émet des arguments JSON illisibles, on les remplace par
        # "{}" dans l'historique ré-expédié (sinon l'API rejette -> 400). La
        # détection param_error et la réponse d'erreur, plus bas, restent
        # fondées sur les arguments d'origine : le modèle est bien informé.
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

            # Gouvernance par l'orchestration : on répare un type d'argument
            # erroné fréquent (année passée en nombre) au lieu de le laisser
            # casser la chaîne. La réparation est tracée (flag arg_coerced).
            if name == "query_resource_data" and isinstance(
                    args.get("filter_value"), (int, float, bool)):
                args["filter_value"] = str(args["filter_value"])
                flags.add("arg_coerced")

            ok, err = validate_call(name, args)
            if not ok:
                flags.add("tool_rejected")
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
    print("Outils exposés :", [t["function"]["name"] for t in tools])
    print(f"Fournisseur : {PROVIDER} | variante de prompt : {PROMPT_VARIANT}")
    if not tools:
        raise SystemExit(f"Aucun outil MCP exposé : serveur up sur {MCP_URL} ?")

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
                        out = run_chain(model, qid, question, run, tools, trace_fh)
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