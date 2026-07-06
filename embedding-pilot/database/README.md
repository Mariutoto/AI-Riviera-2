# Base PostgreSQL de test

La base s'appelle `ai_riviera_embedding_pilot`. Elle est entièrement
séparée de la base actuelle.

Le fichier `schema.sql` est le schéma réellement exécuté par Docker.
`schema_preview.sql` conserve la première proposition à titre de référence.

## Utilisation

```powershell
docker compose -f embedding-pilot/database/compose.yaml up -d --wait
python embedding-pilot/scripts/load_embeddings_to_docker.py
python embedding-pilot/scripts/search_docker.py "Votre question" --limit 5
```

Connexion locale : `127.0.0.1:55432`, base
`ai_riviera_embedding_pilot`, utilisateur `pilot`. Le mot de passe est réservé
à ce conteneur local et figure dans `compose.yaml`.

## Choix appliqués

- PostgreSQL ;
- extension `pgvector` ;
- modèle `mistral-embed` ;
- vecteurs de 1024 dimensions ;
- recherche par distance cosinus ;
- contenu et métadonnées conservés avec chaque chunk ;
- historique du modèle et de la recette utilisés.

Les données persistent dans le volume Docker
`ai_riviera_embedding_pilot_data` lorsque le conteneur est arrêté.
