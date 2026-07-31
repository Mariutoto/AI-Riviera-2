# Postulats de Montreux — législature 2021–2026

Pipeline reproductible couvrant les postulats rattachés aux séances du Conseil communal entre le 1er juillet 2021 et le 30 juin 2026.

La source officielle sépare souvent le postulat, son rapport de commission et la réponse municipale publiée comme rapport-préavis. Le scraper relie ces objets, conserve la version finale des réponses republiées et fusionne les PDF identiques partagés par plusieurs postulats.

Ordre d’exécution :

```powershell
python scrape-montreux/scrape_postulats_2021_2026.py
python audit-montreux/postulats-2021-2026/run_targeted_ocr.py
python audit-montreux/postulats-2021-2026/build_audit.py
python audit-montreux/postulats-2021-2026/generate_embedding_inputs.py
python audit-montreux/postulats-2021-2026/generate_embeddings.py
python audit-montreux/postulats-2021-2026/load_to_postgres.py
python audit-montreux/postulats-2021-2026/validate_database.py
```

Corpus validé : 49 postulats, 144 documents canoniques, 7 objets avec réponse municipale vérifiable, 32 pièces OCRisées et 512 chunks vectorisés avec `mistral-embed`.
