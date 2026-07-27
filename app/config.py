from pathlib import Path
import os

from municipal_pipeline.municipalities import MUNICIPALITIES


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
DOCUMENTS_ROOTS = {
    key: PROJECT_ROOT / "documents" / municipality.documents_directory
    for key, municipality in MUNICIPALITIES.items()
}
# Backward-compatible alias while historical ingestion scripts are migrated.
DOCUMENTS_ROOT = DOCUMENTS_ROOTS["la-tour-de-peilz"]
