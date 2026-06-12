# AI Riviera 2

Prototype de chatbot pour interroger les documents communaux de La Tour-de-Peilz.

Cette version utilise Postgres comme stockage principal, puis indexe les chunks dans OpenSearch pour une recherche hybride BM25 + vectorielle. Le JSON/SQLite reste disponible uniquement comme ancien mode d'export ou de compatibilité, désactivé par défaut dans l'application.

## Lancer le chatbot

```powershell
python -m pip install -r requirements.txt
docker compose -f docker-compose.opensearch.yml up -d
python -m app.ingest
python -m streamlit run app/ui.py
```

L'application répond avec les passages les plus pertinents et leurs sources. Si une clé Mistral ou OpenAI est configurée, elle génère aussi une synthèse en français à partir des extraits retrouvés.

Pour relancer l'ingestion manuellement ou via un job planifié:

```powershell
python -m app.ingestion_pipeline --trigger-name scheduled
```

`python -m app.ingest` alimente Postgres et OpenSearch:

- Postgres: stocke les villes, documents, chunks, hashes, statuts d'ingestion et logs.
- Postgres: extrait aussi une couche financiere structuree pour les budgets communaux (`financial_summary_tables`, `financial_summary_rows`, `financial_account_lines`).
- OpenSearch: indexe les chunks pour la recherche hybride et les filtres.

Pour vérifier OpenSearch en local:

```powershell
curl http://localhost:9200
```

Pour reconstruire l'ancien index JSON/SQLite explicitement:

```powershell
python -m app.ingest --legacy-json
```

Pour relancer uniquement l'extraction financiere des budgets deja ingeres:

```powershell
python -m app.financial_extraction
```

## Options LLM

Sans clé API, l'app affiche les meilleurs extraits trouvés.

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

Avec `LLM_PROVIDER="auto"`, l'app essaie Mistral si `MISTRAL_API_KEY` existe, puis OpenAI si `OPENAI_API_KEY` existe.

## Données intégrées

Source principale: site officiel de La Tour-de-Peilz.

Couverture actuelle:

- Conseil communal, rubriques institutionnelles: admissions, bureau du conseil communal, compétences, liste des membres par parti, règlement du Conseil communal.
- Ordres du jour, législature 2021-2026: séances du 15 septembre 2021 au 6 mai 2026, soit 34 séances indexées.
- Procès-verbaux, législature 2021-2026: PV01 du 16 juin 2021 à PV34 du 25 mars 2026.
- Motions, postulats, interpellations et réponses: rubrique officielle `motions-postulats`, années 2021 à 2026, avec catégories de documents séparées (`motions`, `postulats`, `interpellations`).
- Objets divers: rubrique officielle affichée comme `Objets divers` sur le site, avec l'URL technique `informations-diverses.php`, années 2021 à 2026, soit 32 PDF indexés depuis la page dédiée.
- Préavis municipaux: rubrique officielle `preavis-municipaux`, années 2021 à 2026, soit 150 PDF indexés depuis la page dédiée.
- Rapports de gestion: exercices 2021 à 2024, avec rapport de la commission de gestion et réponse de la Municipalité, soit 4 gros rapports indexés depuis `rapport-comptes-budget.php`.
- Rapports des comptes: exercices 2021 à 2024, soit 4 gros rapports financiers indexés depuis `rapport-comptes-budget.php`.
- Budgets communaux: exercices 2021 à 2026, soit 6 gros rapports budgétaires indexés depuis `rapport-comptes-budget.php`.
- Infos de la Municipalité: décisions mensuelles publiées dans la rubrique officielle `infos-muni`, de septembre 2021 à mars 2026, soit 55 pages HTML indexées.
- Documents liés depuis les ordres du jour 2021-2026: préavis, rapports, communications municipales, motions, postulats, interpellations et réponses lorsque les PDF sont liés depuis les séances.
- Collecte directe 2025-2026: motions, postulats, interpellations, préavis municipaux, communications municipales, informations diverses, budgets, ordres du jour et procès-verbaux.

Limites volontaires:

