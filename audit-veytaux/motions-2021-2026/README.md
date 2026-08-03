# Motions de Veytaux — législature 2021–2026

Même source et même scraper que les interpellations de Veytaux (voir `audit-veytaux/interpellations-2021-2026/README.md` pour le détail de la page manuellement éditée et de la logique d'extraction). Ici, la réponse municipale à une motion prend la forme d'un préavis municipal complet plutôt que d'une simple lettre de réponse — les deux sont classés sous le même rôle `municipal_response`.

Ordre d'exécution :

```powershell
python scrape-veytaux/scrape_political_objects_2021_2026.py
python audit-veytaux/motions-2021-2026/run_targeted_ocr.py
python audit-veytaux/motions-2021-2026/build_audit.py
python audit-veytaux/motions-2021-2026/generate_embedding_inputs.py
python audit-veytaux/motions-2021-2026/generate_embeddings.py
python audit-veytaux/motions-2021-2026/load_to_postgres.py
python audit-veytaux/motions-2021-2026/validate_database.py
```

Corpus validé : 6 motions, 9 documents canoniques, 3 réponses municipales (préavis), 9 pièces OCRisées et 21 chunks vectorisés avec `mistral-embed`.
