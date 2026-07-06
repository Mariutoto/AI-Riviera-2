# Évaluation future

## Benchmark comparatif

```powershell
python embedding-pilot/evaluation/compare_pipelines.py
```

Les deux côtés utilisent la même reformulation, le même reranker LLM et le
même modèle de réponse. Seule la récupération change : pipeline actuel contre
PostgreSQL/pgvector avec `mistral-embed`.

Le rapport est généré dans `comparison_report.html`, avec les données complètes
dans `comparison_results.json`.

Les tests devront couvrir au minimum :

- retrouver un texte politique initial ;
- distinguer texte initial, rapport et décision ;
- retrouver majorité et minorité ;
- retrouver un article du règlement par numéro ;
- retrouver un article par thème sans connaître son numéro ;
- vérifier les résultats croisés motion/postulat/interpellation.

Les questions et les résultats attendus seront écrits avant de lancer les
embeddings afin de ne pas adapter les tests aux résultats obtenus.
