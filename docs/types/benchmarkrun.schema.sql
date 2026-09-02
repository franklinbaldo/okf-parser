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
    setup_seconds DOUBLE,
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
    answer_sha256 VARCHAR,
    expected_sha256 VARCHAR,
    started_at VARCHAR,
    finished_at VARCHAR,
    answer_path VARCHAR,
    transcript_path VARCHAR,
    tool_log_path VARCHAR
);

COMMENT ON TABLE "BenchmarkRun" IS
    'Immutable OKF evidence for one agentic benchmark trial.';
COMMENT ON COLUMN "BenchmarkRun".setup_seconds IS
    'Tool installation/setup time, recorded separately from the agent wall-clock budget.';
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
COMMENT ON COLUMN "BenchmarkRun".answer_sha256 IS
    'SHA-256 of the canonical produced answer or output manifest.';
COMMENT ON COLUMN "BenchmarkRun".expected_sha256 IS
    'SHA-256 of the grader-derived oracle representation.';
