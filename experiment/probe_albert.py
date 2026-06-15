"""
probe_albert.py — Sonde reproductible Albert API (tool-calling + empreinte).

Artefact de rejouabilité : à versionner dans le dépôt à côté des autres test_*.py.
La clé n'est PAS dans le code : elle est lue depuis un fichier .env (non versionné).

Ce que le script fige et documente (versant modèle, au sens de l'article) :
- liste de modèles épinglée (MODELS) ;
- temperature = 0 et SEED fixe ;
- capture, pour chaque appel : system_fingerprint, finish_reason, tool_call émis,
  validité JSON des arguments, tokens, coût, et impacts (kWh, kgCO2eq).

Limite assumée : le déterminisme reste « best-effort » côté serveur (le spec Albert
le dit lui-même). On obtient donc une rejouabilité DOCUMENTÉE, pas une reproductibilité
bit-exacte. Le system_fingerprint sert précisément à détecter un changement de backend.

Sorties horodatées dans ./outputs_albert/ :
- probe_<timestamp>.json : trace complète (config + résultats)
- probe.csv              : une ligne par (modèle), cumulée entre exécutions
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # lit le fichier .env du dossier courant
except ImportError:
    pass

# --- Configuration épinglée (versionnable) -----------------------------------
BASE_URL = os.getenv("ALBERT_BASE_URL", "https://albert.api.etalab.gouv.fr/v1").rstrip("/")
API_KEY = os.getenv("ALBERT_API_KEY")

SEED = int(os.getenv("SEED", "42"))
TEMPERATURE = 0
MAX_TOKENS = 256

# Modèles à sonder (épinglés). Surchargeable via ALBERT_MODELS="a,b,c".
DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "mistralai/Ministral-3-8B-Instruct-2512",
]
MODELS = (os.getenv("ALBERT_MODELS").split(",")
          if os.getenv("ALBERT_MODELS") else DEFAULT_MODELS)

QUESTION = ("Liste les jeux de donnees du topic univers-culture-deps. "
            "Utilise l'outil disponible.")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "list_topic_elements",
        "description": "Liste les jeux de donnees d'un topic data.gouv.fr.",
        "parameters": {
            "type": "object",
            "properties": {"topic_id": {"type": "string"}},
            "required": ["topic_id"],
        },
    },
}]

OUT_DIR = Path("outputs_albert")


# --- Appel -------------------------------------------------------------------
def call(model, tool_choice):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": QUESTION}],
        "tools": TOOLS,
        "tool_choice": tool_choice,
        "temperature": TEMPERATURE,
        "seed": SEED,
        "max_completion_tokens": MAX_TOKENS,
    }
    r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                      json=payload, timeout=120)
    if r.status_code != 200:
        return {"ok": False, "http": r.status_code, "detail": r.text[:200]}

    body = r.json()
    choice = (body.get("choices") or [{}])[0]
    tool_calls = (choice.get("message") or {}).get("tool_calls") or []

    args_valid, parsed = None, None
    if tool_calls:
        try:
            parsed = json.loads(tool_calls[0]["function"]["arguments"])
            args_valid = True
        except Exception:
            args_valid = False

    usage = body.get("usage") or {}
    impacts = usage.get("impacts") or {}
    return {
        "ok": True,
        "tool_choice": tool_choice,
        "finish_reason": choice.get("finish_reason"),
        "emitted_tool_call": bool(tool_calls),
        "tool_name": tool_calls[0]["function"]["name"] if tool_calls else None,
        "args_valid_json": args_valid,
        "parsed_args": parsed,
        "system_fingerprint": body.get("system_fingerprint"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost": usage.get("cost"),
        "kWh": impacts.get("kWh"),
        "kgCO2eq": impacts.get("kgCO2eq"),
    }


def probe(model):
    res = call(model, "auto")
    # si rien en "auto", on force pour distinguer "ne sait pas" de "ne veut pas"
    if res.get("ok") and not res.get("emitted_tool_call"):
        res["forced_required"] = call(model, "required")
    return res


def main():
    if not API_KEY:
        raise SystemExit("ALBERT_API_KEY manquante : crée un fichier .env "
                         "(voir .env.example) ou définis la variable.")

    OUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    run = {
        "timestamp_utc": ts,
        "base_url": BASE_URL,
        "seed": SEED,
        "temperature": TEMPERATURE,
        "question": QUESTION,
        "models": {},
    }

    for model in MODELS:
        print(f"\n=== {model} ===")
        res = probe(model)
        run["models"][model] = res
        print(json.dumps(res, ensure_ascii=False, indent=2))

    # Trace JSON complète (l'artefact de cette exécution)
    json_path = OUT_DIR / f"probe_{ts}.json"
    json_path.write_text(json.dumps(run, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    # CSV cumulatif (synthèse comparable entre exécutions)
    csv_path = OUT_DIR / "probe.csv"
    write_header = not csv_path.exists()
    fields = ["timestamp_utc", "model", "ok", "finish_reason", "emitted_tool_call",
              "args_valid_json", "system_fingerprint", "prompt_tokens",
              "completion_tokens", "cost", "kWh", "kgCO2eq", "seed", "temperature"]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        for model, res in run["models"].items():
            w.writerow({
                "timestamp_utc": ts, "model": model,
                "ok": res.get("ok"), "finish_reason": res.get("finish_reason"),
                "emitted_tool_call": res.get("emitted_tool_call"),
                "args_valid_json": res.get("args_valid_json"),
                "system_fingerprint": res.get("system_fingerprint"),
                "prompt_tokens": res.get("prompt_tokens"),
                "completion_tokens": res.get("completion_tokens"),
                "cost": res.get("cost"), "kWh": res.get("kWh"),
                "kgCO2eq": res.get("kgCO2eq"), "seed": SEED,
                "temperature": TEMPERATURE,
            })

    print(f"\nArtefacts : {json_path}  et  {csv_path}")


if __name__ == "__main__":
    main()