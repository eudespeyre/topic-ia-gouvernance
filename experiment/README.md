# Expérimentations MCP topic-aware

Ce dossier contient les scripts d’expérimentation associés au dépôt `topic-ia-gouvernance`.

Les tests documentés ici visent à évaluer progressivement l’usage d’un serveur MCP custom exposant des outils liés aux topics data.gouv.fr, notamment pour le topic `univers-culture-deps`.

## 1. Lancer le serveur MCP local

Le serveur MCP custom n’est pas versionné dans ce dépôt. Il est cloné localement dans le dossier ignoré par Git :

```text
external/datagouv-mcp
```

Depuis la racine du dépôt `topic-ia-gouvernance`, lancer :

```bash
cd external/datagouv-mcp
export PATH="$HOME/.local/bin:$PATH"

MCP_HOST=127.0.0.1 \
MCP_PORT=8007 \
MCP_ENV=local \
DATAGOUV_API_ENV=prod \
LOG_LEVEL=DEBUG \
uv run main.py
```

Le serveur doit afficher une ligne du type :

```text
Uvicorn running on http://127.0.0.1:8007
```

Le terminal doit rester ouvert pendant l’exécution des tests.

## 2. Exécuter le test direct MCP

Dans un second terminal, depuis la racine du dépôt `topic-ia-gouvernance`, lancer :

```bash
python experiment/test_mcp_topic.py
```

Ce test appelle directement le serveur MCP local, sans passer par un modèle de langage.

Il vérifie successivement :

1. que le serveur MCP répond via `/health` ;
2. que l’outil `list_topic_elements` retourne les éléments du topic `univers-culture-deps` ;
3. que l’outil `get_topic_catalog` retourne le catalogue de contextualisation associé au topic.

## 3. Interpréter le résultat

Un résultat attendu ressemble à ceci :

```text
=== 1. Healthcheck du serveur MCP ===
status: ok

=== 2. Test du périmètre documentaire : list_topic_elements ===
topic_id: univers-culture-deps
total datasets: 37

=== 3. Test du catalogue de contextualisation : get_topic_catalog ===
status: ok
catalog_dataset_id: 6a11bacd77a67baa26b8cecd
catalog_version: 1.0
resources:
- catalog_datasets_univers-culture-deps
- catalog_schema_univers-culture-deps
```

Ce résultat confirme que le serveur MCP custom expose correctement :

* le périmètre documentaire du topic ;
* le catalogue de contextualisation ;
* les ressources CSV associées au catalogue.

## 4. Statut méthodologique du test

Ce test ne constitue pas une évaluation d’un modèle de langage.

Il s’agit d’un contrôle technique préalable destiné à vérifier que l’infrastructure MCP fonctionne avant toute expérimentation agentique.

Il permet de séparer deux niveaux :

```text
1. Validation MCP
   Le serveur MCP expose-t-il correctement les outils et artefacts attendus ?

2. Évaluation agentique / LLM
   Un modèle sait-il mobiliser ces outils pour produire une réponse correcte,
   traçable et bornée par le périmètre documentaire ?
```

Le test `test_mcp_topic.py` appartient donc au premier niveau. Il valide l’accès au périmètre documentaire et au catalogue, mais ne mesure pas encore la qualité d’une réponse produite par un modèle.

## 5. Étape suivante

Les étapes suivantes consistent à comparer différentes configurations de réponse :

```text
A — sans contexte documentaire topic/catalogue
D — avec contexte documentaire issu du topic et du catalogue
```

Ces tests pourront ensuite être rapprochés d’un cadre d’évaluation plus structuré, par exemple `datagouv-ai-evaluation`, afin d’observer :

* la qualité des réponses ;
* la sélection des ressources ;
* la trajectoire d’outils ;
* les refus hors périmètre ;
* les hallucinations éventuelles ;
* le coût et la stabilité des exécutions.
