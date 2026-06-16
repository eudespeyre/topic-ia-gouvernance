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
import unicodedata
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

# --- v11 : primitive d'orientation documentaire (outil LOCAL, cote harnais) ---
# Lit catalog_datasets + catalog_schema du topic (via l'API tabulaire publique) et
# renvoie une liste COURTE de ressources qualifiees. Scoring GENERIQUE : extrait
# granularite / annee / mesure de LA QUESTION (rien de code en dur), puis confronte
# aux proprietes du catalogue. Valide hors-ligne sur Q1 / communes / departements.
TABULAR_BASE = os.getenv("TABULAR_BASE",
                         "https://tabular-api.data.gouv.fr/api/resources")
# Ressources du dataset-catalogue du topic (extras.mcp.catalog_dataset_id =
# 6a11bacd77a67baa26b8cecd), resolues le 16/06/2026 ; surchargables par env.
CATALOG_DATASETS_RID = os.getenv("CATALOG_DATASETS_RID",
                                 "0f939cb9-5837-4010-a3ad-74126cbb8d0c")
CATALOG_SCHEMA_RID = os.getenv("CATALOG_SCHEMA_RID",
                               "8db8c14c-1c4f-4e25-b27b-10cadd06f9c3")

_GRAN = {
    "regions": ["region"],
    "communes": ["commune"],
    "departements": ["departement"],
    "intercommunalites": ["intercommunalite", "epci", "groupement"],
}


def _norm(x):
    x = unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", x)


def _parse_question(q):
    qn = _norm(q)
    gran = next((g for g, terms in _GRAN.items() if any(t in qn for t in terms)), None)
    years = re.findall(r"\b(?:19|20)\d{2}\b", q)
    year = years[0] if years else None
    measure = "total" if "total" in qn else None
    return gran, year, measure


def _fetch_tabular_all(rid, page_size=100, max_pages=20):
    rows = []
    page = 1
    while page <= max_pages:
        r = requests.get(f"{TABULAR_BASE}/{rid}/data/",
                         params={"page": page, "page_size": page_size}, timeout=60)
        r.raise_for_status()
        data = r.json().get("data") or []
        rows.extend(data)
        if len(data) < page_size:
            break
        page += 1
    return rows


def find_relevant_resources(question, max_results=5):
    """Outil LOCAL : qualification documentaire par proprietes (catalog_datasets +
    catalog_schema). Retourne un JSON de ressources candidates classees."""
    try:
        datasets = _fetch_tabular_all(CATALOG_DATASETS_RID)
        schema = _fetch_tabular_all(CATALOG_SCHEMA_RID)
    except Exception as e:
        return json.dumps({"error": f"catalogue indisponible : {e}",
                           "hint": "Reviens a l'exploration manuelle du catalogue."},
                          ensure_ascii=False)

    gran, year, measure = _parse_question(question)
    ds_by_id = {d.get("id.dataset"): d for d in datasets}
    res = {}
    for row in schema:
        rid = row.get("id.ressource")
        if not rid:
            continue
        r = res.setdefault(rid, {"id.dataset": row.get("id.dataset"),
                                 "title.ressource": row.get("title.ressource", ""),
                                 "columns": []})
        if row.get("column_name"):
            r["columns"].append(row["column_name"])

    out = []
    for rid, r in res.items():
        d = ds_by_id.get(r["id.dataset"], {})
        ds_title = d.get("title.dataset", "")
        ds_desc = d.get("description.dataset", "")
        text = _norm(ds_title + " " + r["title.ressource"])
        desc = _norm(ds_desc)
        score = 0
        reasons = []
        gran_match = bool(gran and any(t in text for t in _GRAN[gran]))
        if gran_match:
            score += 3
            reasons.append(f"granularite {gran} ok")
        other = [g for g in _GRAN if g != gran and any(t in text for t in _GRAN[g])]
        if other and not gran_match:
            score -= 4
            reasons.append(f"granularite {other[0]} (differente)")
        if year and (year in text or year in desc):
            score += 2
            reasons.append(f"annee {year} ok")
        elif year:
            oy = re.findall(r"(?:19|20)\d{2}", text + " " + desc)
            if oy and year not in oy:
                score -= 1
                reasons.append(f"autres annees {sorted(set(oy))}")
        matched = []
        if measure == "total":
            matched = [c for c in r["columns"] if "totales" in _norm(c)
                       and "pct" not in _norm(c) and "habitant" not in _norm(c)]
            if matched:
                score += 2
                reasons.append("colonne total ok")
        out.append({"dataset_id": r["id.dataset"], "dataset_title": ds_title,
                    "resource_id": rid, "resource_title": r["title.ressource"],
                    "matched_columns": matched[:4], "score": score,
                    "reason": "; ".join(reasons)})

    out.sort(key=lambda x: -x["score"])
    shortlist = []
    for i, o in enumerate(out[:max_results], 1):
        o["rank"] = i
        shortlist.append(o)
    return json.dumps({"question_criteria": {"granularite": gran, "annee": year,
                                             "mesure": measure},
                       "candidates": shortlist}, ensure_ascii=False)


