-- Neon + pgvector schema for the RAG store (replaces ChromaDB).
-- Embeddings: Voyage `voyage-3` → 1024 dims. Adjust if you switch models.

CREATE EXTENSION IF NOT EXISTS vector;

-- Application document chunks (one collection per application via application_ref).
CREATE TABLE IF NOT EXISTS app_chunks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_ref TEXT NOT NULL,
    source_file   TEXT NOT NULL,
    page_number   INTEGER,
    document_type TEXT,
    chunk_text    TEXT NOT NULL,
    embedding     vector(1024) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_chunks_ref_idx ON app_chunks (application_ref);
CREATE INDEX IF NOT EXISTS app_chunks_embedding_idx
    ON app_chunks USING hnsw (embedding vector_cosine_ops);

-- Policy knowledge base with temporal (point-in-time) filtering. A clause is
-- effective for a date D when effective_from <= D < coalesce(effective_to, ∞).
CREATE TABLE IF NOT EXISTS policy_chunks (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source         TEXT NOT NULL,        -- e.g. LTN_1_20, NPPF, Cherwell_Local_Plan
    section_ref    TEXT,                 -- e.g. "Chapter 5", "Table 5-2", "Policy SLE 4"
    binding        BOOLEAN NOT NULL DEFAULT true, -- binding policy vs adopted strategy/best-practice
    chunk_text     TEXT NOT NULL,
    embedding      vector(1024) NOT NULL,
    revision_id    TEXT,
    effective_from DATE NOT NULL DEFAULT DATE '1900-01-01',
    effective_to   DATE,                 -- NULL = current
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS policy_chunks_source_idx ON policy_chunks (source);
CREATE INDEX IF NOT EXISTS policy_chunks_effective_idx ON policy_chunks (effective_from, effective_to);
CREATE INDEX IF NOT EXISTS policy_chunks_embedding_idx
    ON policy_chunks USING hnsw (embedding vector_cosine_ops);
