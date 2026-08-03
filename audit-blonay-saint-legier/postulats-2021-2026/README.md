# Postulats de Blonay–Saint-Légier — législature 2021–2026

Pipeline reproductible couvrant les postulats déposés depuis la création de la commune (fusion de Blonay et St-Légier-La Chiésaz au 1er juillet 2023) jusqu'à la fin de la législature cantonale 2021–2026.

Réutilise les fonctions génériques du scraper des interpellations (`scrape-blonay-saint-legier/scrape_interpellations_2021_2026.py`) — même site, même structure de fiche auto-suffisante, aucun lien croisé entre objets. Deux différences propres aux postulats, découvertes en auditant les 7 objets un par un avant d'écrire le classement automatique :

- Le champ « Auteur » peut lister les membres d'une commission (Président·e, Rapporteur) une fois celle-ci formée, en plus ou à la place du ou de la postulant·e d'origine ; chaque rôle affiché par le site est conservé tel quel plutôt que supposé être « l'auteur ».
- Les documents attachés couvrent davantage de rôles que pour une interpellation : texte du postulat, rapport de commission, décision du Conseil (extrait de décision accepté/refusé), formation de la commission, et réponse municipale.

Ordre d'exécution :

```powershell
python scrape-blonay-saint-legier/scrape_postulats_2021_2026.py
python audit-blonay-saint-legier/postulats-2021-2026/run_targeted_ocr.py
python audit-blonay-saint-legier/postulats-2021-2026/build_audit.py
python audit-blonay-saint-legier/postulats-2021-2026/generate_embedding_inputs.py
python audit-blonay-saint-legier/postulats-2021-2026/generate_embeddings.py
python audit-blonay-saint-legier/postulats-2021-2026/load_to_postgres.py
python audit-blonay-saint-legier/postulats-2021-2026/validate_database.py
```

Corpus validé : 7 postulats, 16 documents canoniques, 5 réponses municipales, 1 rapport de commission, 2 décisions du Conseil, 1 avis de formation de commission, 9 pièces OCRisées et 70 chunks vectorisés avec `mistral-embed`.
