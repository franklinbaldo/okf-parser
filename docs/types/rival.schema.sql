CREATE TABLE "Rival" (
    registry VARCHAR,
    package VARCHAR,
    executable VARCHAR,
    version_measured VARCHAR,
    surface VARCHAR[],
    homepage VARCHAR,
    measured BOOLEAN
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
    'The version the capability matrix last ran against, or null when unmeasured.';
COMMENT ON COLUMN "Rival".surface IS
    'The subcommands the tool advertises, transcribed from its own --help output.';
COMMENT ON COLUMN "Rival".homepage IS
    'Where the tool is developed, when it declares one.';
COMMENT ON COLUMN "Rival".measured IS
    'Whether benchmarks/capability_matrix.py interrogates this rival today.';
