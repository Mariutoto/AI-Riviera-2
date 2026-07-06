# Pilote embeddings V2

Ce dossier prépare le futur test d'embeddings sans encore appeler l'API Mistral,
créer de vecteurs ou modifier la base de données principale.

Commencer par ouvrir [`index.html`](index.html) dans un navigateur.

## Périmètre actuel

- motions validées ;
- interpellations validées ;
- postulats validés ;
- articles du règlement du Conseil communal.

Seuls les fichiers `chunks/*.json` des audits complets sont considérés comme
canoniques. Les pilotes, rapports HTML et artefacts OCR ne seront pas indexés.

## Ordre prévu

1. comprendre et valider les recettes dans `config/embedding_recipes.json` ;
2. contrôler les sources dans `config/sources.json` ;
3. générer une prévisualisation des 583 `embedding_input` sans API ;
4. valider le schéma PostgreSQL proposé ;
5. créer la base de test ;
6. appeler `mistral-embed` ;
7. charger les vecteurs et évaluer la recherche.

## État

`PRÉPARATION UNIQUEMENT` — aucun embedding n'a été généré.
