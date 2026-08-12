# AI Riviera 2

Chatbot open source pour interroger les documents publics des communes de la Riviera vaudoise.

AI Riviera est un projet à but non lucratif. L'objectif est d'aider les citoyennes, citoyens, élus et personnes intéressées à retrouver plus facilement des informations dans les documents publics communaux (motions, postulats, interpellations et leurs réponses), tout en gardant les sources visibles pour vérification.

Code source: https://github.com/Mariutoto/AI-Riviera-2

## Communes couvertes

| Commune | Documents indexés |
|---|---|
| La Tour-de-Peilz | Interpellations, postulats, motions, préavis municipaux, procès-verbaux, budgets, rapports de gestion, rapports des comptes, règlement du Conseil communal |
| Vevey | Interpellations, motions, postulats |
| Montreux | Interpellations, motions, postulats |
| Blonay–Saint-Légier | Interpellations, motions, postulats |
| Veytaux | Interpellations, motions |

Cette liste vit dans `municipal_pipeline/municipalities.py` (`search_enabled=True`) et pilote à la fois les filtres de l'interface (`app/ui.py`) et le routage de la recherche (`app/agent.py`). D'autres communes de la Riviera (Corsier-sur-Vevey, Corseaux, Chardonne, Jongny, Villeneuve, ASR) apparaissent déjà dans le menu comme « prochainement », grisées, en attendant leur pipeline.

## Architecture en bref

- Chaque commune a un ou plusieurs scrapers dans `scrape-<commune>/`, qui téléchargent les PDF et construisent un `inventory.json` par catégorie de document.
- L'OCR ciblé (`run_targeted_ocr.py`, API Mistral OCR) traite les PDF scannés qui n'ont pas de texte natif.
- `build_audit.py` consolide l'inventaire et les textes en un audit vérifiable par catégorie.
- `generate_embedding_inputs.py` puis `generate_embeddings.py` (moteur partagé dans `embedding-pilot/`) découpent les documents en passages et calculent les embeddings Mistral (`mistral-embed`, 1024 dimensions).
- `load_to_postgres.py` charge documents, passages et métadonnées dans Postgres/pgvector (`POSTGRES_V2_URL`), puis `validate_database.py` vérifie le résultat.
- L'application Streamlit (`app/ui.py`) interroge cette base via `app/pilot_v2_store.py`: `app/retrieval.py` détecte le type de document et l'année demandés dans la question, puis `app/agent.py` répartit la recherche entre communes et fusionne les résultats. `app/answer.py` génère une synthèse en français avec Mistral ou OpenAI si une clé API est configurée; sans clé, l'app affiche directement les meilleurs passages retrouvés avec leurs sources.

Chaque commune suit la même convention de dossiers, par exemple pour Montreux:

```text
scrape-montreux/scrape_interpellations_2021_2026.py
audit-montreux/interpellations-2021-2026/
  inventory.json
  run_targeted_ocr.py
  build_audit.py
  generate_embedding_inputs.py
  generate_embeddings.py
  load_to_postgres.py
  validate_database.py
  README.md
```

Les scripts d'une commune importent souvent ceux d'une autre commune déjà en place (via `importlib`) et surchargent quelques constantes (préfixe de document, catégorie, nom de la commune) plutôt que de dupliquer toute la logique: voir le `README.md` de chaque dossier `audit-<commune>/<categorie>/` pour le détail.

