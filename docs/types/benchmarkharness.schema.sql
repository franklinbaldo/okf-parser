CREATE TABLE "BenchmarkHarness" (
    harness_id VARCHAR,
    package VARCHAR,
    version VARCHAR,
    provider VARCHAR,
    enabled BOOLEAN
);

COMMENT ON TABLE "BenchmarkHarness" IS
    'Pinned agent shell configuration for one agentic benchmark round.';
