#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scorer déterministe des campagnes agentiques (Article 2).

Lit un fichier de trace JSONL (une ligne = une étape), regroupe par
(model, question_id, run), puis calcule pour chaque run des métriques chiffrées
contre une vérité de référence — au lieu de se fier aux flags, qui ne mesurent
pas l'exactitude.

Usage :
    python score_runs.py                                   # outputs_agent_albert/trace.jsonl
    python score_runs.py outputs_agent_albert_Q1_B_v8_SmallMinistral/trace.jsonl
    python score_runs.py chemin1/trace.jsonl chemin2/trace.jsonl

Sortie : un tableau lisible à l'écran + un fichier score.csv à côté du 1er trace.

Métriques par run :
  - resource_hit            : l'agent a interrogé la BONNE ressource (étage 2)
  - answer_value_eur        : montant détecté dans la réponse finale (en €), si présent
  - answer_exact            : ce montant == référence (à tolérance près) (étage 3 réussi)
  - calc_error_after_hit    : bonne ressource atteinte MAIS réponse chiffrée fausse
  - false_negative          : conclut à tort à l'absence de donnée
  - data_hallucination      : fabrique des lignes de données (bloc CSV inventé)
  - unsupported_action      : renonce et renvoie vers un téléchargement hors boucle
  - n_steps, kWh_total, finish_last
