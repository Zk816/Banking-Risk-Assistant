-- Kazakhstan Banking Intelligence — full schema.
-- Re-runnable: every statement is IF NOT EXISTS. Applied by scripts/init_db.py.
--
-- NOTE: chunks.embedding is vector(1024) because that is bge-m3's dense
-- dimension. If you change EMBED_MODEL in .env, this number must change too —
-- scripts/init_db.py checks that the two agree and refuses to run if they don't.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- entities: who a number is about. Identified by BIN, never by name.
-- Names appear in Russian, Kazakh, English, and old forms — they are labels,
-- not identity.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    bin         TEXT PRIMARY KEY,
    name_ru     TEXT,
    name_kz     TEXT,
    name_en     TEXT,
    type        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- documents: a source text. Legal acts get edition + effective date, because
-- "what does the law say" is always "as of when".
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- chunks: retrievable pieces of a document.
--   embedding — bge-m3 dense vector, for meaning
--   tsv       — Postgres full-text, for exact tokens ("k2", "пруденциальный")
-- The tsv column exists from day one but stays unused until Step 3.
-- ---------------------------------------------------------------------------
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
    -- Deduplication key. Measured on the real corpus: 21.4% of chunks were
    -- byte-identical, and one boilerplate line ("Форму подписывают
    -- руководитель…") appeared 87 times — it is repeated in every annex of
    -- every amending resolution. Twenty copies of the same template can fill
    -- the entire candidate budget before the reranker sees anything useful.
    text_hash    TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

-- ---------------------------------------------------------------------------
-- metrics: the numbers. Every value carries unit, period, and provenance —
-- nothing is stored that we cannot attribute to a document and a date.
-- Populated from Step 7 onward; the shape is fixed now so it never becomes
-- tempting to store a bare number.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- NULL means the value is not about one company: an exchange rate or a base
    -- rate belongs to the whole market. Bank-level metrics always carry a BIN.
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
    -- NULLS NOT DISTINCT is load-bearing. By default Postgres treats every NULL
    -- as unique, so with a NULL bin this constraint would permit unlimited
    -- duplicates of the same rate — exactly the rows it exists to prevent.
    UNIQUE NULLS NOT DISTINCT (bin, metric, period, source_doc)
);

-- --- Indexes ---------------------------------------------------------------

-- Vector similarity. HNSW over cosine distance: the `<=>` operator.
-- Built now while the table is empty, which is the cheap moment to do it.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Full-text. Unused until Step 3, but free to create now.
CREATE INDEX IF NOT EXISTS chunks_tsv_gin
    ON chunks USING gin (tsv);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);

-- One row per distinct text. A chunk repeated across acts is stored once; the
-- act it was first seen in keeps the citation.
CREATE UNIQUE INDEX IF NOT EXISTS chunks_text_hash_uniq ON chunks (text_hash);
CREATE INDEX IF NOT EXISTS metrics_lookup_idx     ON metrics (bin, metric, period);

-- ---------------------------------------------------------------------------
-- Long-term memory. Survives restarts; written only with the user's consent.
-- user_ref is sha256(salt + user id): the raw id is never stored.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_profile (
    user_ref        TEXT PRIMARY KEY,
    consent         BOOLEAN     NOT NULL DEFAULT false,
    language        TEXT,
    -- company name -> how many questions asked about it. Drives "мои банки".
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