FIND_RELEVANT_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "find_relevant_resources",
        "description": ("Qualification documentaire : lit le catalogue des jeux et le "
                        "schema des colonnes du topic et renvoie une liste courte de "
                        "ressources candidates classees (dataset, ressource, colonnes "
                        "correspondantes, justification) pour la question donnee. A "
                        "appeler EN PRIORITE pour choisir la bonne ressource sans "
                        "parcourir tous les jeux un par un."),
        "parameters": {"type": "object",
                       "properties": {"question": {"type": "string",
                                                   "description": "La question, telle quelle."}},
                       "required": ["question"]},
    },
}


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
    "find_relevant_resources",            # v11 : orientation documentaire (locale)
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
    "jeux de données pertinents et récupère leur id — pour cela, parcours TOUT le catalogue (toutes ses pages : page 1, page 2, etc., jusqu'à l'absence de page suivante) avant de choisir, car le jeu le plus pertinent peut se situer au-delà de la première page. Choisis en lisant les DESCRIPTIONS du catalogue (pas seulement les titres) : retiens LE SEUL jeu dont la description correspond à la fois à l'entité (régions, communes, départements, intercommunalités…), à la période (l'année demandée) et à la mesure (total, etc.) ; (2) liste les ressources de CE jeu retenu uniquement, sans énumérer les ressources des autres jeux, "
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

# Prompt C (v11) : orientation documentaire d'abord, puis calcul delegue.
SYSTEM_PROMPT_C = (
    "Tu es un agent d'analyse documentaire borne au topic data.gouv.fr "
    f"`{TOPIC_ID}`. METHODE OBLIGATOIRE : "
    "1) Identifie les criteres de la question : entite/granularite territoriale "
    "(regions, communes, departements, intercommunalites...), periode (annee), "
    "mesure (total, etc.) et operation (somme, max...). "
    "2) Appelle EN PRIORITE l'outil `find_relevant_resources` en lui passant la "
    "question telle quelle : il lit le catalogue des jeux et le schema des colonnes "
    "du topic et te renvoie une liste COURTE de ressources deja qualifiees (dataset, "
    "ressource, colonnes correspondantes, justification). "
    "3) NE PARCOURS PAS tous les jeux un par un : prends la ressource de rang 1 si "
    "elle correspond a la granularite, a la periode et a la mesure demandees ; en "
    "cas de doute, compare seulement les premiers candidats renvoyes. "
    "4) Pour obtenir la valeur, utilise `aggregate_resource_data` (resource_id, "
    "column, op) ; jamais un calcul de tete, jamais une estimation a partir d'un "
    "apercu. Tu peux d'abord previsualiser la ressource avec query_resource_data "
    "pour confirmer le nom exact de la colonne. "
    "REPONSE FINALE : donne le resultat avec son unite, puis une phrase de methode "
    "(jeu, ressource, colonne, operation, filtre eventuel) ; si des candidats "
    "proches ont ete ecartes, dis brievement pourquoi. Si l'information n'est pas "
    "dans ce topic, dis-le."
)

PROMPT_VARIANT = os.getenv("PROMPT_VARIANT", "A").upper()
BASE_PROMPT = {"A": SYSTEM_PROMPT_A, "B": SYSTEM_PROMPT_B, "C": SYSTEM_PROMPT_C}.get(PROMPT_VARIANT, SYSTEM_PROMPT_A)

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
    out.append(FIND_RELEVANT_TOOL_DEF)  # v11 : outil local expose au modele
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
                if name == "find_relevant_resources":
                    result = find_relevant_resources(args.get("question") or question)
                else:
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