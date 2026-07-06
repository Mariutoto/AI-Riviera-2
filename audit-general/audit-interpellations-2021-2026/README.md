# Audit des interpellations 2021–2026

Sous Windows, double-cliquer sur `OPEN_AUDIT.cmd` pour ouvrir l’audit complet dans le navigateur.

On peut aussi ouvrir directement `audit.html`. Tous les liens vers les métadonnées, textes nettoyés, blocs retirés et chunks sont relatifs au dossier et fonctionnent hors ligne.

## Régénérer l’audit

Depuis la racine du projet :

```powershell
python audit-interpellations-2021-2026/build_full_audit.py
python audit-interpellations-2021-2026/build_chunk_audit.py
```

Les OCR déjà produits sont conservés dans `ocr_overrides/`; leur régénération nécessite la clé Mistral locale, qui ne doit jamais être ajoutée à Git.
