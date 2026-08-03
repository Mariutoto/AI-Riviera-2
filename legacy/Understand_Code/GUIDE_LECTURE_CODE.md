# Guide de lecture du code AI Riviera 2

Ce document est fait pour te remettre de l'ordre dans la tete quand le projet semble trop gros.

L'idee principale: **AI Riviera 2 est un chatbot documentaire**. Il prend des documents publics de La Tour-de-Peilz, les transforme en textes et donnees, les indexe dans une base, puis permet de poser des questions dessus avec des sources visibles.

## 1. La carte mentale du projet

```text
scrape-la-tour-de-peilz/
  Recupere les documents publics depuis le site officiel.
  Extrait les textes des PDF ou pages HTML.
  Cree des fichiers .txt et .json dans documents/.

documents/
  Contient les documents deja extraits:
  - .txt = texte lisible
  - .json = metadonnees du document

data/
  Contient des exports ou donnees structurees:
  seances, documents, objets politiques, statistiques.

app/
  Contient l'application:
  - interface Streamlit
  - ingestion
  - recherche
  - reponse LLM
  - stockage Postgres / OpenSearch / SQLite

db/
  Contient la structure SQL de la base.
```

Le coeur du projet est dans `app/`.

Les scripts dans `scrape-la-tour-de-peilz/` servent surtout a fabriquer les donnees de depart.

## 2. Le parcours complet d'une question

Quand un utilisateur pose une question dans l'interface:

```text
app/ui.py
  recoit la question

app/structured.py
  essaie de repondre directement avec les donnees structurees
  exemple: "combien de motions en 2024 ?"

app/answer.py
  peut reformuler la question pour la recherche

app/retrieval.py
  cherche les passages les plus pertinents

app/answer.py
  envoie les passages a Mistral ou OpenAI si une cle existe
  sinon affiche les meilleurs extraits

app/ui.py
  affiche la reponse et les sources
```

Version ultra simple:

```text
Question -> Recherche -> Sources -> Reponse -> Affichage
```

## 3. Les fichiers les plus importants

### `app/ui.py`

C'est l'interface visible. Elle utilise Streamlit.

Elle fait:

- afficher le titre, les onglets et la zone de chat;
- stocker l'historique de conversation;
- recevoir les questions;
- appeler la recherche et la generation de reponse;
- afficher les sources.

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `admin_tabs_enabled()` | Regarde si les onglets d'administration doivent etre affiches. |
| `current_filters()` | Retourne les filtres appliques a la recherche. Ici: ville = La Tour-de-Peilz. |
| `cacheable_filters()` | Transforme les filtres en format utilisable par le cache Streamlit. |
| `normalize_follow_up_text()` | Nettoie une question pour detecter si c'est une question de suivi. |
| `looks_like_follow_up()` | Devine si la question depend de la conversation precedente. Exemple: "et en 2025 ?" |
| `compact_history_for_question()` | Resume les derniers messages pour donner du contexte a une question de suivi. |
| `contextualize_question()` | Transforme une question courte en question complete avec l'historique. |
| `ensure_index_ready()` | Verifie si Postgres ou OpenSearch est pret. |
| `group_results_by_document()` | Regroupe plusieurs passages venant du meme document. |
| `source_link()` | Cree un lien Markdown vers la source PDF. |
| `link_source_mentions()` | Transforme "Source 1" en lien cliquable dans l'interface. |
| `render_sources()` | Affiche la liste des documents utilises et les passages retrouves. |
| `cached_answer_question()` | Fonction centrale cachee: structure, recherche, reranking, reponse. |
| `answer_question()` | Fonction principale appelee quand l'utilisateur pose une question. |
| `queue_question()` | Met une question en attente dans l'etat Streamlit. |

La fonction la plus importante ici est:

```python
answer_question(question, messages)
```

Elle:

1. verifie que l'index est pret;
2. ajoute le contexte si la question est une suite;
3. appelle `cached_answer_question`;
4. enregistre un diagnostic;
5. retourne la reponse et les sources.

### `app/answer.py`

C'est le fichier qui parle aux LLM: Mistral ou OpenAI.

