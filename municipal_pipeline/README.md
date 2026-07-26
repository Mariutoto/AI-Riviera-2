# Pipeline municipale commune

`municipal_pipeline` contient uniquement ce qui doit être identique pour
toutes les communes :

- registre et état d'activation des communes ;
- contrat minimal des métadonnées documentaires ;
- téléchargement, empreinte SHA-256, déduplication et audit texte des PDF.

Les collecteurs restent séparés parce que les sites communaux ont des
structures différentes :

```text
scrape-la-tour-de-peilz/   collecte propre au site de La Tour-de-Peilz
scrape-vevey/              collecte propre au site de Vevey
municipal_pipeline/        traitement partagé après la collecte
embedding-pilot/           chunks, embeddings et chargement PostgreSQL
app/                       recherche et interface communes
```

La migration est volontairement progressive. Les scrapers historiques de
La Tour-de-Peilz continuent à fonctionner avec `DOCUMENTS_ROOT`, tandis que
les nouveaux collecteurs utilisent le registre et le contrat communs. Une
catégorie historique peut ainsi être migrée et testée sans déplacer toutes
les autres en même temps.

Une commune ne doit être marquée `search_enabled=True` qu'après :

1. validation de sa collecte ;
2. indexation de ses documents ;
3. ajout du filtre SQL obligatoire par ville ;
4. tests empêchant le relâchement du filtre ville.