"""

import sys, os, json, re, csv
from collections import defaultdict

# ---------------------------------------------------------------------------
# Vérité de référence. Extensible aux autres questions (Q2…Q5).
#   type "number" : montant attendu en euros + tolérance relative
#   resource      : identifiant de la ressource tabulaire correcte
# ---------------------------------------------------------------------------
REFERENCES = {
    "Q1": {
        "type": "number",
        "value": 781_237_000,           # 781 237 k€
        "tol": 0.005,                   # ±0,5 % (couvre la forme arrondie « 781,2 M€ »)
        "resource": "4dfccc0d-962c-4800-8599-b47730823a33",
    },
    # "Q2": {"type": "string", "value": ["hauts-de-france"], "resource": "4dfccc0d-..."},
}

ABSENCE_PATTERNS = [
    "non disponible", "pas disponible", "n'est disponible", "ne sont pas disponible",
    "aucun jeu", "aucune donnée", "aucun autre jeu", "pas de donnée", "ne trouve pas",
    "ne contient de données", "ne semble pertinent", "n'a été trouvé",
]
DOWNLOAD_PATTERNS = [
    "télécharger", "telecharger", "download", "analyser localement",
    "url suivante", "lien vers le fichier",
]

# ---------------------------------------------------------------------------
# Détection d'un montant en euros dans un texte libre (formats FR)
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r"\d[\d \u00a0\u202f\.]*(?:,\d+)?")

def parse_amounts_eur(text):
    """Renvoie la liste des montants détectés, normalisés en euros."""
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0).strip(" \u00a0\u202f.")
        if not raw:
            continue
        # normalisation : on retire les séparateurs de milliers, virgule = décimale
        norm = raw.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
        norm = norm.replace(".", "").replace(",", ".")
        try:
            val = float(norm)
        except ValueError:
            continue
        # échelle éventuelle juste après le nombre
        tail = text[m.end():m.end() + 15].lstrip().lower()
        if tail.startswith("milliard") or tail.startswith("md"):
            val *= 1e9
        elif tail.startswith("million") or tail.startswith("m€") or tail.startswith("m eur") or tail.startswith("meur"):
            val *= 1e6
        elif tail.startswith("millier") or tail.startswith("k€") or tail.startswith("k eur") or tail.startswith("keur"):
            val *= 1e3
        out.append(val)
    return out

def is_exact(amounts, ref):
    tol = ref["tol"]
    return any(abs(v - ref["value"]) <= tol * ref["value"] for v in amounts)

def contains_any(text, patterns):
    low = text.lower()
    return any(p in low for p in patterns)

def has_fabricated_table(text):
    # bloc CSV/markdown inventé : fence ```csv, ou plusieurs lignes séparées par , ou ;
    low = text.lower()
    if "```" in text and ("csv" in low or ";" in text or "," in text):
        return True
    lines = [l for l in text.splitlines() if l.count(",") >= 2 or l.count(";") >= 2]
    return len(lines) >= 3

# ---------------------------------------------------------------------------
# Lecture des traces
# ---------------------------------------------------------------------------
def load_runs(paths):
    runs = defaultdict(list)  # (model, qid, run) -> [step dict, ...]
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = (rec.get("model"), rec.get("question_id"), rec.get("run"))
                runs[key].append(rec)
    return runs

def score_run(steps):
    steps = sorted(steps, key=lambda r: r.get("step", 0))
    model, qid = steps[0].get("model"), steps[0].get("question_id")
    ref = REFERENCES.get(qid)

    expected_rid = ref["resource"] if ref else None
    resource_hit = False
    for s in steps:
        for tc in (s.get("tool_calls") or []):
            if tc.get("name") == "query_resource_data":
                rid = (tc.get("args") or {}).get("resource_id")
                dig = (tc.get("result_digest") or "").lower()
                ok = ("not found" not in dig) and ("not available" not in dig)
                if expected_rid and rid == expected_rid and ok:
                    resource_hit = True

    final = steps[-1].get("assistant_text") or ""
    final_num = re.sub(r"https?://\S+", " ", final)   # ignore les nombres dans les URL (dates, ids)
    amounts = parse_amounts_eur(final_num)
    monetary = [v for v in amounts if v >= 100_000]   # écarte l'année 2023, etc.

    exact = bool(ref and ref["type"] == "number" and is_exact(amounts, ref))
    has_amount = len(monetary) > 0
    false_neg = (not exact) and contains_any(final, ABSENCE_PATTERNS)
    data_hall = has_fabricated_table(final)
    unsupported = (not has_amount) and contains_any(final, DOWNLOAD_PATTERNS)
    calc_error = resource_hit and has_amount and not exact

    return {
        "model": model, "question_id": qid, "run": steps[0].get("run"),
        "n_steps": max(s.get("step", 0) for s in steps),
        "resource_hit": resource_hit,
        "answer_value_eur": (max(monetary) if monetary else ""),
        "answer_exact": exact,
        "calc_error_after_hit": calc_error,
        "false_negative": false_neg,
        "data_hallucination": data_hall,
        "unsupported_action": unsupported,
        "kWh_total": round(sum(float(s.get("kWh") or 0) for s in steps), 6),
        "finish_last": steps[-1].get("finish_reason"),
    }

# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------
def short_model(m):
    return (m or "").split("/")[-1][:28]

def main():
    paths = sys.argv[1:] or ["outputs_agent_albert/trace.jsonl"]
    paths = [p for p in paths if os.path.exists(p)]
    if not paths:
        print("Aucun trace.jsonl trouvé. Donne le chemin en argument.")
        return

    runs = load_runs(paths)
    rows = [score_run(v) for v in runs.values()]
    rows.sort(key=lambda r: (r["model"] or "", r["question_id"] or "", r["run"] or 0))

    def b(x):
        return "✓" if x else "·"

    print(f"\n{'modèle':30} {'Q':3} {'run':>3} {'étp':>3} {'res':>3} {'exact':>5} "
          f"{'calc?':>5} {'absc':>4} {'hallu':>5} {'dl':>3} {'kWh':>9}  réponse")
    print("-" * 120)
    for r in rows:
        val = f"{int(r['answer_value_eur']):,}".replace(",", " ") if r["answer_value_eur"] != "" else "—"
        print(f"{short_model(r['model']):30} {r['question_id']:3} {r['run']:>3} {r['n_steps']:>3} "
              f"{b(r['resource_hit']):>3} {b(r['answer_exact']):>5} {b(r['calc_error_after_hit']):>5} "
              f"{b(r['false_negative']):>4} {b(r['data_hallucination']):>5} {b(r['unsupported_action']):>3} "
              f"{r['kWh_total']:.2e}  {val} €")

    # Synthèse par modèle
    print("\n--- Synthèse par modèle ---")
    agg = defaultdict(lambda: defaultdict(int))
    for r in rows:
        a = agg[r["model"]]
        a["n"] += 1
        for k in ("resource_hit", "answer_exact", "calc_error_after_hit",
                  "false_negative", "data_hallucination", "unsupported_action"):
            a[k] += int(r[k])
    for m, a in sorted(agg.items()):
        print(f"{short_model(m):30} runs={a['n']}  res={a['resource_hit']}/{a['n']}  "
              f"exact={a['answer_exact']}/{a['n']}  calc_err={a['calc_error_after_hit']}  "
              f"abs={a['false_negative']}  hallu={a['data_hallucination']}  dl={a['unsupported_action']}")

    # Écriture CSV
    out = os.path.join(os.path.dirname(paths[0]) or ".", "score.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nÉcrit : {out}")

if __name__ == "__main__":
    main()