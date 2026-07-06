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
INDEX_DIR = PROJECT_ROOT / "data" / "index"
DB_DIR = PROJECT_ROOT / "db"
POSTGRES_SCHEMA_PATH = DB_DIR / "postgres" / "migrations" / "001_init.sql"
OPENSEARCH_MAPPING_PATH = DB_DIR / "opensearch" / "chunks-index.json"
CHUNKS_PATH = INDEX_DIR / "chunks.jsonl"
SQLITE_PATH = INDEX_DIR / "ai_riviera.sqlite"
STRUCTURED_DATA_DIR = PROJECT_ROOT / "data" / "structured" / "la-tour-de-peilz"
RAG_VERSION = config_value("RAG_VERSION", "v1").lower().strip()
STORAGE_BACKEND = config_value("STORAGE_BACKEND", "sql").lower().strip()
ENABLE_LEGACY_JSON_FALLBACK = config_value("ENABLE_LEGACY_JSON_FALLBACK", "0").lower().strip() in {"1", "true", "yes", "on"}
POSTGRES_URL = config_value("POSTGRES_URL", "postgresql://localhost:5432/ai_riviera", ("postgres", "url"))
OPENSEARCH_URL = config_value("OPENSEARCH_URL", "http://localhost:9200", ("opensearch", "url"))
OPENSEARCH_INDEX = config_value("OPENSEARCH_INDEX", "ai_riviera_chunks", ("opensearch", "index"))
OPENSEARCH_TIMEOUT = float(config_value("OPENSEARCH_TIMEOUT", "2", ("opensearch", "timeout")))
EMBEDDING_MODEL = config_value("EMBEDDING_MODEL", "text-embedding-3-small", ("embedding", "model"))
EMBEDDING_DIMENSIONS = int(config_value("EMBEDDING_DIMENSIONS", "1536", ("embedding", "dimensions")))
