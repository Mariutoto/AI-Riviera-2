# Interpellations de Blonay–Saint-Légier — législature 2021–2026

Pipeline reproductible couvrant les interpellations déposées depuis la création de la commune (fusion de Blonay et St-Légier-La Chiésaz au 1er juillet 2023) jusqu'à la fin de la législature cantonale 2021–2026.

Le site officiel (plateforme « icms ») est nettement plus simple que celui de Montreux : la page `/objets-politiques` embarque déjà, dans un attribut `data-entities`, la liste complète des 288 objets politiques de la commune (aucune pagination à gérer). Chaque fiche objet est ensuite auto-suffisante — statut, auteur·ice et documents (texte original, réponse municipale, résolution éventuelle) vivent tous sur la même page, sans objet séparé à recouper. Vérification faite sur les 51 interpellations : la section « Objets associés » de chaque fiche est systématiquement vide, confirmant qu'aucun lien croisé n'existe sur ce site pour cette catégorie.

Deux particularités traitées explicitement :
- Un objet (2024-IN-03) n'a pas de texte original mis en ligne, seule la réponse existe ; un texte de repli est généré à partir des métadonnées de la fiche.
- Une pièce jointe (une résolution) est une image JPEG plutôt qu'un PDF ; elle est convertie en PDF d'une page avant extraction/OCR, pour que le reste du pipeline n'ait pas à en tenir compte.

Ordre d'exécution :

```powershell
python scrape-blonay-saint-legier/scrape_interpellations_2021_2026.py
python audit-blonay-saint-legier/interpellations-2021-2026/run_targeted_ocr.py
python audit-blonay-saint-legier/interpellations-2021-2026/build_audit.py
python audit-blonay-saint-legier/interpellations-2021-2026/generate_embedding_inputs.py
python audit-blonay-saint-legier/interpellations-2021-2026/generate_embeddings.py
python audit-blonay-saint-legier/interpellations-2021-2026/load_to_postgres.py
python audit-blonay-saint-legier/interpellations-2021-2026/validate_database.py
```

Corpus validé : 51 interpellations, 93 documents canoniques, 39 réponses municipales vérifiées (dont 4 objets où la réponse est déjà en ligne alors que le statut affiché sur le site n'est pas encore mis à jour), 2 résolutions, 63 pièces OCRisées et 241 chunks vectorisés avec `mistral-embed`.