- La séance du 24 juin 2026 n'est pas indexée, car elle est future au moment de la collecte du 29 mai 2026.
- La séance du 30 juin 2021 n'est pas incluse dans les ordres du jour 2021-2026, car elle apparaît dans l'onglet `Législature 2016-2021`.
- La page `motions-postulats.php` est structurée par années et non par onglets de législature; l'import prend les rubriques 2021 à 2026 puis sépare les documents en `motions`, `postulats` et `interpellations`. Certains PDF peuvent avoir une année de fichier différente de l'année affichée sur la page, par exemple lorsqu'une réponse est publiée l'année suivante.
- La page des `Objets divers` utilise l'URL technique `informations-diverses.php` et est aussi structurée par années; l'import prend les rubriques 2021 à 2026.
- Les PDF ne sont pas versionnés dans Git pour éviter un dépôt trop lourd. Le dépôt garde les textes extraits et les métadonnées JSON.

## Structure

```text
documents/la-tour-de-peilz/
  2021/
  2022/
  2023/
  2024/
  2025/
  2026/
  institutionnel/

data/sessions/la-tour-de-peilz/
data/proces-verbaux/la-tour-de-peilz/
data/institutionnel/la-tour-de-peilz/
data/infos-municipalite/la-tour-de-peilz/
data/structured/la-tour-de-peilz/
data/index/
```

Les statistiques d'indexation locales sont créées ici:

```text
data/index/stats.json
```

Le dossier `data/index/` est généré localement et n'est pas versionné dans Git. Les anciens fichiers `chunks.jsonl` et `ai_riviera.sqlite` ne sont recréés que si l'option `--legacy-json` est utilisée.

## Scrapers utiles

```powershell
python scrape-la-tour-de-peilz/scrape_ordres_du_jour_2025_2026.py
python scrape-la-tour-de-peilz/scrape_proces_verbaux_2021_2026.py
python scrape-la-tour-de-peilz/scrape_motions_postulats_2021_2026.py
python scrape-la-tour-de-peilz/scrape_informations_diverses_2021_2026.py
python scrape-la-tour-de-peilz/scrape_preavis_municipaux_2021_2026.py
python scrape-la-tour-de-peilz/scrape_rapport_gestion_2021_2024.py
python scrape-la-tour-de-peilz/scrape_rapport_comptes_2021_2024.py
python scrape-la-tour-de-peilz/scrape_budgets_2021_2026.py
python scrape-la-tour-de-peilz/scrape_infos_municipalite_2021_2026.py
python scrape-la-tour-de-peilz/scrape_conseil_communal_institutionnel.py
python scrape-la-tour-de-peilz/build_structured_data.py
python scrape-la-tour-de-peilz/clean_existing_text_data.py
python -m app.ingest
```

Le nom `scrape_ordres_du_jour_2025_2026.py` est historique; il couvre maintenant les ordres du jour de la législature 2021-2026.
Le nom `scrape_informations_diverses_2021_2026.py` suit le slug technique du site, mais correspond à la rubrique affichée `Objets divers`.

## Données structurées

Le dossier `data/structured/la-tour-de-peilz/` contient une vue plus exploitable par le chatbot:

- `sessions.json`: séances indexées, dates, lieux, sources et nombre de documents liés.
- `documents.json`: documents reliés aux séances.
- `political_objects.json`: motions, postulats, interpellations, préavis, communications, rapports et réponses reliés aux points d'ordre du jour.

Cette couche permet de répondre sans deviner à des questions calculables comme `combien de dépôts à la dernière séance ?`. Le RAG reste utilisé pour les questions documentaires et les synthèses.

## Prochaines étapes

- Consolider la couche Postgres: ajouter plus de vues métier par séance, par objet politique et par document, puis garder le JSON uniquement comme format d'import/export.
- Rendre le webscraping plus robuste: automatiser la mise à jour des nouvelles séances, détecter les nouveaux PDF, éviter les doublons, garder un journal des imports et vérifier quand une page officielle change de structure.
- Évaluer ensuite `pgvector` dans Postgres si on veut rapatrier la recherche vectorielle directement dans SQL.
- Garder des résultats rapides et fluides: pré-indexer les documents, mettre en cache les recherches fréquentes, séparer les métadonnées structurées des passages de texte, et limiter ce qui est envoyé au LLM à ce qui est vraiment pertinent.
- Ajouter éventuellement un login: accès public pour les documents déjà publics, puis espace privé pour les élus ou l'administration avec des droits plus fins, historique de questions, favoris, annotations et documents internes si la commune veut les ajouter.
- Préparer une version multi-communes Riviera: même structure de données, mais avec un champ `commune` clair pour comparer ou filtrer entre La Tour-de-Peilz, Vevey, Montreux, etc.
