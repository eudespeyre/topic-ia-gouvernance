Vérification Q1 — « Dépense culturelle totale des régions en 2023 »

Source de vérité


Jeu de données : Dépenses culturelles des régions — id 64ae255bbc600d7a3468ee64
Ressource : Dépenses_culturelles_des_régions_2023_total.csv — id 4dfccc0d-962c-4800-8599-b47730823a33
Contenu : 17 lignes, année 2023, une ligne par région (13 métropolitaines + Corse + 5 DOM : Guyane, Martinique, Guadeloupe, La Réunion ; pas de ligne « total » ni « ensemble »).
Séparateur ;, montants en k€.


Sommes réelles (recalculées ligne à ligne sur les 17 régions)

ColonneSomme (k€)En €depenses_culturelles_totales_k_eur781 237781 237 000depenses_culturelles_fonctionnement_k_eur545 094545 094 000depenses_culturelles_investissement_k_eur236 141236 141 000fonctionnement + investissement781 235—

Écart de 2 k€ entre totales (781 237) et fonctionnement+investissement (781 235) : arrondis dans la source. Sans incidence sur l'ordre de grandeur.

Réponse de référence Q1 = 781 237 000 € (781,2 M€).

Repères utiles : région la plus dépensière = Hauts-de-France, 107 097 k€ ; Île-de-France = 89 844 k€ (n'est donc pas le maximum).

Réponse produite par Ministral-3-8B (B/v7, 3 runs sur 3, déterministe)


« 1 022 300 000 € … calculé en sommant la colonne depenses_culturelles_totales_k_eur … »



Démonstration : le nombre ne correspond à aucune opération sur la donnée

La colonne qu'il dit sommer vaut 781 237, pas 1 022 300. Le nombre annoncé ne correspond à aucune opération honnête sur les colonnes du fichier :

Opération testéeRésultat (k€)= 1 022 300 ?somme totales_k_eur (méthode annoncée)781 237nontotales + investissement1 017 378nontotales + fonctionnement1 326 331nonfonctionnement + investissement781 235nontoute colonne seule—non

Écart résiduel 1 022 300 − 781 237 = 241 063 k€ : ne correspond à aucune composante du fichier. L'opération la plus proche (totales + investissement = 1 017 378, déjà aberrante car double-compte l'investissement) reste à 4 922 k€ du nombre annoncé. Le montant se termine par 00, signature d'une estimation et non d'une somme de valeurs hétérogènes.

Conclusion

Ministral atteint la bonne ressource (200 OK sur 4dfccc0d, les 17 lignes 2023 lui sont retournées en entier) et énonce la bonne méthode, mais n'exécute pas la somme qu'il décrit : le nombre produit ne dérive d'aucune opération sur les données récupérées. Il s'agit d'une hallucination d'agrégation — le calcul final n'est ancré dans aucun traitement déterministe.

Ce mode d'échec se situe à l'étage 3 (exactitude du calcul), distinct de l'étage 1 (périmètre) et de l'étage 2 (accès à la bonne ressource), tous deux franchis ici. Il montre que la chaîne agentique gouverne la récupération (corpus, sélection d'outils) mais laisse le calcul hors gouvernance, exécuté par le modèle. Un dispositif d'orchestration gouverné doit donc prolonger le grounding documentaire par un grounding computationnel : primitive d'agrégation déterministe (somme/agrégation côté API tabulaire), ou restitution forcée des valeurs ligne à ligne rendant l'arithmétique auditable.