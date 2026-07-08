from pathlib import Path
import os


def config_value(name: str, default: str = "", *secret_paths: tuple[str, str]) -> str:
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        value = st.secrets.get(name)
        if value:
            return str(value)
        for section, key in secret_paths:
            section_value = st.secrets.get(section, {})
            if section_value and section_value.get(key):
                return str(section_value[key])
    except Exception:
        pass

    return default


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
