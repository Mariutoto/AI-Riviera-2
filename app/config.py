from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_ROOT = PROJECT_ROOT / "documents" / "la-tour-de-peilz"
INDEX_DIR = PROJECT_ROOT / "data" / "index"
CHUNKS_PATH = INDEX_DIR / "chunks.jsonl"
SQLITE_PATH = INDEX_DIR / "ai_riviera.sqlite"
STRUCTURED_DATA_DIR = PROJECT_ROOT / "data" / "structured" / "la-tour-de-peilz"
