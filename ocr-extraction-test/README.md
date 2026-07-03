# Test extraction native contre Mistral OCR

Ce mini-projet compare trois PDF de La Tour-de-Peilz avec :

1. l'extraction native PyMuPDF ;
2. Mistral OCR avec `mistral-ocr-latest`.

## Clé API

Ouvre `.env` et colle ta clé après le signe `=` :

```dotenv
MISTRAL_API_KEY=ta_cle_ici
```

Le fichier `.env` est ignoré par Git. Ne publie jamais cette clé.

## Installation et lancement sous PowerShell

```powershell
cd "C:\Users\yannb\Documents\AI Riviera 2\ocr-extraction-test"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_comparison.py
```

Le rapport final sera créé dans `results/comparison_report.md`. Chaque document aura aussi son texte natif, sa sortie OCR Markdown, la réponse OCR JSON et ses métriques.

Les scores indiquent à quel point les deux sorties se ressemblent. Ils ne constituent pas une vérité terrain : il faut ouvrir les deux textes et vérifier les noms, nombres, accents, tableaux et ordre de lecture.
