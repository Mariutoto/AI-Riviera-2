CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS embedding_runs (
    run_id uuid PRIMARY KEY,
    model_name text NOT NULL,
    model_dimension integer NOT NULL CHECK (model_dimension = 1024),
    recipe_version text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    status text NOT NULL CHECK (status IN ('loading', 'completed', 'failed')),
    input_chunks integer NOT NULL,
    tokens_used integer
);

CREATE TABLE IF NOT EXISTS documents (
    document_id text PRIMARY KEY,
    document_family text NOT NULL,
    category text NOT NULL,
    document_role text,
    title text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    component text,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding_input text NOT NULL,
    embedding vector(1024) NOT NULL,
    embedding_model text NOT NULL,
    embedding_run_id uuid NOT NULL REFERENCES embedding_runs(run_id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks(document_id);
CREATE INDEX IF NOT EXISTS documents_category_idx ON documents(category);
