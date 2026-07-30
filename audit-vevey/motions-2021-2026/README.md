# Motions de Vevey — législature 2021–2026

Pipeline reproductible pour les motions déposées pendant la législature
2021–2026 à Vevey.

## Résultat validé

- 65 entrées dans le filtre officiel « Motion » ;
- 18 entrées datées de 2021 à 2026 ;
- 4 motions confirmées par le titre et le contenu ;
- 14 faux positifs du catalogue exclus ;
- 10 PDF canoniques : 4 motions et 6 documents de suivi ;
- 32 chunks, tous validés et vectorisés avec `mistral-embed` (1024 dimensions) ;
- 2 motions avec réponse et 2 sans réponse ;
- aucun PDF nécessitant un OCR.

Les quatre objets sont distingués ainsi :

1. motion Sandra Marques : prise en considération refusée, sans réponse ;
2. motion « Précarité énergétique » : réponse dans le rapport 2025/P13,
   puis classement ;
3. motion Joëlle Minacci : en traitement, sans réponse, délai au 31 mars 2027 ;
4. motion Patrick Bertschy : transformée en postulat, avec réponse dédiée
   2026/RP18.

## Exécution

Depuis la racine du dépôt :

```powershell
python scrape-vevey/scrape_motions_2021_2026.py
python audit-vevey/motions-2021-2026/build_audit.py
python audit-vevey/motions-2021-2026/generate_embedding_inputs.py
python audit-vevey/motions-2021-2026/generate_embeddings.py
python audit-vevey/motions-2021-2026/load_to_postgres.py
python audit-vevey/motions-2021-2026/validate_database.py
```

Les PDF et les vecteurs sont reproductibles et ignorés par Git. Les textes
nettoyés, métadonnées, chunks, rapports d’audit et manifestes sont conservés.

Source officielle :
<https://www.vevey.ch/vie-politique/conseil-communal/documents-du-conseil-communal>
