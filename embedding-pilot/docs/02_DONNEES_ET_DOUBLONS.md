# Données utilisées et doublons évités

Un même document existe sous plusieurs formes dans `audit-general` : PDF,
texte natif, texte nettoyé, métadonnées, chunks et pages HTML.

Le pilote lit uniquement les JSON présents dans les dossiers `chunks` des
audits complets. Cela évite d'indexer plusieurs fois le même contenu.

Avant tout appel API, un futur contrôle vérifiera :

- unicité de `chunk_id` ;
- unicité de `chunk_hash` lorsque disponible ;
- présence du contenu ;
- présence des métadonnées exigées par la recette ;
- longueur compatible avec le modèle ;
- absence de numéros de page isolés.