Il ne cherche pas les documents lui-meme. Il prend des resultats deja trouves et construit une reponse.

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `get_secret()` | Lit une cle API depuis les variables d'environnement ou les secrets Streamlit. |
| `document_key()` | Trouve une cle stable pour identifier un document. |
| `group_results_by_document()` | Regroupe les passages par document. |
| `build_context()` | Fabrique le bloc de texte envoye au LLM avec les sources et passages. |
| `answer_with_openai()` | Demande a OpenAI de repondre a partir des extraits. |
| `answer_with_mistral()` | Demande a Mistral de repondre a partir des extraits. |
| `short_openai_completion()` | Petit appel OpenAI pour une tache courte: reformuler, router, reranker. |
| `short_mistral_completion()` | Meme idee pour Mistral. |
| `rewrite_query_with_openai()` | Reformule une question pour la recherche avec OpenAI. |
| `rewrite_query_with_mistral()` | Reformule une question pour la recherche avec Mistral. |
| `rewrite_query_with_llm()` | Choisit Mistral/OpenAI/auto pour reformuler. |
| `route_intent_with_openai()` | Demande a OpenAI si la question est `structured` ou `rag`. |
| `route_intent_with_mistral()` | Meme idee avec Mistral. |
| `route_intent_with_llm()` | Choisit le routeur actif. |
| `result_rerank_candidate()` | Prepare un extrait sous forme courte pour le reranking. |
| `parse_rerank_ids()` | Lit la liste JSON d'identifiants choisie par le LLM. |
| `rerank_results_with_llm()` | Demande au LLM de trier les meilleurs extraits. |
| `test_mistral_connection()` | Teste si Mistral repond correctement. |
| `answer_with_llm()` | Choisit OpenAI ou Mistral selon la configuration. |
| `llm_status()` | Donne l'etat du fournisseur LLM actif. |
| `answer_from_sources()` | Produit la reponse finale a partir des sources. |
| `_sources_section()` | Ajoute la liste des sources en bas de reponse. |

La logique importante:

```text
Si une cle Mistral/OpenAI existe:
  le LLM fait une synthese avec les sources.

Sinon:
  l'application affiche directement les meilleurs passages.
```

Le `SYSTEM_PROMPT` est tres important. Il dit au modele:

- repondre uniquement avec les extraits fournis;
- dire clairement quand les sources ne suffisent pas;
- citer les sources;
- ne pas confondre vote communal et votation populaire.

### `app/retrieval.py`

C'est le fichier de recherche documentaire.

Son role: prendre une question et retourner une liste de passages pertinents.

Fonctions:

| Fonction | Role simple |
|---|---|
| `tokenize()` | Decoupe une question en mots utiles et retire les mots vides. |
| `is_broad_legislature_query()` | Detecte les questions larges sur la legislature. |
| `is_council_vote_query()` | Detecte les questions sur les votes du Conseil communal. |
| `is_council_regulation_query()` | Detecte les questions sur le reglement ou les articles. |
| `load_chunks()` | Charge les anciens chunks JSON si on utilise le mode legacy. |
| `search()` | Fonction principale de recherche. |
| `merge_postgres_results()` | Fusionne resultats par mots-cles et resultats vectoriels. |

La fonction principale:

```python
search(query, limit=6, filters=None)
```

Elle fait plusieurs choses:

1. elle tokenise la question;
2. elle ajoute des filtres automatiques;
   - "motion" -> filtre motions;
   - "postulat" -> filtre postulats;
   - "article 96" -> filtre reglement;
   - "2025" -> filtre annee;
3. elle essaie la recherche hybride;
4. elle essaie Postgres mots-cles + vecteurs;
5. elle peut retomber sur SQLite ou JSON ancien mode.

Important: ce fichier est le carrefour entre la question humaine et les bases de recherche.

### `app/ingestion_pipeline.py`

C'est le pipeline moderne d'indexation.

Son role: lire les fichiers `.txt` et `.json` dans `documents/`, les decouper en morceaux, creer les embeddings, puis stocker le tout.

Fonctions:

| Fonction | Role simple |
|---|---|
| `chunk_text()` | Decoupe un long texte en morceaux de 1200 caracteres environ. |
| `chunks_for_document()` | Choisit comment decouper un document. Les articles de reglement restent entiers. |
| `load_metadata()` | Charge le `.json` a cote du `.txt` et enrichit les metadonnees. |
| `iter_text_files()` | Parcourt tous les fichiers `.txt` utiles. |
| `_document_payload()` | Prepare les donnees d'un document avant insertion en base. |
| `_searchable_text()` | Ajoute titre, auteurs, annee, statut, etc. devant chaque chunk pour ameliorer la recherche. |
| `ingest_documents()` | Fonction centrale qui indexe tous les documents. |
| `main()` | Permet de lancer le fichier avec `python -m app.ingestion_pipeline`. |

