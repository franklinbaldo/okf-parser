CREATE TABLE "BenchmarkRun" (
    run_id VARCHAR,
    task_id VARCHAR,
    tool_id VARCHAR,
    tool_package VARCHAR,
    tool_version VARCHAR,
    tool_executable VARCHAR,
    tool_used BOOLEAN,
    harness_id VARCHAR,
    harness_version VARCHAR,
    model VARCHAR,
    provider VARCHAR,
    repetition BIGINT,
    wall_seconds DOUBLE,
    budget_seconds BIGINT,
    input_tokens BIGINT,
    output_tokens BIGINT,
    total_tokens BIGINT,
    cost_usd DOUBLE,
    usage_available BOOLEAN,
    status VARCHAR,
    graded BOOLEAN,
    failure_class VARCHAR,
    answer VARCHAR,
    expected VARCHAR,
    started_at VARCHAR,
    finished_at VARCHAR,
    transcript_path VARCHAR,
    tool_log_path VARCHAR
);

COMMENT ON TABLE "BenchmarkRun" IS
    'Immutable OKF evidence for one agentic benchmark trial.';
COMMENT ON COLUMN "BenchmarkRun".input_tokens IS
    'Provider/harness-reported input token consumption when available.';
COMMENT ON COLUMN "BenchmarkRun".output_tokens IS
    'Provider/harness-reported output token consumption when available.';
COMMENT ON COLUMN "BenchmarkRun".total_tokens IS
    'Total token consumption when reported or derivable without estimation.';
COMMENT ON COLUMN "BenchmarkRun".cost_usd IS
    'Provider/harness-reported monetary cost when available.';
COMMENT ON COLUMN "BenchmarkRun".usage_available IS
    'Whether authoritative token usage was captured; false means unavailable, never zero-filled.';
