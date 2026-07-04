# Audit des motions

Le sous-dossier `pilot/` contient le pilote documentaire couvrant les 12 motions
officielles actuellement publiées pour la législature 2021–2026.

Le pilote sert à :

1. construire les métadonnées documentaires de base ;
2. conserver une copie des métadonnées additionnelles produites par le scraper ;
3. classer les champs additionnels (doublon, utile, contradictoire ou supprimable) ;
4. proposer un schéma minimal ;
5. tester le nettoyage des textes sans modifier les données existantes.

Dans `pilot/combined_metadata_view/`, chaque document réunit la métadonnée de
base, la métadonnée motion minimale et le bloc `processing` retenu.

Exécution :

```powershell
.\.venv\Scripts\python.exe audit-general\audit-motions\build_pilot.py
```

L'audit complet ne sera lancé qu'après validation du pilote.
