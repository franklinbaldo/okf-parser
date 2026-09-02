CREATE TABLE "BenchmarkTask" (
    task_id VARCHAR,
    prompt VARCHAR,
    answer_kind VARCHAR,
    fixture_kind VARCHAR,
    fixture_size BIGINT,
    grader VARCHAR
);

COMMENT ON TABLE "BenchmarkTask" IS
    'A deterministic large-scale problem handed to the agentic benchmark harness.';
COMMENT ON COLUMN "BenchmarkTask".task_id IS
    'Stable identifier used in run evidence and reports.';
COMMENT ON COLUMN "BenchmarkTask".prompt IS
    'Problem statement handed unchanged across rival conditions.';
COMMENT ON COLUMN "BenchmarkTask".answer_kind IS
    'Shape of the expected deliverable: scalar, lines or artifact.';
COMMENT ON COLUMN "BenchmarkTask".fixture_kind IS
    'Named deterministic fixture generator used for this task.';
COMMENT ON COLUMN "BenchmarkTask".fixture_size IS
    'Primary scale parameter; first-round tasks should be materially non-trivial.';
COMMENT ON COLUMN "BenchmarkTask".grader IS
    'Named deterministic grader that derives its oracle from the generated fixture.';
