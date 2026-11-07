-- Google Places policy permits storing Place IDs. Keep all other Google
-- Places fields live-only unless Google grants an explicit storage exception.
CREATE TABLE IF NOT EXISTS google_place_ids (
    place_id TEXT PRIMARY KEY,
    search_query VARCHAR(500) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