La fonction centrale:

```python
ingest_documents()
```

Elle:

1. verifie que le schema Postgres existe;
2. ouvre une connexion Postgres;
3. parcourt tous les `.txt`;
4. charge les metadonnees;
5. calcule un hash pour savoir si le document a change;
6. saute les documents inchanges;
7. decoupe le texte;
8. cree les embeddings;
9. insere les chunks dans Postgres;
10. indexe aussi dans OpenSearch si disponible;
11. journalise les statistiques.

### `app/postgres_store.py`

C'est la couche d'acces Postgres.

Elle cache les details SQL du reste de l'application.

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `DocumentRecord` | Structure Python qui represente un document avant insertion. |
| `_connect()` | Ouvre une connexion Postgres. |
| `ensure_schema()` | Cree/verifie les tables SQL. |
| `sha256_text()` | Calcule un hash stable d'un texte. |
| `canonical_source_url()` | Choisit l'URL source officielle d'un document. |
| `canonical_document_date()` | Trouve la meilleure date du document. |
| `canonical_fetch_date()` | Trouve la date de collecte. |
| `build_document_hash()` | Cree les empreintes du document et du contenu. |
| `upsert_document()` | Insere ou met a jour un document. |
| `get_document_by_source_url()` | Retrouve un document par URL. |
| `ready()` | Verifie si Postgres est utilisable. |
| `vector_search_ready()` | Verifie si la recherche vectorielle est disponible. |
| `_vector_literal()` | Convertit un vecteur Python en texte SQL. |
| `_normalize_search_rows()` | Transforme les lignes SQL en resultats utilisables par l'app. |
| `_filter_sql()` | Transforme les filtres Python en conditions SQL. |
| `search_chunks()` | Recherche par mots-cles dans Postgres. |
| `search_vector_chunks()` | Recherche vectorielle dans Postgres. |
| `insert_chunks()` | Insere les morceaux de texte indexes. |
| `delete_chunks_for_document()` | Supprime les anciens chunks d'un document. |
| `start_ingestion_run()` | Cree une ligne de debut d'ingestion. |
| `finish_ingestion_run()` | Marque la fin d'une ingestion. |
| `log_ingestion_event()` | Ajoute un evenement de log d'ingestion. |

### `app/structured.py`

C'est le gros fichier qui repond sans LLM quand la question est calculable.

Exemples:

- combien de postulats en 2024 ?
- qui a depose telle interpellation ?
- quels objets parlent de mobilite ?
- que dit l'article 96 ?
- quels montants apparaissent dans le budget ?

Il contient beaucoup de petites fonctions de detection:

```text
wants_...
```

Ces fonctions veulent dire:

```text
"Est-ce que la question demande ce type de reponse ?"
```

Exemples:

| Fonction | Role simple |
|---|---|
| `wants_financial_question()` | Detecte une question financiere. |
| `wants_regulation_question()` | Detecte une question sur le reglement. |
| `wants_person_deposits()` | Detecte une question sur les depots d'une personne. |
| `wants_count_by_party()` | Detecte une question de comptage par parti. |
| `wants_objects_by_status()` | Detecte une question sur les objets par statut. |
| `wants_themed_political_objects()` | Detecte une question sur un theme: mobilite, bus, ecole, etc. |

Puis il contient des fonctions:

```text
answer_...
```

Ces fonctions fabriquent vraiment la reponse.

Exemples:

| Fonction | Role simple |
|---|---|
| `answer_financial_db()` | Repond avec les donnees financieres en base. |
| `answer_regulation_db()` | Repond avec les articles du reglement. |
| `answer_object_author_db()` | Trouve l'auteur d'un objet politique. |
| `answer_person_deposits_db()` | Liste les objets deposes par une personne. |
| `answer_coauthors_db()` | Trouve les coauteurs. |
| `answer_count_by_party_db()` | Compte les objets par parti. |
| `answer_objects_by_status_db()` | Liste les objets selon leur statut. |
| `answer_themed_objects_db()` | Liste les objets lies a un theme. |
| `answer_objects_by_year_db()` | Liste les objets d'une annee. |
| `answer_most_deposits()` | Trouve qui a le plus depose. |
| `answer_deposits_by_year()` | Donne les depots par annee. |
| `answer_latest_deposits()` | Donne les derniers depots. |
| `answer_structured_question()` | Point d'entree final de ce fichier. |

