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

`PILOTE LOCAL ACTIF` — 583 embeddings Mistral sont chargés dans la base Docker.
Les vecteurs et la clé API restent locaux et ne sont pas versionnés.

## Lancer le chatbot local V2

```powershell
python embedding-pilot/scripts/run_chatbot_v2.py
```

Le lanceur démarre la base Docker si nécessaire puis ouvre le chatbot existant
en mode V2 sur `http://localhost:8502`. La branche `main` et la base Aiven V1
ne sont pas modifiées.

## Résumés persistants des documents

Les descriptions affichées dans la liste des sources peuvent être générées une
fois puis stockées dans `documents`. Cette opération n'extrait pas les PDF à
nouveau et ne recalcule aucun embedding.

Tester d'abord sur dix documents sans écrire:

```powershell
python embedding-pilot/scripts/backfill_document_summaries.py --limit 10 --dry-run
```

Enregistrer dix résumés:

```powershell
python embedding-pilot/scripts/backfill_document_summaries.py --limit 10
```

Puis remplir tous les documents restants:

```powershell
python embedding-pilot/scripts/backfill_document_summaries.py
```

Le script ajoute lui-même les colonnes manquantes et ignore les documents déjà
résumés. Il peut donc être interrompu et relancé. `POSTGRES_V2_URL` et
`MISTRAL_API_KEY` doivent être configurés dans l'environnement,
`embedding-pilot/.env` ou `.streamlit/secrets.toml`.
