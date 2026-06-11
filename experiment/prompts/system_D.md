# Configuration D — dispositif documentaire complet

## Description

La configuration D correspond à un assistant conversationnel connecté au MCP data.gouv.fr étendu, avec accès au topic `univers-culture-deps` et au catalogue de contextualisation associé.

Le modèle peut mobiliser un espace documentaire borné, composé des datasets sélectionnés dans le topic, des ressources associées et des informations de contextualisation produites par le pipeline `catalog-topic.py`.

## Objectif expérimental

Cette configuration vise à mesurer l’apport du dispositif complet d’espace de confiance documentaire par rapport au modèle nu.

Elle permet d’évaluer si la combinaison du périmètre documentaire, des métadonnées de datasets, des ressources tabulaires et du catalogue de contextualisation améliore la capacité du modèle à produire des réponses exactes, traçables et vérifiables.

## Hypothèse principale

Le dispositif complet réduit l’incertitude documentaire en bornant l’espace informationnel accessible et en contextualisant les ressources mobilisables.

Il devrait augmenter la probabilité de produire une réponse correcte, vérifiable au niveau des colonnes, tout en réduisant le volume de génération inutile.

## Différence avec la configuration A

Contrairement à la configuration A, le modèle ne répond pas uniquement depuis ses connaissances d’entraînement.

Il dispose d’un accès documentaire structuré au topic `univers-culture-deps`, aux datasets associés, aux ressources tabulaires et au catalogue de contextualisation.

## Dimensions évaluées

- exactitude de la source mobilisée ;
- exactitude des colonnes/propriétés mobilisées ;
- reproductibilité de la chaîne documentaire ;
- qualité des limites méthodologiques ;
- capacité de refus hors périmètre ;
- tokens d’entrée, tokens de sortie et durée de génération.