La fonction la plus importante:

```python
answer_structured_question(question)
```

Elle essaie plusieurs strategies de reponse directe avant de passer au RAG.

### `app/metadata_enrichment.py`

Ce fichier enrichit les metadonnees des documents.

Il sert a transformer des JSON parfois incomplets en metadonnees plus utiles pour la recherche.

Exemples de choses ajoutees:

- categorie;
- type de contenu;
- legislature;
- auteurs;
- parti politique;
- dates;
- references legales;
- facettes de recherche;
- statut d'un objet politique.

Fonction centrale:

```python
enrich_metadata(metadata, text_path=None, content=None)
```

Elle prend des metadonnees brutes et renvoie des metadonnees plus propres.

### `app/embeddings.py`

Ce fichier cree les vecteurs de recherche.

Fonctions:

| Fonction | Role simple |
|---|---|
| `_normalize_text()` | Nettoie le texte avant embedding. |
| `_hash_embedding()` | Cree un faux embedding stable si OpenAI n'est pas disponible. |
| `_openai_client()` | Cree le client OpenAI. |
| `embed_texts()` | Cree les embeddings pour plusieurs textes. |
| `embed_text()` | Cree un embedding pour un seul texte. |
| `cosine_similarity()` | Mesure la proximite entre deux vecteurs. |

Important: si aucune cle OpenAI n'est disponible, le code peut quand meme creer des embeddings de fallback avec hash. Ce n'est pas aussi bon, mais ca permet de fonctionner.

### `app/opensearch_store.py`

C'est la couche OpenSearch.

Fonctions:

| Fonction | Role simple |
|---|---|
| `get_client()` | Cree le client OpenSearch. |
| `load_index_body()` | Lit la configuration de l'index. |
| `ensure_index()` | Cree l'index si besoin. |
| `ensure_runtime_mapping()` | Verifie certains champs calcules. |
| `ready()` | Dit si OpenSearch est disponible. |
| `delete_document()` | Supprime un document de l'index. |
| `index_chunks()` | Envoie les chunks dans OpenSearch. |
| `_filters_to_query()` | Convertit les filtres en requete OpenSearch. |
| `keyword_search()` | Recherche par mots-cles. |
| `knn_search()` | Recherche vectorielle. |
| `_normalize_hit()` | Convertit un resultat OpenSearch en format commun. |

### `app/hybrid_search.py`

Ce fichier fusionne plusieurs types de recherche.

Fonctions:

| Fonction | Role simple |
|---|---|
| `_merge_candidates()` | Fusionne resultats mots-cles et vectoriels. |
| `search_hybrid()` | Lance recherche hybride dans OpenSearch. |
| `grouped_sources()` | Regroupe les resultats par source. |

### `app/reranker.py`

Ce fichier retrie les chunks apres recherche.

Fonctions:

| Fonction | Role simple |
|---|---|
| `canonical_priority()` | Donne plus de poids aux sources canoniques. |
| `regulation_priority()` | Donne plus de poids aux articles pertinents du reglement. |
| `rerank_chunks()` | Retrie les passages trouves. |

### `app/year_metadata.py`

Ce fichier normalise les annees et dates.

Il evite une confusion importante:

```text
annee du fichier PDF
annee de publication
annee de l'objet politique
annee de la seance
```

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `infer_object_year()` | Trouve l'annee de l'objet politique. |
| `infer_document_year()` | Trouve l'annee du document. |
| `infer_publication_date()` | Trouve la date de publication. |
| `normalize_year_metadata()` | Met tout ca au propre dans les metadonnees. |

### `app/people_index.py`

Ce fichier construit un index des personnes.

Il sert a relier:

- auteurs;
- motionnaires;
- postulants;
- interpellateurs;
- partis;
- objets politiques.

Classe et fonctions importantes:

