# Audit complet des motions 2021–2026

Sous Windows, double-cliquer sur `OPEN_AUDIT.cmd`, exactement comme pour
l'audit des interpellations. On peut aussi ouvrir `audit.html` directement.

L'audit principal reprend la même navigation : métadonnées manquantes, dates,
nettoyage, OCR, qualité des chunks et liens vers JSON, texte, blocs retirés et
pages détaillées. `chunks_audit.html` présente l'audit global des chunks.

Régénération depuis la racine du projet :

```powershell
.\.venv\Scripts\python.exe audit-general\audit-motions\full-audit\build_audit.py
```

L'audit ne modifie pas les données de production sous `documents/`.
