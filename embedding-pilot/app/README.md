# Interface Streamlit du pilote

La base Docker et Docker Desktop doivent être démarrés avant l'application.

```powershell
docker compose -f embedding-pilot/database/compose.yaml up -d --wait
streamlit run embedding-pilot/app/streamlit_app.py
```

L'application utilise la clé `MISTRAL_API_KEY` présente dans
`embedding-pilot/.env` uniquement pour vectoriser les nouvelles questions.

Cette interface est prévue pour un test local : un déploiement Streamlit Cloud
ne peut pas joindre la base Docker de l'ordinateur.
