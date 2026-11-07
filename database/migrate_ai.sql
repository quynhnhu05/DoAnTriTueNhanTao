CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    place_id BIGINT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    event_type VARCHAR(20) NOT NULL CHECK (event_type IN ('view', 'save', 'like', 'dislike', 'book')),
    value NUMERIC,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feedback_user_created ON recommendation_feedback (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_place ON recommendation_feedback (place_id);
