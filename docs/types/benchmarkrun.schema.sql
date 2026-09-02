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
