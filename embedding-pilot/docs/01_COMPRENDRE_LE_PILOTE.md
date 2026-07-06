# Comprendre le pilote

## Ce qui existe déjà

Les audits ont produit des chunks avec leur contenu, leur rôle et leurs
métadonnées. Ces fichiers sont lisibles et ne contiennent pas encore les
vecteurs définitifs.

## Ce que nous allons ajouter

Pour chaque chunk :

1. construire un texte `embedding_input` ;
2. envoyer ce texte au modèle `mistral-embed` ;
3. recevoir 1024 nombres ;
4. stocker ces nombres dans PostgreSQL/pgvector.

## Ce qui n'est pas fait maintenant

- aucune clé API utilisée ;
- aucun texte envoyé à Mistral ;
- aucun coût engagé ;
- aucune base créée ;
- aucune donnée actuelle remplacée.
