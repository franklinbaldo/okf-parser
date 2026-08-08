CREATE TABLE "Parity" (
    amount DECIMAL(18, 4),
    amounts DECIMAL(18, 4)[],
    local_at TIMESTAMP,
    zoned_at TIMESTAMPTZ,
    external_id UUID
);

COMMENT ON TABLE "Parity" IS 'Shared declared parity fixture.';
COMMENT ON COLUMN "Parity".amount IS 'Monetary amount.';
COMMENT ON COLUMN "Parity".local_at IS 'Local wall-clock timestamp.';
