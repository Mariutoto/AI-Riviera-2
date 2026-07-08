# Évaluation future

Cette branche ne contient plus que le pipeline V2 (Mistral embed + Aiven
pgvector) — `compare_pipelines.py` comparait ce pipeline à l'ancien Postgres
V1, retiré de cette branche, et a été supprimé avec lui. L'onglet Eval de
l'application (`SHOW_ADMIN_TABS=1`) et `eval/eval_questions.json` couvrent
désormais le suivi de la qualité de recherche.

Les tests devront couvrir au minimum :

- retrouver un texte politique initial ;
- distinguer texte initial, rapport et décision ;
- retrouver majorité et minorité ;
- retrouver un article du règlement par numéro ;
- retrouver un article par thème sans connaître son numéro ;
- vérifier les résultats croisés motion/postulat/interpellation.

Les questions et les résultats attendus seront écrits avant de lancer les
embeddings afin de ne pas adapter les tests aux résultats obtenus.
