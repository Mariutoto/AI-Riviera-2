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
