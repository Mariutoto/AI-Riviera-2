# Motions de Blonay–Saint-Légier — législature 2021–2026

Pipeline reproductible couvrant les motions déposées depuis la création de la commune (fusion de Blonay et St-Légier-La Chiésaz au 1er juillet 2023) jusqu'à la fin de la législature cantonale 2021–2026.

Réutilise les fonctions génériques des scrapers des interpellations et des postulats (même site, même structure de fiche auto-suffisante, mêmes rôles de document possibles : texte, rapport de commission, décision du Conseil, formation de commission, réponse municipale). Un objet (2025-MO-02) porte le statut « Répondu » alors que son unique document est le texte original de la motion, sans réponse municipale jointe ; l'OCR de ce document confirme qu'il ne contient aucune mention de réponse — il s'agit très probablement d'une réponse orale donnée en séance et jamais documentée par écrit. Le champ `has_response` reflète fidèlement l'absence de document, indépendamment du statut affiché par le site.

Ordre d'exécution :

```powershell
python scrape-blonay-saint-legier/scrape_motions_2021_2026.py
python audit-blonay-saint-legier/motions-2021-2026/run_targeted_ocr.py
python audit-blonay-saint-legier/motions-2021-2026/build_audit.py
python audit-blonay-saint-legier/motions-2021-2026/generate_embedding_inputs.py
python audit-blonay-saint-legier/motions-2021-2026/generate_embeddings.py
python audit-blonay-saint-legier/motions-2021-2026/load_to_postgres.py
python audit-blonay-saint-legier/motions-2021-2026/validate_database.py
```

Corpus validé : 3 motions, 5 documents canoniques, 1 réponse municipale, 1 décision du Conseil, 3 pièces OCRisées et 8 chunks vectorisés avec `mistral-embed`.
