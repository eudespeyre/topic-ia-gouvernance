# verif_1 — Dépenses culturelles des régions 2023 (Q1, Q2)

Note de vérification de la **vérité de référence** utilisée pour scorer les modèles.
Accompagne le fichier `reference_regions_2023_2026-06-15.json` (même dossier).

Instantané daté du **15/06/2026**. Vaut « à état constant des données sources » :
la ressource peut être rafraîchie ultérieurement par le producteur (DEPS / ministère
de la Culture).

## Questions vérifiées

- **Q1** : Quelle est la dépense culturelle totale des régions en 2023 ?
- **Q2** : Quelle région présente la dépense culturelle totale la plus élevée en 2023 ?

## Source

- Topic : `univers-culture-deps`
- Jeu de données : « Dépenses culturelles des régions » — id `64ae255bbc600d7a3468ee64`
- Ressource : `Dépenses_culturelles_des_régions_2023_total.csv` — id `4dfccc0d-962c-4800-8599-b47730823a33`
- 17 lignes (régions), toutes `annee = 2023`.
- Montants exprimés en **milliers d'euros (k€)**.

## Requête reproductible (sans authentification)

API tabulaire de data.gouv.fr :

```
GET https://tabular-api.data.gouv.fr/api/resources/4dfccc0d-962c-4800-8599-b47730823a33/data/?page_size=50
```

En ligne de commande (Linux / Codespace) :

```bash
curl -s "https://tabular-api.data.gouv.fr/api/resources/4dfccc0d-962c-4800-8599-b47730823a33/data/?page_size=50" | python -m json.tool
```

En PowerShell (Windows) :

```powershell
Invoke-RestMethod "https://tabular-api.data.gouv.fr/api/resources/4dfccc0d-962c-4800-8599-b47730823a33/data/?page_size=50" | ConvertTo-Json -Depth 6
```

## Calcul

- **Q1** = somme de la colonne `depenses_culturelles_totales_k_eur` sur les 17 régions.
- **Q2** = région ayant la valeur maximale de `depenses_culturelles_totales_k_eur`.

Reproduction du calcul à partir du JSON récupéré :

```python
import json
rows = json.load(open("reference_regions_2023_2026-06-15.json"))["rows"]
tot = sum(r["depenses_culturelles_totales_k_eur"] for r in rows)
mx  = max(rows, key=lambda r: r["depenses_culturelles_totales_k_eur"])
print(tot, "k€ =", round(tot/1000, 1), "M€")
print("Région max :", mx["libelle_region"], round(mx["depenses_culturelles_totales_k_eur"]/1000, 1), "M€")
```

## Résultats de référence

| Indicateur | Valeur |
|---|---|
| **Q1 — total régions 2023** | **781 237 k€ = 781,2 M€** |
| dont fonctionnement | 545 094 k€ (545,1 M€) |
| dont investissement | 236 141 k€ (236,1 M€) |
| **Q2 — région la plus élevée** | **Hauts-de-France — 107 097 k€ (107,1 M€)** |

Note : l'Île-de-France n'est **pas** la plus élevée (89,8 M€) — bonne question piège.

## Contrôle de cohérence

Fonctionnement + investissement = 545 094 + 236 141 = 781 235 k€, soit un **écart
résiduel de 2 k€** avec la colonne « total » (781 237 k€). Cet écart d'arrondi est sans
incidence sur l'ordre de grandeur — cohérent avec le même type d'écart documenté pour le
Pas-de-Calais dans Article 1.

## Pourquoi cette note

La vérité est **atteignable en une seule requête publique** et vérifiable par un humain.
C'est le point de référence permettant de mesurer si les modèles agentiques (gpt-oss,
Mistral, Qwen…) retrouvent ou non cette valeur via la boucle d'orchestration — et de
localiser leurs erreurs lorsqu'ils échouent.