| Element | Role simple |
|---|---|
| `PersonAccumulator` | Accumule les infos trouvees sur une personne. |
| `normalize_name()` | Nettoie un nom. |
| `person_key()` | Cree une cle stable pour identifier une personne. |
| `alias_keys()` | Cree des variantes du nom. |
| `iter_author_entries()` | Lit les auteurs dans les metadonnees. |
| `build_people()` | Construit l'index en memoire. |
| `people_rows()` | Prepare les lignes SQL. |
| `upsert_people()` | Insere/met a jour les personnes en base. |
| `rebuild_people_index()` | Reconstruit tout l'index personnes. |

### `app/political_objects_index.py`

Ce fichier construit l'index des objets politiques.

Un objet politique peut etre:

- motion;
- postulat;
- interpellation;
- preavis;
- communication;
- rapport;
- reponse.

Classe et fonctions importantes:

| Element | Role simple |
|---|---|
| `PoliticalObjectAccumulator` | Accumule les infos sur un objet politique. |
| `object_type_from_metadata()` | Determine le type d'objet. |
| `should_use_metadata()` | Decide si un JSON doit entrer dans l'index. |
| `infer_object_id()` | Cree ou recupere l'identifiant d'objet politique. |
| `normalize_author()` | Nettoie les auteurs. |
| `merge_authors()` | Fusionne les auteurs venant de plusieurs documents. |
| `document_record()` | Cree un enregistrement de document lie. |
| `scheduled_session_records()` | Recupere les seances liees. |
| `merge_metadata()` | Fusionne les infos de plusieurs documents. |
| `build_political_objects()` | Construit les objets politiques. |
| `political_object_rows()` | Prepare les lignes SQL. |
| `upsert_political_objects()` | Insere/met a jour les objets en base. |
| `rebuild_political_objects_index()` | Reconstruit l'index complet. |

### `app/political_object_normalization.py`

Ce fichier met de l'ordre dans les statuts et dates des objets politiques.

Probleme qu'il resout:

```text
Un objet politique a souvent plusieurs dates:
- depot
- premiere seance
- commission
- rapport
- decision
- reponse
```

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `NormalizedPoliticalObject` | Structure finale normalisee. |
| `normalize_status()` | Traduit les statuts bruts en statut propre. |
| `scheduled_deposit_date()` | Trouve la date de depot planifiee. |
| `first_session_date()` | Trouve la premiere seance liee. |
| `latest_decision_session()` | Trouve la derniere decision. |
| `document_dates()` | Recupere les dates des documents. |
| `canonical_document_date()` | Choisit la date canonique du document. |
| `relation_date()` | Cherche une date selon un type de relation. |
| `commission_meeting_date()` | Trouve la date de commission. |
| `response_document_date()` | Trouve la date de reponse. |
| `normalize_dates()` | Choisit les dates finales importantes. |
| `normalize_row()` | Normalise un objet complet. |
| `upsert_normalization()` | Insere la normalisation en base. |
| `rebuild_political_object_normalization()` | Reconstruit toute la normalisation. |

### `app/political_object_people_index.py`

Ce fichier relie les objets politiques aux personnes.

Fonctions:

| Fonction | Role simple |
|---|---|
| `load_people_lookup()` | Charge les personnes connues. |
| `load_political_objects()` | Charge les objets politiques. |
| `resolve_person_id()` | Essaie de retrouver l'identifiant d'une personne. |
| `relation_rows()` | Prepare les relations objet-personne. |
| `upsert_relations()` | Insere les relations en base. |
| `rebuild_political_object_people_index()` | Reconstruit ces relations. |

### `app/political_object_documents_index.py`

Ce fichier relie les objets politiques aux documents.

Fonctions:

| Fonction | Role simple |
|---|---|
| `infer_relation_type()` | Determine le lien: document canonique, rapport, reponse, decision, etc. |
| `load_political_objects()` | Charge les objets politiques. |
| `relation_rows()` | Prepare les relations objet-document. |
| `upsert_relations()` | Insere les relations en base. |
| `rebuild_political_object_documents_index()` | Reconstruit ces relations. |

### `app/financial_extraction.py`

Ce fichier extrait des donnees financieres depuis les budgets.

Fonctions importantes:

