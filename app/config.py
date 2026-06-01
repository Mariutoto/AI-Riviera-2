from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
INDEX_DIR = PROJECT_ROOT / "data" / "index"
DB_DIR = PROJECT_ROOT / "db"
POSTGRES_SCHEMA_PATH = DB_DIR / "postgres" / "migrations" / "001_init.sql"
OPENSEARCH_MAPPING_PATH = DB_DIR / "opensearch" / "chunks-index.json"
CHUNKS_PATH = INDEX_DIR / "chunks.jsonl"
SQLITE_PATH = INDEX_DIR / "ai_riviera.sqlite"
STRUCTURED_DATA_DIR = PROJECT_ROOT / "data" / "structured" / "la-tour-de-peilz"
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sql").lower().strip()
ENABLE_LEGACY_JSON_FALLBACK = os.getenv("ENABLE_LEGACY_JSON_FALLBACK", "0").lower().strip() in {"1", "true", "yes", "on"}
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://localhost:5432/ai_riviera")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "ai_riviera_chunks")
OPENSEARCH_TIMEOUT = float(os.getenv("OPENSEARCH_TIMEOUT", "2"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
