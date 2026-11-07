BEGIN;
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_source_url TEXT;
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_checked_at TIMESTAMPTZ;
ALTER TABLE places ADD COLUMN IF NOT EXISTS price_kind VARCHAR(40);

-- Never expose seeded/estimated prices as live prices. Any current price that
-- lacks both a source URL and a check timestamp is unverified, regardless of
-- which collector originally wrote it.
UPDATE places
SET price = NULL,
    price_min = NULL,
    price_max = NULL,
    currency = NULL,
    price_kind = NULL
WHERE price_source_url IS NULL
   OR price_checked_at IS NULL;
COMMIT;
