-- Runs automatically the first time the Postgres container starts
-- (mounted into /docker-entrypoint-initdb.d).

-- The sequence is the source of unique IDs. nextval() is atomic, so even with
-- many API workers inserting at once, no two links ever get the same number.
CREATE SEQUENCE IF NOT EXISTS links_id_seq;

CREATE TABLE IF NOT EXISTS links (
    id           BIGINT PRIMARY KEY,                 -- value from links_id_seq
    code         TEXT        NOT NULL,               -- the short code, e.g. "k3Fz9Qa"
    original_url TEXT        NOT NULL,
    user_id      BIGINT,                             -- NULL = anonymous (FK added with auth)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ                         -- NULL = never expires
);

-- Every redirect looks a link up by code, so this unique index is THE hot path.
-- UNIQUE also protects custom aliases against duplicates, even under races.
CREATE UNIQUE INDEX IF NOT EXISTS links_code_key ON links (code);

-- "Show me my links" (added with auth) will filter by user.
CREATE INDEX IF NOT EXISTS links_user_idx ON links (user_id, created_at DESC);