## Lancer le chatbot

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app/ui.py
```

Pour le déploiement de production, utiliser le lanceur qui prépare les
métadonnées SEO, le favicon, `robots.txt` et `sitemap.xml` avant la première
requête HTTP :

```powershell
python run_app.py --server.port $env:PORT --server.headless true
```

L'application lit directement depuis Postgres (`POSTGRES_V2_URL`); il n'y a pas d'étape d'ingestion à lancer pour simplement servir l'app, tant que la base est déjà peuplée (voir « Alimenter une commune » ci-dessous).

## Options LLM

Sans clé API, l'app affiche les meilleurs extraits trouvés, avec leurs sources.

Avec Mistral:

```powershell
$env:LLM_PROVIDER="mistral"
$env:MISTRAL_API_KEY="ta-cle"
$env:MISTRAL_MODEL="mistral-small-latest"
python -m streamlit run app/ui.py
```

Avec OpenAI:

```powershell
$env:LLM_PROVIDER="openai"
$env:OPENAI_API_KEY="ta-cle"
python -m streamlit run app/ui.py
```

Avec `LLM_PROVIDER="auto"` (par défaut), l'app essaie Mistral si `MISTRAL_API_KEY` existe, puis OpenAI si `OPENAI_API_KEY` existe.

## Formulaire de contact

L'onglet Contact envoie un message par e-mail via SMTP (`app/contact.py`). Secrets nécessaires: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, et optionnellement `CONTACT_RECIPIENT` (adresse de destination, sinon `yannboulben@gmail.com` par défaut).

## Déploiement Streamlit Cloud

En cloud, les secrets peuvent être fournis comme variables d'environnement ou dans les secrets Streamlit (`.streamlit/secrets.toml` en local, jamais commité). Les plus importants:

- `POSTGRES_V2_URL`: URL du Postgres/pgvector qui contient les documents indexés.
- `LLM_PROVIDER`, `MISTRAL_API_KEY` ou `OPENAI_API_KEY`: fournisseur LLM pour la synthèse.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `CONTACT_RECIPIENT`: formulaire de contact.
- `SUPPORT_HASH_SECRET`: secret aléatoire utilisé pour dédupliquer les soutiens sans conserver l'adresse e-mail en clair lorsque la personne ne demande pas les nouvelles du projet.
- `GA_MEASUREMENT_ID`, `GOOGLE_SITE_VERIFICATION`: voir « Analytics et Search Console » ci-dessous.

## Analytics et Search Console

Streamlit ne permet pas d'éditer le `<head>` de la page normalement (pas de fichier HTML statique dans ce projet). `app/analytics.py` contourne ça en patchant le `static/index.html` du paquet `streamlit` installé, au démarrage de l'app — cela fonctionne sur Streamlit Cloud car le paquet est réinstallé à chaque déploiement, avant que l'app ne tourne.

- `GA_MEASUREMENT_ID` (ex. `G-JT3WHS117T`): à définir dans les secrets Streamlit Cloud pour activer Google Analytics (gtag.js) en production. Ne pas la définir en local pour ne pas polluer les données avec du trafic de dev.
- `GOOGLE_SITE_VERIFICATION`: optionnel, contenu de la balise meta si on vérifie Search Console par « balise HTML ». Comme `airiviera.org` est un domaine dont on contrôle le DNS, la méthode « propriété de domaine » (enregistrement TXT chez l'hébergeur DNS) est plus simple et n'a pas besoin de ce secret ni de redéploiement.

## Alimenter une commune (pipeline complet)

Pour ajouter ou rafraîchir une commune, exécuter dans l'ordre, depuis le dossier de la commune concernée (exemple avec Montreux, catégorie interpellations):

```powershell
python scrape-montreux/scrape_interpellations_2021_2026.py
python audit-montreux/interpellations-2021-2026/run_targeted_ocr.py
python audit-montreux/interpellations-2021-2026/build_audit.py
python audit-montreux/interpellations-2021-2026/generate_embedding_inputs.py
python audit-montreux/interpellations-2021-2026/generate_embeddings.py
python audit-montreux/interpellations-2021-2026/load_to_postgres.py
python audit-montreux/interpellations-2021-2026/validate_database.py
```

Une fois la commune chargée, ajouter son entrée dans `municipal_pipeline/municipalities.py` (`search_enabled=True`, `search_scope`, `document_types`) pour qu'elle apparaisse dans l'interface.

Les scrapers historiques de La Tour-de-Peilz (préavis, procès-verbaux, budgets, rapports, ordres du jour...) restent dans `scrape-la-tour-de-peilz/`; leur convention de nommage est plus ancienne mais suit le même principe.

## Tests

```powershell
python -m pytest
```

## Dossier `legacy/`

Le dossier `legacy/` regroupe le code archivé qui n'est plus utilisé par l'app ni par les pipelines (anciens guides, expérimentations ponctuelles). Voir `legacy/README.md` pour le détail.

## Prochaines étapes

- Automatiser le rafraîchissement des communes déjà indexées (détecter les nouveaux documents, éviter de tout retraiter).
- Étendre la couverture aux communes encore grisées dans l'interface (Corsier-sur-Vevey, Corseaux, Chardonne, Jongny, Villeneuve, ASR).
- Séparer les fonctions pures de `app/ui.py` du rendu de page, pour simplifier les tests automatisés (voir le commentaire dans `tests/test_ui_document_tabs.py`).
