# Contexte documentaire — univers-culture-deps

Le serveur MCP est accessible à l’adresse :

${MCP_URL}

Le contexte documentaire disponible est borné par le topic data.gouv.fr :

- `univers-culture-deps`

Ce topic est associé à un catalogue contextuel exploitable via l’API tabular de data.gouv.fr.

Le catalogue contient deux ressources principales :

## catalog_datasets

Resource ID :

- `0f939cb9-5837-4010-a3ad-74126cbb8d0c`

Cette ressource liste les datasets du topic avec leurs métadonnées éditoriales.

Elle permet d’identifier le ou les datasets pertinents pour une question donnée.

---

## catalog_schema

Resource ID :

- `8db8c14c-1c4f-4e25-b27b-10cadd06f9c3`

Cette ressource décrit colonne par colonne les ressources tabulaires des datasets du topic.

Elle permet d’identifier :
- les noms exacts des colonnes ;
- les types détectés ;
- les ressources tabulaires associées.

La jointure entre `catalog_datasets` et `catalog_schema` se fait via :

- `id.dataset`

---

# Méthode attendue

Pour répondre à une question :

1. identifier d’abord le dataset pertinent dans `catalog_datasets` ;
2. récupérer son `id.dataset` ;
3. interroger `catalog_schema` avec cet `id.dataset` ;
4. identifier la ressource tabulaire et les colonnes utiles ;
5. interroger ensuite la ressource métier pertinente via l’API tabular ;
6. répondre uniquement à partir des résultats obtenus.

---

# Contraintes

- Ne jamais supposer l’existence de colonnes non observées.
- Ne jamais mobiliser de sources extérieures au topic.
- Si les données disponibles ne permettent pas de répondre, l’indiquer explicitement.