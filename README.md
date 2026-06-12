# Espace de confiance documentaire — topic data.gouv.fr + MCP

Dépôt d’artefacts accompagnant l’article : *Gouvernance et outils conversationnels génératifs : expérimentation d’un espace de confiance de la donnée*  (Eudes Peyre, ministère de la Culture).

## Objet

L’apport principal de ce travail est de documenter une chaîne de constitution d’un espace de confiance documentaire à partir de briques publiques existantes : un *topic* data.gouv.fr pour matérialiser le périmètre, un catalogue de contextualisation publié comme jeu de données ordinaire, et le protocole MCP pour rendre ce périmètre interrogeable par un outil conversationnel.

Ce dépôt ne documente donc pas d’abord un résultat de mesure, mais une chaîne expérimentale de constitution : comment passer d’un ensemble de jeux de données ouverts à un périmètre nommé, contextualisé, borné et interrogeable de bout en bout — sans base intermédiaire, index vectoriel ni couche applicative propriétaire.

Cette chaîne articule quatre étages :

- la définition du périmètre via un *topic*, édité en amont avec Grist ;
- un catalogue de contextualisation publié comme jeu de données, lié au *topic* par la convention `extras.mcp.catalog_dataset_id` ;
- l’interrogation des ressources via l’API tabulaire data.gouv.fr ;
- l’accès agentique via le serveur MCP data.gouv.fr, étendu pour exposer les *topics* et leurs éléments.

Les tests fonctionnels rassemblés ici valent à ce titre : ils éprouvent la chaîne de constitution et en illustrent les propriétés attendues : sélection du périmètre, traçabilité de bout en bout et refus explicite hors périmètre.

## Statut

Ces travaux sont exploratoires. Les tests présentés ne constituent ni une évaluation statistique, ni un benchmark stabilisé. Ils doivent être interprétés comme une première expérimentation, destinée à formuler des hypothèses, à identifier des points de vigilance et à documenter des premiers comportements observés.

Le protocole, les configurations et les métriques observées ont évolué au fil de l’expérimentation. De nombreux ajustements métrologiques restent nécessaires, notamment pour stabiliser la répétition des essais, comparer les configurations, automatiser l’évaluation et caractériser plus finement les modes d’échec.

## Dépôts liés

- [`culturegouv/donnees-ouvertes`](https://github.com/culturegouv/donnees-ouvertes) : script de profilage et fichiers CSV servant de source de vérité au catalogue de contextualisation.

- [`eudespeyre/datagouv-mcp`](https://github.com/eudespeyre/datagouv-mcp) : fork expérimental du serveur MCP data.gouv.fr utilisé dans cette étude.  
  Les modifications spécifiques introduites dans le cadre de cette expérimentation peuvent être consultées dans le [comparatif avec le dépôt amont](https://github.com/datagouv/datagouv-mcp/compare/main...eudespeyre:datagouv-mcp:feat/topics-elements). Elles correspondent notamment à l’extension du serveur MCP exposant les *topics* et leurs éléments (`list_topic_elements` et `get_topic_catalog`).

## Évaluation structurée

Ces travaux ont été menés avant la publication du dépôt officiel d’évaluation de data.gouv.fr. Ils en sont indépendants, tout en relevant de la même réflexion sur la gouvernance de l’orchestration agentique appliquée aux données publiques.

Pour une démarche d’évaluation plus structurée, voir le dépôt officiel :
- [`datagouv/datagouv-ai-evaluation`](https://github.com/datagouv/datagouv-ai-evaluation)
