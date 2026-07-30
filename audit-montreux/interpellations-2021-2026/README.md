# Interpellations de Montreux — législature 2021–2026

Pipeline reproductible pour les interpellations déposées entre le 1er juillet
2021 et le 30 juin 2026.

Le scraper parcourt les 592 fiches historiques renvoyées par le catalogue
officiel, applique les bornes exactes de la législature, puis ouvre chaque
fiche retenue. Les libellés de pièces jointes étant parfois inexacts, les
réponses sont identifiées par le nom, le contenu et le hash du PDF. Les
réponses transcrites directement sur la fiche officielle deviennent des
documents canoniques reliés à l’interpellation.

Ordre d’exécution :

```powershell
python scrape-montreux/scrape_interpellations_2021_2026.py
python audit-montreux/interpellations-2021-2026/build_audit.py
python audit-montreux/interpellations-2021-2026/generate_embedding_inputs.py
python audit-montreux/interpellations-2021-2026/generate_embeddings.py
python audit-montreux/interpellations-2021-2026/load_to_postgres.py
python audit-montreux/interpellations-2021-2026/validate_database.py
```

Résultat du corpus :

- 155 interpellations ;
- 307 documents canoniques ;
- 152 interpellations avec une réponse vérifiable ;
- 66 réponses officielles en PDF et 86 réponses transcrites sur la fiche ;
- aucun OCR nécessaire ;
- 666 chunks.

La section `automatic_detection_notes` de `inventory.json` documente les
signaux utiles pour généraliser plus tard la détection, sans introduire ce
changement générique dans ce pilote.
