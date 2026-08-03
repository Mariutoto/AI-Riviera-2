# Interpellations de Veytaux — législature 2021–2026

Contrairement aux autres communes, le site de Veytaux ne repose sur aucune base de données : `/motions-postulats-interpellations` est une unique page éditée à la main dans un éditeur de texte riche. Aucun identifiant d'objet, aucun statut, aucun champ auteur structuré n'est publié — seuls le titre du lien, un paragraphe de regroupement par date de dépôt (« Interpellation(s) du/le [date] ») et l'ordre des documents dans la liste sont exploitables de façon fiable.

Le scraper (`scrape-veytaux/scrape_political_objects_2021_2026.py`, partagé avec les motions) s'appuie sur cette seule régularité structurelle : chaque paragraphe de date groupe une liste `<ul>` d'objets, et dans chaque `<li>`, le premier lien est le texte original, les liens suivants (imbriqués ou non) sont classés par mot-clé (« réponse »/« préavis » → réponse municipale, « résolution » → résolution). Aucun auteur n'est extrait automatiquement — le nom, quand il est mentionné, reste dans le titre du document et dans le texte lui-même, où l'assistant peut le retrouver au moment de répondre.

Toutes les pièces jointes de cette commune sont des scans (aucun texte natif détecté sur les 44 documents des deux catégories) : passage systématique par Mistral OCR.

Ordre d'exécution (fait aussi les motions en une seule passe) :

```powershell
python scrape-veytaux/scrape_political_objects_2021_2026.py
python audit-veytaux/interpellations-2021-2026/run_targeted_ocr.py
python audit-veytaux/interpellations-2021-2026/build_audit.py
python audit-veytaux/interpellations-2021-2026/generate_embedding_inputs.py
python audit-veytaux/interpellations-2021-2026/generate_embeddings.py
python audit-veytaux/interpellations-2021-2026/load_to_postgres.py
python audit-veytaux/interpellations-2021-2026/validate_database.py
```

Corpus validé : 17 interpellations, 35 documents canoniques, 16 réponses municipales, 2 résolutions, 35 pièces OCRisées et 90 chunks vectorisés avec `mistral-embed`.

Le seul postulat mentionné sur la page (2019) est antérieur à la législature 2021-2026 et n'a pas été repris.
