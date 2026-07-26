ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS summary text,
    ADD COLUMN IF NOT EXISTS summary_generated_at timestamptz,
    ADD COLUMN IF NOT EXISTS summary_model text,
    ADD COLUMN IF NOT EXISTS summary_content_hash text;
