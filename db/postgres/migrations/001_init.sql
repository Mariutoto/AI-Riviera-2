CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF to_regclass('public.documents') IS NOT NULL THEN
        ALTER TABLE documents ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT '';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'city_id'
        ) AND to_regclass('public.cities') IS NOT NULL THEN
            UPDATE documents d
            SET city = c.name
            FROM cities c
            WHERE d.city_id = c.id AND COALESCE(d.city, '') = '';
        END IF;
        UPDATE documents
        SET city = COALESCE(NULLIF(city, ''), metadata->>'commune', 'La Tour-de-Peilz')
        WHERE COALESCE(city, '') = '';
    END IF;

    IF to_regclass('public.document_chunks') IS NOT NULL THEN
        ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT '';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'document_chunks' AND column_name = 'city_id'
        ) AND to_regclass('public.cities') IS NOT NULL THEN
            UPDATE document_chunks dc
            SET city = c.name
            FROM cities c
            WHERE dc.city_id = c.id AND COALESCE(dc.city, '') = '';
        END IF;
        UPDATE document_chunks
        SET city = COALESCE(NULLIF(city, ''), metadata->>'commune', 'La Tour-de-Peilz')
        WHERE COALESCE(city, '') = '';
    END IF;

    IF to_regclass('public.financial_summary_tables') IS NOT NULL THEN
        ALTER TABLE financial_summary_tables ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT '';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'financial_summary_tables' AND column_name = 'city_id'
        ) AND to_regclass('public.cities') IS NOT NULL THEN
            UPDATE financial_summary_tables fst
            SET city = c.name
            FROM cities c
            WHERE fst.city_id = c.id AND COALESCE(fst.city, '') = '';
        END IF;
        UPDATE financial_summary_tables
        SET city = COALESCE(NULLIF(city, ''), 'La Tour-de-Peilz')
        WHERE COALESCE(city, '') = '';
    END IF;

    IF to_regclass('public.financial_account_lines') IS NOT NULL THEN
        ALTER TABLE financial_account_lines ADD COLUMN IF NOT EXISTS city TEXT NOT NULL DEFAULT '';
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'financial_account_lines' AND column_name = 'city_id'
        ) AND to_regclass('public.cities') IS NOT NULL THEN
            UPDATE financial_account_lines fal
            SET city = c.name
            FROM cities c
            WHERE fal.city_id = c.id AND COALESCE(fal.city, '') = '';
        END IF;
        UPDATE financial_account_lines
        SET city = COALESCE(NULLIF(city, ''), 'La Tour-de-Peilz')
        WHERE COALESCE(city, '') = '';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    city TEXT NOT NULL DEFAULT '',
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
    UNIQUE (city, source_path)
);

CREATE INDEX IF NOT EXISTS idx_documents_city ON documents(city);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_document_date ON documents(document_date);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    city TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_document_chunks_city ON document_chunks(city);
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

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    city TEXT NOT NULL DEFAULT '',
    canonical_name TEXT NOT NULL DEFAULT '',
    normalized_name TEXT NOT NULL DEFAULT '',
    party_current TEXT NOT NULL DEFAULT '',
    parties JSONB NOT NULL DEFAULT '[]'::jsonb,
    variants JSONB NOT NULL DEFAULT '[]'::jsonb,
    roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    years JSONB NOT NULL DEFAULT '[]'::jsonb,
    objects JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_people_city ON people(city);
CREATE INDEX IF NOT EXISTS idx_people_normalized_name ON people(normalized_name);
CREATE INDEX IF NOT EXISTS idx_people_party_current ON people(party_current);
CREATE INDEX IF NOT EXISTS idx_people_parties ON people USING GIN(parties);
CREATE INDEX IF NOT EXISTS idx_people_objects ON people USING GIN(objects);

