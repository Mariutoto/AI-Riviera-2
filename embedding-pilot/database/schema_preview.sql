-- PROPOSITION UNIQUEMENT : ce fichier n'est pas encore exécuté.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE embedding_runs (
    run_id uuid PRIMARY KEY,
    model_name text NOT NULL,
    model_dimension integer NOT NULL CHECK (model_dimension = 1024),
    recipe_version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    status text NOT NULL
);

CREATE TABLE documents (
    document_id text PRIMARY KEY,
    document_family text NOT NULL,
    category text NOT NULL,
    document_role text,
    title text NOT NULL,
    source_url text,
    metadata jsonb NOT NULL
);

CREATE TABLE chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES documents(document_id),
    chunk_index integer NOT NULL,
    component text,
    content text NOT NULL,
    embedding_input text NOT NULL,
    embedding vector(1024),
    embedding_model text,
    embedding_run_id uuid REFERENCES embedding_runs(run_id),
    metadata jsonb NOT NULL,
    UNIQUE (document_id, chunk_index)
);

-- L'index sera créé après le chargement du pilote et la validation du choix.
-- CREATE INDEX chunks_embedding_hnsw
-- ON chunks USING hnsw (embedding vector_cosine_ops);
