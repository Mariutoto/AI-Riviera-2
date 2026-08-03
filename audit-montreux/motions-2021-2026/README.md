# Motions de Montreux — législature 2021–2026

Pipeline reproductible couvrant les motions rattachées aux séances du Conseil communal entre le 1er juillet 2021 et le 30 juin 2026.

Contrairement aux postulats, aucune motion de ce corpus n'a de réponse municipale directement rattachée à sa propre fiche : la réponse, quand elle existe, prend la forme d'un rapport-préavis ou d'un rapport distinct de la Municipalité (parfois commun à plusieurs motions et postulats à la fois). Chaque lien motion ↔ réponse a été vérifié dans le texte intégral du rapport-préavis avant d'être ajouté à `RESPONSE_OBJECT_LINKS`. Le site re-liste par ailleurs une motion sous un nouvel identifiant lorsque le Conseil vote sur le rapport-préavis qui y répond (`DUPLICATE_OBJECT_IDS`) ; ce doublon est fusionné dans l'objet d'origine.

Ordre d'exécution :

```powershell
python scrape-montreux/scrape_motions_2021_2026.py
python audit-montreux/motions-2021-2026/run_targeted_ocr.py
python audit-montreux/motions-2021-2026/build_audit.py
python audit-montreux/motions-2021-2026/generate_embedding_inputs.py
python audit-montreux/motions-2021-2026/generate_embeddings.py
python audit-montreux/motions-2021-2026/load_to_postgres.py
python audit-montreux/motions-2021-2026/validate_database.py
```

Corpus validé : 17 motions, 52 documents canoniques, 5 objets avec réponse municipale vérifiable (dont un rapport-préavis répondant à deux motions), 8 pièces OCRisées et 213 chunks vectorisés avec `mistral-embed`.
