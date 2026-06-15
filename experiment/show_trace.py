"""
show_trace.py — Lecteur lisible de outputs_agent_albert/trace.jsonl.

Affiche, étape par étape, ce que le modèle a dit et quels outils il a appelés.
Usage :
    python show_trace.py        # toutes les questions
    python show_trace.py Q1      # seulement Q1
"""

import json
import sys
from pathlib import Path

path = Path("outputs_agent_albert/trace.jsonl")
qfilter = sys.argv[1] if len(sys.argv) > 1 else None

if not path.exists():
    raise SystemExit(f"Fichier introuvable : {path} (lance d'abord agent_albert_mcp.py)")

for line in path.read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if qfilter and r.get("question_id") != qfilter:
        continue
    print(f"\n=== {r['model']} | {r['question_id']} run{r['run']} | "
          f"étape {r['step']} | finish={r['finish_reason']} ===")
    txt = (r.get("assistant_text") or "").strip()
    print("texte :", txt if txt else "(aucun texte — le modèle a appelé un outil)")
    for c in r.get("tool_calls", []):
        print("  → outil :", c.get("name"),
              "| args :", c.get("args", c.get("args_raw")))
        dig = c.get("result_digest")
        if dig:
            print("    résultat (extrait) :", dig[:200].replace("\n", " "))