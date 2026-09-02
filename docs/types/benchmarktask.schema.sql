CREATE TABLE "BenchmarkTask" (
    task_id VARCHAR,
    prompt VARCHAR,
    answer_kind VARCHAR,
    expected_bool BOOLEAN,
    expected_int BIGINT,
    expected_strings VARCHAR[]
);

COMMENT ON TABLE "BenchmarkTask" IS
    'A deterministic problem handed to the agentic benchmark harness.';
COMMENT ON COLUMN "BenchmarkTask".task_id IS
    'Stable identifier used in run evidence and reports.';
COMMENT ON COLUMN "BenchmarkTask".prompt IS
    'Problem statement handed unchanged to every tool condition.';
COMMENT ON COLUMN "BenchmarkTask".answer_kind IS
    'Grading representation: bool, int, strings, cycles or counts.';
COMMENT ON COLUMN "BenchmarkTask".expected_bool IS
    'Expected boolean answer when answer_kind is bool.';
COMMENT ON COLUMN "BenchmarkTask".expected_int IS
    'Expected integer answer when answer_kind is int.';
COMMENT ON COLUMN "BenchmarkTask".expected_strings IS
    'Canonical string representation for list, cycle and count answers.';
