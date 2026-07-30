# Postulats de Vevey — législature 2021–2026

Pipeline reproductible pour les postulats déposés entre le 1er juillet 2021
et le 30 juin 2026.

Le catalogue officiel est parcouru intégralement, car son filtre « Postulat »
omet ou classe mal plusieurs documents. Les PDF autonomes sont dédupliqués
par contenu. Pour huit objets du début de législature, le texte officiel est
extrait uniquement des pages de l’annexe au procès-verbal signé.

Ordre d’exécution :

```powershell
python scrape-vevey/scrape_postulats_2021_2026.py
python audit-vevey/postulats-2021-2026/run_targeted_ocr.py
python audit-vevey/postulats-2021-2026/build_audit.py
python audit-vevey/postulats-2021-2026/generate_embedding_inputs.py
python audit-vevey/postulats-2021-2026/generate_embeddings.py
python audit-vevey/postulats-2021-2026/load_to_postgres.py
python audit-vevey/postulats-2021-2026/validate_database.py
```

Résultat validé au 30 juillet 2026 :

- 30 postulats ;
- 44 documents canoniques ;
- 5 réponses municipales reliées ;
- 9 rapports de prise en considération ;
- 3 PDF traités par OCR Mistral ;
- 185 chunks et 185 embeddings `mistral-embed` de dimension 1024.
