
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS entities (
    bin         TEXT PRIMARY KEY,
    name_ru     TEXT,
    name_kz     TEXT,
    name_en     TEXT,
    type        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source          TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    url             TEXT,
    language        TEXT,
    edition         TEXT,
    published_at    DATE,
    effective_from  DATE,
    retrieved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT        NOT NULL,
    raw_path        TEXT,
    UNIQUE (source, content_hash)
);

CREATE TABLE IF NOT EXISTS chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  BIGINT      NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal      INT         NOT NULL,
    text         TEXT        NOT NULL,
    article_ref  TEXT,
    char_start   INT,
    char_end     INT,
    embedding    vector(1024),
    tsv          tsvector,
    text_hash    TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

CREATE TABLE IF NOT EXISTS metrics (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bin           TEXT        REFERENCES entities(bin),
    metric        TEXT        NOT NULL,
    period        DATE        NOT NULL,
    value         NUMERIC     NOT NULL,
    unit          TEXT        NOT NULL,
    source_doc    BIGINT      REFERENCES documents(id),
    page          INT,
    line_code     TEXT,
    published_at  DATE,
    retrieved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (bin, metric, period, source_doc)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_tsv_gin
    ON chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);

CREATE UNIQUE INDEX IF NOT EXISTS chunks_text_hash_uniq ON chunks (text_hash);
CREATE INDEX IF NOT EXISTS metrics_lookup_idx     ON metrics (bin, metric, period);

CREATE TABLE IF NOT EXISTS user_profile (
    user_ref        TEXT PRIMARY KEY,
    consent         BOOLEAN     NOT NULL DEFAULT false,
    language        TEXT,
    focus_companies JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS query_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_ref    TEXT        NOT NULL REFERENCES user_profile(user_ref) ON DELETE CASCADE,
    session_id  TEXT        NOT NULL,
    query       TEXT        NOT NULL,
    intent      TEXT        NOT NULL,
    companies   TEXT[]      NOT NULL DEFAULT '{}',
    answer      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS query_history_user_idx ON query_history (user_ref, created_at DESC);

CREATE TABLE IF NOT EXISTS metric_info (
    metric      TEXT PRIMARY KEY,
    label_ru    TEXT,
    label_en    TEXT,
    unit        TEXT,
    source      TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS ratings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bin         TEXT        REFERENCES entities(bin),
    agency      TEXT        NOT NULL,
    rating      TEXT        NOT NULL,
    previous    TEXT,
    outlook     TEXT,
    rated_on    DATE,
    source_doc  BIGINT      REFERENCES documents(id),
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (bin, agency, rated_on)
);

ALTER TABLE documents ADD COLUMN IF NOT EXISTS bin TEXT REFERENCES entities(bin);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type TEXT;
CREATE INDEX IF NOT EXISTS documents_bin_type_idx ON documents (bin, doc_type);

CREATE TABLE IF NOT EXISTS web_pages (
    url          TEXT PRIMARY KEY,
    domain       TEXT        NOT NULL,
    title        TEXT,
    official     BOOLEAN     NOT NULL,
    times_used   INT         NOT NULL DEFAULT 1,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at  TIMESTAMPTZ,
    document_id  BIGINT      REFERENCES documents(id) ON DELETE SET NULL
);