CREATE TABLE IF NOT EXISTS political_objects (
    object_id TEXT PRIMARY KEY,
    city TEXT NOT NULL DEFAULT '',
    legislature TEXT NOT NULL DEFAULT '',
    object_type TEXT NOT NULL DEFAULT '',
    object_title TEXT NOT NULL DEFAULT '',
    status_raw TEXT NOT NULL DEFAULT '',
    status_normalized TEXT NOT NULL DEFAULT '',
    deposit_date DATE,
    decision_date DATE,
    year TEXT NOT NULL DEFAULT '',
    canonical_source_url TEXT NOT NULL DEFAULT '',
    canonical_document_source_url TEXT NOT NULL DEFAULT '',
    canonical_document_path TEXT NOT NULL DEFAULT '',
    authors JSONB NOT NULL DEFAULT '[]'::jsonb,
    documents JSONB NOT NULL DEFAULT '[]'::jsonb,
    scheduled_sessions JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_political_objects_city ON political_objects(city);
CREATE INDEX IF NOT EXISTS idx_political_objects_type ON political_objects(object_type);
CREATE INDEX IF NOT EXISTS idx_political_objects_year ON political_objects(year);
CREATE INDEX IF NOT EXISTS idx_political_objects_status ON political_objects(status_normalized);
CREATE INDEX IF NOT EXISTS idx_political_objects_authors ON political_objects USING GIN(authors);
CREATE INDEX IF NOT EXISTS idx_political_objects_documents ON political_objects USING GIN(documents);

CREATE TABLE IF NOT EXISTS political_object_people (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_id TEXT NOT NULL REFERENCES political_objects(object_id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(person_id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'author',
    party_at_time TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (object_id, person_id, role)
);

CREATE INDEX IF NOT EXISTS idx_political_object_people_object_id ON political_object_people(object_id);
CREATE INDEX IF NOT EXISTS idx_political_object_people_person_id ON political_object_people(person_id);
CREATE INDEX IF NOT EXISTS idx_political_object_people_role ON political_object_people(role);
CREATE INDEX IF NOT EXISTS idx_political_object_people_party ON political_object_people(party_at_time);

CREATE TABLE IF NOT EXISTS political_object_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_id TEXT NOT NULL REFERENCES political_objects(object_id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    relation_type TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    document_date DATE,
    order_index INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (object_id, source_url, source_path, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_political_object_documents_object_id ON political_object_documents(object_id);
CREATE INDEX IF NOT EXISTS idx_political_object_documents_document_id ON political_object_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_political_object_documents_relation_type ON political_object_documents(relation_type);
CREATE INDEX IF NOT EXISTS idx_political_object_documents_source_url ON political_object_documents(source_url);
CREATE INDEX IF NOT EXISTS idx_political_object_documents_document_date ON political_object_documents(document_date);

CREATE TABLE IF NOT EXISTS financial_summary_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    city TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_financial_summary_tables_city_year ON financial_summary_tables(city, fiscal_year);
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
    city TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_city_year ON financial_account_lines(city, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_account_number ON financial_account_lines(account_number);
CREATE INDEX IF NOT EXISTS idx_financial_account_lines_service_code ON financial_account_lines(service_code);

DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    DROP INDEX IF EXISTS idx_documents_city_id;
    DROP INDEX IF EXISTS idx_document_chunks_city_id;
    DROP INDEX IF EXISTS idx_financial_summary_tables_city_year;
    DROP INDEX IF EXISTS idx_financial_account_lines_city_year;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'city_id'
    ) THEN
        FOR constraint_name IN
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.documents'::regclass
              AND conname IN ('documents_city_id_fkey', 'documents_city_id_source_path_key')
        LOOP
            EXECUTE format('ALTER TABLE documents DROP CONSTRAINT IF EXISTS %I', constraint_name);
        END LOOP;
        ALTER TABLE documents DROP COLUMN city_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'document_chunks' AND column_name = 'city_id'
    ) THEN
        ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS document_chunks_city_id_fkey;
        ALTER TABLE document_chunks DROP COLUMN city_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'financial_summary_tables' AND column_name = 'city_id'
    ) THEN
        ALTER TABLE financial_summary_tables DROP CONSTRAINT IF EXISTS financial_summary_tables_city_id_fkey;
        ALTER TABLE financial_summary_tables DROP COLUMN city_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'financial_account_lines' AND column_name = 'city_id'
    ) THEN
        ALTER TABLE financial_account_lines DROP CONSTRAINT IF EXISTS financial_account_lines_city_id_fkey;
        ALTER TABLE financial_account_lines DROP COLUMN city_id;
    END IF;

    ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_city_source_path_key;
    ALTER TABLE documents ADD CONSTRAINT documents_city_source_path_key UNIQUE (city, source_path);
    CREATE INDEX IF NOT EXISTS idx_documents_city ON documents(city);
    CREATE INDEX IF NOT EXISTS idx_document_chunks_city ON document_chunks(city);
    CREATE INDEX IF NOT EXISTS idx_financial_summary_tables_city_year ON financial_summary_tables(city, fiscal_year);
    CREATE INDEX IF NOT EXISTS idx_financial_account_lines_city_year ON financial_account_lines(city, fiscal_year);
    DROP TABLE IF EXISTS cities;
END $$;
