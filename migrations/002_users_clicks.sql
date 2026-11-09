-- Applied automatically (alphabetical order) when the Postgres volume is first created.

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL,           -- stored lower-cased
    password_hash TEXT        NOT NULL,           -- argon2 hash, never the password
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users (email);

-- Deleting a user deletes their links (and, below, those links' clicks).
ALTER TABLE links
    ADD CONSTRAINT links_user_fk FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS clicks (
    id         BIGSERIAL PRIMARY KEY,
    link_id    BIGINT      NOT NULL REFERENCES links (id) ON DELETE CASCADE,
    clicked_at TIMESTAMPTZ NOT NULL,
    referrer   TEXT,                              -- host only, e.g. "twitter.com"
    user_agent TEXT,
    device     TEXT,                              -- desktop / mobile / tablet / bot
    ip_hash    TEXT                               -- keyed hash, not the raw IP
);
-- Every stats query is "clicks for THIS link in THIS time range".
CREATE INDEX IF NOT EXISTS clicks_link_time_idx ON clicks (link_id, clicked_at);
