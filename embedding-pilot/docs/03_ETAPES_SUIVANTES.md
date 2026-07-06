# Étapes suivantes

## Étape 2 — prévisualisation

Générer un fichier JSONL et une page HTML montrant exactement le texte qui
serait envoyé à Mistral pour chacun des 583 chunks retenus.

## Étape 3 — base locale

Créer PostgreSQL/pgvector dans un conteneur séparé et appliquer le schéma après
validation.

## Étape 4 — embeddings

Configurer `MISTRAL_API_KEY`, lancer les appels par lots et enregistrer le nom
du modèle, la dimension, la recette et le coût estimé.

## Étape 5 — évaluation

Préparer des questions françaises et vérifier que les bons documents,
composants et articles apparaissent dans les premiers résultats.
