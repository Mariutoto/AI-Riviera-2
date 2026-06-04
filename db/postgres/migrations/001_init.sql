CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS cities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    canton TEXT DEFAULT '',
    country TEXT DEFAULT 'CH',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    city_id UUID NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL DEFAULT '',
    doc_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    document_date DATE,
    fetch_date TIMESTAMPTZ,
    last_processed_at TIMESTAMPTZ,
    document_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (city_id, source_path)
);

CREATE INDEX IF NOT EXISTS idx_documents_city_id ON documents(city_id);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_document_date ON documents(document_date);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    city_id UUID NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    doc_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    document_date DATE,
    source_url TEXT NOT NULL,
    content TEXT NOT NULL,
    document_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE document_chunks
ADD COLUMN IF NOT EXISTS search_vector TSVECTOR NOT NULL DEFAULT ''::tsvector;

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_city_id ON document_chunks(city_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_type ON document_chunks(doc_type);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_date ON document_chunks(document_date);
CREATE INDEX IF NOT EXISTS idx_document_chunks_search_vector ON document_chunks USING GIN(search_vector);

UPDATE document_chunks
SET search_vector =
    setweight(to_tsvector('french', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('french', coalesce(doc_type, '')), 'B') ||
    setweight(to_tsvector('french', coalesce(content, '')), 'C') ||
    setweight(to_tsvector('french', coalesce(metadata::text, '')), 'D')
WHERE search_vector = ''::tsvector;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_name TEXT NOT NULL DEFAULT 'manual',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ingestion_logs (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID REFERENCES ingestion_runs(id) ON DELETE CASCADE,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_logs_run_id ON ingestion_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_logs_level ON ingestion_logs(level);

CREATE TABLE IF NOT EXISTS financial_summary_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    city_id UUID NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    table_key TEXT NOT NULL UNIQUE,
    fiscal_year INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'CHF',
    source_path TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_financial_summary_tables_document_id ON financial_summary_tables(document_id);
CREATE INDEX IF NOT EXISTS idx_financial_summary_tables_city_year ON financial_summary_tables(city_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_financial_summary_tables_metric ON financial_summary_tables(metric);

CREATE TABLE IF NOT EXISTS financial_summary_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id UUID NOT NULL REFERENCES financial_summary_tables(id) ON DELETE CASCADE,
    row_order INTEGER NOT NULL DEFAULT 0,
    service_code TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL DEFAULT '',
    values JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (table_id, row_order, service_code, service_name)
);

CREATE INDEX IF NOT EXISTS idx_financial_summary_rows_table_id ON financial_summary_rows(table_id);
CREATE INDEX IF NOT EXISTS idx_financial_summary_rows_service_code ON financial_summary_rows(service_code);

CREATE TABLE IF NOT EXISTS financial_account_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    city_id UUID NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    line_key TEXT NOT NULL UNIQUE,
    fiscal_year INTEGER NOT NULL,
    service_code TEXT NOT NULL DEFAULT '',
    service_name TEXT NOT NULL DEFAULT '',
    group_code TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    account_number TEXT NOT NULL DEFAULT '',
    account_label TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'CHF',
    values JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_path TEXT NOT NULL DEFAULT '',
    line_number INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_financial_account_lines_document_id ON financial_account_lines(document_id);
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_city_year ON financial_account_lines(city_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_account_number ON financial_account_lines(account_number);
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_service_code ON financial_account_lines(service_code);
