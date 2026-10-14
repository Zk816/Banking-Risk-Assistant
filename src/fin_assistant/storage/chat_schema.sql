
CREATE TABLE IF NOT EXISTS "User" (
    id           TEXT PRIMARY KEY,
    identifier   TEXT UNIQUE NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}',
    "createdAt"  TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updatedAt"  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "Thread" (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    "userId"     TEXT REFERENCES "User"(id) ON DELETE CASCADE,
    tags         TEXT[] NOT NULL DEFAULT '{}',
    metadata     JSONB NOT NULL DEFAULT '{}',
    "createdAt"  TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updatedAt"  TIMESTAMPTZ NOT NULL DEFAULT now(),
    "deletedAt"  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS thread_user_updated_idx ON "Thread" ("userId", "updatedAt" DESC);

CREATE TABLE IF NOT EXISTS "Step" (
    id           TEXT PRIMARY KEY,
    "threadId"   TEXT REFERENCES "Thread"(id) ON DELETE CASCADE,
    "parentId"   TEXT,
    input        TEXT,
    output       TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}',
    name         TEXT,
    type         TEXT,
    "showInput"  TEXT DEFAULT 'json',
    "isError"    BOOLEAN DEFAULT false,
    "startTime"  TIMESTAMPTZ,
    "endTime"    TIMESTAMPTZ,
    "createdAt"  TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updatedAt"  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS step_thread_idx ON "Step" ("threadId");

CREATE TABLE IF NOT EXISTS "Element" (
    id            TEXT PRIMARY KEY,
    "threadId"    TEXT REFERENCES "Thread"(id) ON DELETE CASCADE,
    "stepId"      TEXT,
    metadata      JSONB NOT NULL DEFAULT '{}',
    mime          TEXT,
    name          TEXT,
    "objectKey"   TEXT,
    url           TEXT,
    "chainlitKey" TEXT,
    display       TEXT,
    size          TEXT,
    language      TEXT,
    page          INT,
    props         JSONB,
    "createdAt"   TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updatedAt"   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS "Feedback" (
    id           TEXT PRIMARY KEY,
    "stepId"     TEXT,
    name         TEXT NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    comment      TEXT,
    "createdAt"  TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updatedAt"  TIMESTAMPTZ NOT NULL DEFAULT now()
);
