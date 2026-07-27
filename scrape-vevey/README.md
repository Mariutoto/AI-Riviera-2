# Pilote Vevey

Ce dossier contient une collecte isolée des interpellations publiées par le
Conseil communal de Vevey pour 2025 et 2026.

Le pilote ne modifie ni la base PostgreSQL ni l'index de production. Il :

1. applique le filtre officiel « Interpellation » ;
2. parcourt toutes les pages de résultats avant de limiter localement la
   période à 2025–2026 (le filtre de date du site omet certains ajouts récents) ;
3. vérifie que le nombre collecté correspond au nombre annoncé ;
4. conserve les occurrences de la liste ;
5. peut télécharger les PDF et regrouper les contenus strictement identiques
   grâce à leur empreinte SHA-256.

Collecte de la liste :

```powershell
python scrape-vevey/scrape_interpellations_pilot.py `
  --output audit-vevey/interpellations-pilot/inventory.json
```

Audit des PDF, sans conserver les fichiers :

```powershell
python scrape-vevey/scrape_interpellations_pilot.py `
  --audit-downloads `
  --output audit-vevey/interpellations-pilot/inventory.json `
  --html-output audit-vevey/interpellations-pilot/audit.html
```

Pour conserver temporairement les PDF, ajouter
`--download-dir <répertoire>`. Les fichiers téléchargés ne doivent pas être
ajoutés au dépôt avant validation de leur volume et de leur qualité.

## Annexes et réponses aux interpellations

La collecte des Annexes reste séparée de celle des interpellations. Elle
parcourt toute la rubrique pour contrôler sa complétude, puis télécharge
uniquement les entrées `RI` ou explicitement décrites comme réponses.

```powershell
python scrape-vevey/scrape_annexes_pilot.py `
  --audit-candidates `
  --download-dir audit-vevey/annexes-pilot/pdfs `
  --output audit-vevey/annexes-pilot/inventory.json `
  --html-output audit-vevey/annexes-pilot/audit.html
```

Le rapprochement est une troisième étape indépendante :

```powershell
python scrape-vevey/link_interpellation_responses.py `
  --interpellations audit-vevey/interpellations-pilot/inventory.json `
  --annexes audit-vevey/annexes-pilot/inventory.json `
  --interpellation-metadata-dir audit-vevey/interpellations-pilot/general-audit/metadata `
  --output-dir audit-vevey/interpellation-response-links
```

Le rapprochement ne crée automatiquement un lien que pour une correspondance
exacte ou probable. Les résultats ambigus gardent un meilleur candidat pour
la revue, mais leur `political_object_id` reste vide afin d'éviter une fausse
relation.
