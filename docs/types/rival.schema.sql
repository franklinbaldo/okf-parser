CREATE TABLE "Rival" (
    registry VARCHAR,
    package VARCHAR,
    executable VARCHAR,
    version_measured VARCHAR,
    surface VARCHAR[],
    homepage VARCHAR,
    measured BOOLEAN,
    agentic_enabled BOOLEAN,
    agentic_version VARCHAR,
    agentic_executable VARCHAR,
    agentic_instruction VARCHAR
);

COMMENT ON TABLE "Rival" IS
    'A published tool that reads Open Knowledge Format bundles, recorded so the '
    'ecosystem this project competes in is queryable rather than remembered.';
COMMENT ON COLUMN "Rival".registry IS
    'The index the package is published to: pypi or npm.';
COMMENT ON COLUMN "Rival".package IS
    'The distribution name as published, which is not always the command name.';
COMMENT ON COLUMN "Rival".executable IS
    'The command the package installs. Two rivals may claim the same one.';
COMMENT ON COLUMN "Rival".version_measured IS
    'The version the direct capability matrix last ran against, or null when unmeasured.';
COMMENT ON COLUMN "Rival".surface IS
    'The subcommands the tool advertises, transcribed from its own --help output.';
COMMENT ON COLUMN "Rival".homepage IS
    'Where the tool is developed, when it declares one.';
COMMENT ON COLUMN "Rival".measured IS
    'Whether benchmarks/capability_matrix.py interrogates this rival today.';
COMMENT ON COLUMN "Rival".agentic_enabled IS
    'Whether the rival participates in the current agentic benchmark registry.';
COMMENT ON COLUMN "Rival".agentic_version IS
    'Pinned package version used by the current agentic benchmark round.';
COMMENT ON COLUMN "Rival".agentic_executable IS
    'Executable the agentic runner must expose and observe for this rival.';
COMMENT ON COLUMN "Rival".agentic_instruction IS
    'Tool-specific constraint appended to the otherwise identical agent handoff.';