| Fonction | Role simple |
|---|---|
| `normalize_text()` | Nettoie le texte financier. |
| `parse_number()` | Convertit un montant texte en nombre. |
| `find_budget_files()` | Trouve les fichiers budget. |
| `extract_summary_tables()` | Extrait les tableaux de synthese. |
| `collect_amounts()` | Recupere des montants proches d'une ligne. |
| `extract_account_lines()` | Extrait les lignes de comptes. |
| `upsert_financial_data()` | Insere les donnees financieres en base. |
| `ingest_financial_budget_data()` | Pipeline complet d'extraction financiere. |

### Petits fichiers utiles dans `app/`

| Fichier | Role simple |
|---|---|
| `config.py` | Lit la configuration: chemins, variables, backend. |
| `diagnostics.py` | Enregistre erreurs et interactions recentes. |
| `document_categories.py` | Normalise les categories de documents. |
| `eval_set.py` | Charge des questions de test. |
| `ingest.py` | Ancien/compatibilite: construit index JSON/SQLite et lance pipeline moderne. |
| `metadata_sync.py` | Synchronise les metadonnees JSON vers Postgres sans tout reindexer. |
| `sqlite_index.py` | Ancien moteur de recherche local SQLite. |
| `text_cleaning.py` | Corrige les accents/mojibake et nettoie le texte francais. |

## 4. Les scrapers

Le dossier `scrape-la-tour-de-peilz/` contient les scripts qui vont chercher les donnees.

Ils suivent presque tous le meme modele:

```text
fetch_text()
  telecharge une page HTML

collect_items()
  trouve les documents sur la page

download_and_extract()
  telecharge le PDF et extrait le texte

enrich_..._metadata()
  ajoute des metadonnees specialisees

main()
  lance tout le script
```

### Scrapers principaux

| Fichier | Role |
|---|---|
| `scrape_ordres_du_jour_2025_2026.py` | Recupere les pages de seances, ordres du jour et documents lies. |
| `scrape_proces_verbaux_2021_2026.py` | Recupere les proces-verbaux. |
| `structure_proces_verbaux.py` | Analyse les PV pour extraire presence, ordre du jour, votes, signatures, objets politiques. |
| `scrape_motions_2021_2026.py` | Recupere et enrichit les motions. |
| `scrape_postulats_2021_2026.py` | Recupere et enrichit les postulats. |
| `scrape_interpellations_2021_2026.py` | Recupere et enrichit les interpellations. |
| `scrape_preavis_municipaux_2021_2026.py` | Recupere les preavis municipaux et leurs rapports/decisions. |
| `scrape_budgets_2021_2026.py` | Recupere les budgets. |
| `scrape_rapport_comptes_2021_2024.py` | Recupere les rapports des comptes. |
| `scrape_rapport_gestion_2021_2024.py` | Recupere les rapports de gestion. |
| `scrape_infos_municipalite_2021_2026.py` | Recupere les infos de la Municipalite. |
| `scrape_informations_diverses_2021_2026.py` | Recupere les objets divers. |
| `scrape_conseil_communal_institutionnel.py` | Recupere la partie institutionnelle: membres, reglement, articles. |

### Fonctions typiques des scrapers

Tu verras souvent ces noms:

| Fonction | Signification |
|---|---|
| `fetch_text()` | Telecharge une page web. |
| `normalize_pdf_url()` | Transforme un lien PDF relatif en URL complete. |
| `safe_filename()` | Cree un nom de fichier propre et compatible Windows. |
| `extract_pdf_text()` | Extrait le texte d'un PDF. |
| `clean_html_text()` | Nettoie du HTML pour en faire du texte. |
| `clean_pdf_text()` | Nettoie le texte extrait d'un PDF. |
| `normalize()` | Met un texte dans une forme comparable. |
| `slugify()` | Cree un identifiant lisible depuis un titre. |
| `collect_items()` | Trouve les documents a traiter. |
| `download_and_extract()` | Telecharge, extrait et ecrit `.txt` + `.json`. |
| `main()` | Point d'entree du script. |

### Scrapers motions / postulats / interpellations

Ces trois fichiers se ressemblent beaucoup.

Ils essaient de trouver:

- auteurs;
- parti;
- titre officiel;
- statut;
- document canonique;
- rapport;
- decision;
- lien avec une seance.

Fonctions recurrentes:

