# Interpellations de Vevey — législature 2021–2026

Ce corpus couvre les interpellations déposées entre le 1er juillet 2021 et le
30 juin 2026. Il fusionne le filtre documentaire de Vevey, la recherche
textuelle du catalogue (qui retrouve des entrées omises par le filtre) et les
annexes officielles aux procès-verbaux signés.

Pipeline reproductible depuis la racine du dépôt :

```powershell
python scrape-vevey/scrape_interpellations_2021_2026.py
python audit-vevey/interpellations-2021-2026/run_targeted_ocr.py
python audit-vevey/interpellations-2021-2026/build_general_audit.py
python scrape-vevey/scrape_interpellation_responses_2021_2026.py
python scrape-vevey/link_interpellation_responses.py --interpellations audit-vevey/interpellations-2021-2026/inventory.json --annexes audit-vevey/interpellation-responses-2021-2026/inventory.json --interpellation-metadata-dir audit-vevey/interpellations-2021-2026/general-audit/metadata --output-dir audit-vevey/interpellation-response-links-2021-2026
python audit-vevey/build_combined_audit.py
python audit-vevey/generate_embedding_inputs.py
python audit-vevey/generate_embeddings.py
python audit-vevey/load_embeddings_to_aiven.py
python audit-vevey/interpellations-2021-2026/validate_database.py
```

Les PDF officiels et les vecteurs Mistral sont reproductibles et restent hors
Git. Les OCR Markdown, métadonnées, relations, textes nettoyés, chunks et audits
sont conservés pour permettre le contrôle du corpus.