| Fonction | Role |
|---|---|
| `parse_authors_from_listing()` | Recupere les auteurs depuis le titre sur la page web. |
| `clean_group_labels()` | Nettoie les mentions de groupes ou partis. |
| `extract_..._authors()` | Recupere les auteurs depuis le contenu du PDF. |
| `extract_..._object_title()` | Recupere le vrai titre de l'objet politique. |
| `infer_document_role()` | Determine si le PDF est l'objet original, un rapport, une reponse, une decision. |
| `extract_document_components()` | Liste les parties presentes dans le document. |
| `find_related_...()` | Cherche le document canonique correspondant. |
| `mark_agenda_linked_..._documents()` | Marque les documents qui sont lies a un ordre du jour. |

### `scrape_ordres_du_jour_2025_2026.py`

Ce fichier est particulierement important car il relie les seances aux documents.

Fonctions clefs:

| Fonction | Role |
|---|---|
| `TextAndLinksParser` | Parseur HTML qui garde le texte et les liens. |
| `collect_session_pages()` | Trouve les pages de seance. |
| `parse_agenda_items()` | Extrait les points de l'ordre du jour. |
| `create_local_document()` | Cree un document local depuis un lien PDF. |
| `load_local_document_index()` | Charge les documents deja presents. |
| `load_canonical_object_index()` | Charge les objets politiques canoniques. |
| `canonical_for_link()` | Relie un PDF d'ordre du jour a son document canonique. |
| `add_session_to_canonical()` | Ajoute l'information de seance dans le document canonique. |
| `parse_session()` | Analyse une seance complete. |
| `write_session_files()` | Ecrit les fichiers de session. |

### `structure_proces_verbaux.py`

Ce fichier lit les PV deja extraits et essaie d'en faire des donnees structurees.

Fonctions clefs:

| Fonction | Role |
|---|---|
| `extract_session_details()` | Trouve les infos generales de la seance. |
| `extract_signatures()` | Trouve les signatures. |
| `extract_agenda()` | Extrait l'ordre du jour du PV. |
| `extract_attendance()` | Extrait les presences/absences. |
| `vote_result_from_text()` | Interprete un resultat de vote. |
| `extract_previous_minutes()` | Trouve l'approbation des anciens PV. |
| `extract_decisions()` | Extrait les decisions. |
| `extract_political_objects()` | Extrait les objets politiques mentionnes. |
| `enrich_pv_metadata()` | Ajoute ces infos dans les metadonnees du PV. |
| `collect_pvs()` | Charge tous les PV. |
| `update_sessions_with_pvs()` | Relie les PV aux seances. |

## 5. Comment lire le projet sans se perdre

Je te conseille cet ordre:

1. Lire ce document en entier une fois.
2. Ouvrir `app/ui.py`.
3. Chercher `answer_question`.
4. Suivre l'appel vers `cached_answer_question`.
5. Aller dans `app/structured.py` pour comprendre les reponses directes.
6. Aller dans `app/retrieval.py` pour comprendre la recherche.
7. Aller dans `app/answer.py` pour comprendre la reponse finale.
8. Lire `app/ingestion_pipeline.py` pour comprendre comment les documents entrent dans la base.
9. Lire les scrapers seulement apres, car ils sont plus longs et repetitifs.

## 6. Les mots importants du projet

| Mot | Explication simple |
|---|---|
| RAG | Systeme qui cherche des sources puis demande au LLM de repondre avec ces sources. |
| Chunk | Petit morceau de document. |
| Embedding | Vecteur numerique qui represente le sens d'un texte. |
| Recherche vectorielle | Recherche par proximite de sens. |
| BM25 / mots-cles | Recherche classique par mots presents dans le texte. |
| Hybride | Recherche mots-cles + recherche vectorielle. |
| Metadonnees | Infos autour du document: titre, annee, auteur, type, URL. |
| Objet politique | Motion, postulat, interpellation, preavis, etc. |
| Source canonique | Document principal officiel d'un objet politique. |
| Document lie | Rapport, reponse ou decision liee au document principal. |
| Ingestion | Processus qui lit les documents et les indexe dans la base. |
| Reranking | Retri des resultats pour garder les plus utiles. |

## 7. Le resume en une phrase

AI Riviera 2 est une chaine complete:

```text
scraper -> nettoyer -> enrichir -> indexer -> rechercher -> repondre -> citer les sources
```

Si tu comprends cette phrase, tu as deja la colonne vertebrale du projet.

