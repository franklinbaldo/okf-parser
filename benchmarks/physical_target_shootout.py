# /// script
# requires-python = ">=3.12"
# ///
"""Compare workload-specific SQLite, Parquet, and Arrow IPC materializations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import ipc

from okf_parser.materialization import materialize_sqlite_hot

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_QUERY_COUNT = 1_000
_ROUNDS = 5
_WARMUPS = 1


@dataclass(frozen=True)
class PhysicalFiles:
    """Files produced by one physical-materialization pass."""

    sqlite: Path
    parquet_concepts: Path
    parquet_links: Path
    arrow_concepts: Path
    arrow_links: Path


@dataclass
class ArrowTarget:
    """Opened Arrow IPC target plus its DuckDB query bridge."""

    connection: duckdb.DuckDBPyConnection
    concepts: pa.Table
    links: pa.Table
    concept_source: pa.MemoryMappedFile
    link_source: pa.MemoryMappedFile

    def close(self) -> None:
        """Close query bridge and mapped files."""
        self.connection.close()
        self.concept_source.close()
        self.link_source.close()


@dataclass
class OpenTargets:
    """Opened physical targets used for parity and timing."""

    sqlite: sqlite3.Connection
    parquet: duckdb.DuckDBPyConnection
    arrow: ArrowTarget

    def close(self) -> None:
        """Close every physical target."""
        self.arrow.close()
        self.parquet.close()
        self.sqlite.close()


def _source(documents: int) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf")
    connection.execute(
        "CREATE TABLE okf.concepts AS SELECT "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS concept_id, "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS logical_key, "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') || '.md' AS path, "
        "'Type' || CAST(i % 8 AS VARCHAR) AS concept_type, "
        "'Title ' || CAST(i AS VARCHAR) AS title, "
        "CASE WHEN i % 3 = 0 THEN 'Description ' || CAST(i AS VARCHAR) ELSE NULL END "
        "AS description, "
        "'sha256:' || CAST(i AS VARCHAR) AS source_digest, "
        "'parsed:' || CAST(i AS VARCHAR) AS parsed_digest "
        "FROM range(?) t(i)",
        [documents],
    )
    connection.execute(
        "CREATE TABLE okf.links AS SELECT "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS source_id, "
        "'concept-' || lpad(CAST((i + delta) % ? AS VARCHAR), 8, '0') || '.md' AS raw_target, "
        "'concept-' || lpad(CAST((i + delta) % ? AS VARCHAR), 8, '0') AS target_id, "
        "true AS exists, CASE WHEN delta = 1 THEN 'body' ELSE 'frontmatter' END AS origin "
        "FROM range(?) t(i), (VALUES (1), (2)) d(delta)",
        [documents, documents, documents],
    )
    return connection


def _keys(documents: int) -> list[str]:
    stride = max(1, documents // _QUERY_COUNT)
    return [f"concept-{(index * stride) % documents:08d}" for index in range(_QUERY_COUNT)]


def _median_ns(operation: Callable[[], object], *, rounds: int = _ROUNDS) -> int:
    for _ in range(_WARMUPS):
        operation()
    samples: list[int] = []
    for _ in range(rounds):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return int(statistics.median(samples))


def _median_ns_per_query(operation: Callable[[str], object], keys: Sequence[str]) -> int:
    operation(keys[0])
    samples: list[int] = []
    for _ in range(_ROUNDS):
        started = time.perf_counter_ns()
        for key in keys:
            operation(key)
        samples.append((time.perf_counter_ns() - started) // len(keys))
    return int(statistics.median(samples))


def _duck_point(connection: duckdb.DuckDBPyConnection, table: str, key: str) -> object:
    return connection.execute(
        f"SELECT path FROM {table} WHERE concept_id = ?",  # noqa: S608 -- fixed benchmark tables.
        [key],
    ).fetchone()


def _duck_outgoing(connection: duckdb.DuckDBPyConnection, table: str, key: str) -> object:
    return connection.execute(
        f"SELECT target_id FROM {table} WHERE source_id = ? ORDER BY target_id",  # noqa: S608
        [key],
    ).fetchall()


def _duck_scan(connection: duckdb.DuckDBPyConnection, table: str) -> object:
    return connection.execute(
        f"SELECT concept_type, COUNT(*), SUM(length(title)) FROM {table} "  # noqa: S608
        "GROUP BY concept_type ORDER BY concept_type"
    ).fetchall()


def _duck_projection(connection: duckdb.DuckDBPyConnection, table: str) -> object:
    return connection.execute(
        f"SELECT concept_id, title FROM {table} "  # noqa: S608
        "WHERE concept_type = 'Type3' ORDER BY concept_id LIMIT 1000"
    ).fetchall()


def _sqlite_point(connection: sqlite3.Connection, key: str) -> object:
    return connection.execute("SELECT path FROM concepts WHERE concept_id = ?", (key,)).fetchone()


def _sqlite_outgoing(connection: sqlite3.Connection, key: str) -> object:
    return connection.execute(
        "SELECT target_id FROM links WHERE source_id = ? ORDER BY target_id", (key,)
    ).fetchall()


def _sqlite_scan(connection: sqlite3.Connection) -> object:
    return connection.execute(
        "SELECT concept_type, COUNT(*), SUM(length(title)) FROM concepts "
        "GROUP BY concept_type ORDER BY concept_type"
    ).fetchall()


def _sqlite_projection(connection: sqlite3.Connection) -> object:
    return connection.execute(
        "SELECT concept_id, title FROM concepts "
        "WHERE concept_type = 'Type3' ORDER BY concept_id LIMIT 1000"
    ).fetchall()


def _require_equal(label: str, expected: object, actual: object) -> None:
    if actual != expected:
        msg = f"{label} parity failure: expected {expected!r}, got {actual!r}"
        raise RuntimeError(msg)


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _materialize_parquet(source: duckdb.DuckDBPyConnection, directory: Path) -> tuple[Path, Path]:
    concepts = directory / "concepts.parquet"
    links = directory / "links.parquet"
    source.execute(
        f"COPY okf.concepts TO '{_sql_path(concepts)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    source.execute(f"COPY okf.links TO '{_sql_path(links)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return concepts, links


def _open_parquet(concepts: Path, links: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute(
        f"CREATE VIEW concepts AS SELECT * FROM read_parquet('{_sql_path(concepts)}')"  # noqa: S608
    )
    connection.execute(
        f"CREATE VIEW links AS SELECT * FROM read_parquet('{_sql_path(links)}')"  # noqa: S608
    )
    return connection


def _write_ipc(table: pa.Table, path: Path) -> None:
    with pa.OSFile(str(path), "wb") as sink, ipc.new_file(sink, table.schema) as writer:
        writer.write_table(table)


def _materialize_arrow(source: duckdb.DuckDBPyConnection, directory: Path) -> tuple[Path, Path]:
    concepts = directory / "concepts.arrow"
    links = directory / "links.arrow"
    _write_ipc(source.execute("SELECT * FROM okf.concepts").to_arrow_table(), concepts)
    _write_ipc(source.execute("SELECT * FROM okf.links").to_arrow_table(), links)
    return concepts, links


def _open_arrow(concepts: Path, links: Path) -> ArrowTarget:
    concept_source = pa.memory_map(str(concepts), "r")
    link_source = pa.memory_map(str(links), "r")
    concept_table = ipc.open_file(concept_source).read_all()
    link_table = ipc.open_file(link_source).read_all()
    connection = duckdb.connect()
    connection.register("concepts", concept_table)
    connection.register("links", link_table)
    return ArrowTarget(connection, concept_table, link_table, concept_source, link_source)


def _materialize_all(
    source: duckdb.DuckDBPyConnection, directory: Path
) -> tuple[PhysicalFiles, dict[str, int]]:
    sqlite_path = directory / "hot.sqlite"
    started = time.perf_counter_ns()
    materialize_sqlite_hot(source, sqlite_path)
    sqlite_ns = time.perf_counter_ns() - started

    started = time.perf_counter_ns()
    parquet_concepts, parquet_links = _materialize_parquet(source, directory)
    parquet_ns = time.perf_counter_ns() - started

    started = time.perf_counter_ns()
    arrow_concepts, arrow_links = _materialize_arrow(source, directory)
    arrow_ns = time.perf_counter_ns() - started

    files = PhysicalFiles(
        sqlite_path,
        parquet_concepts,
        parquet_links,
        arrow_concepts,
        arrow_links,
    )
    return files, {"sqlite": sqlite_ns, "parquet": parquet_ns, "arrow": arrow_ns}


def _open_all(files: PhysicalFiles) -> tuple[OpenTargets, dict[str, int]]:
    started = time.perf_counter_ns()
    sqlite = sqlite3.connect(files.sqlite)
    sqlite_ns = time.perf_counter_ns() - started

    started = time.perf_counter_ns()
    parquet = _open_parquet(files.parquet_concepts, files.parquet_links)
    parquet_ns = time.perf_counter_ns() - started

    started = time.perf_counter_ns()
    arrow = _open_arrow(files.arrow_concepts, files.arrow_links)
    arrow_ns = time.perf_counter_ns() - started

    return OpenTargets(sqlite, parquet, arrow), {
        "sqlite": sqlite_ns,
        "parquet": parquet_ns,
        "arrow": arrow_ns,
    }


def _validate_parity(source: duckdb.DuckDBPyConnection, targets: OpenTargets, sample: str) -> None:
    expected_point = _duck_point(source, "okf.concepts", sample)
    expected_outgoing = _duck_outgoing(source, "okf.links", sample)
    expected_scan = _duck_scan(source, "okf.concepts")
    expected_projection = _duck_projection(source, "okf.concepts")

    _require_equal("sqlite point", expected_point, _sqlite_point(targets.sqlite, sample))
    _require_equal(
        "parquet point", expected_point, _duck_point(targets.parquet, "concepts", sample)
    )
    _require_equal(
        "arrow point", expected_point, _duck_point(targets.arrow.connection, "concepts", sample)
    )
    _require_equal("sqlite outgoing", expected_outgoing, _sqlite_outgoing(targets.sqlite, sample))
    _require_equal(
        "parquet outgoing", expected_outgoing, _duck_outgoing(targets.parquet, "links", sample)
    )
    _require_equal(
        "arrow outgoing",
        expected_outgoing,
        _duck_outgoing(targets.arrow.connection, "links", sample),
    )
    _require_equal("sqlite scan", expected_scan, _sqlite_scan(targets.sqlite))
    _require_equal("parquet scan", expected_scan, _duck_scan(targets.parquet, "concepts"))
    _require_equal("arrow scan", expected_scan, _duck_scan(targets.arrow.connection, "concepts"))
    _require_equal("sqlite projection", expected_projection, _sqlite_projection(targets.sqlite))
    _require_equal(
        "parquet projection", expected_projection, _duck_projection(targets.parquet, "concepts")
    )
    _require_equal(
        "arrow projection",
        expected_projection,
        _duck_projection(targets.arrow.connection, "concepts"),
    )


def _measure_point(
    source: duckdb.DuckDBPyConnection, targets: OpenTargets, keys: Sequence[str]
) -> dict[str, int]:
    return {
        "duckdb": _median_ns_per_query(lambda key: _duck_point(source, "okf.concepts", key), keys),
        "sqlite": _median_ns_per_query(lambda key: _sqlite_point(targets.sqlite, key), keys),
        "parquet": _median_ns_per_query(
            lambda key: _duck_point(targets.parquet, "concepts", key), keys
        ),
        "arrow": _median_ns_per_query(
            lambda key: _duck_point(targets.arrow.connection, "concepts", key), keys
        ),
    }


def _measure_outgoing(
    source: duckdb.DuckDBPyConnection, targets: OpenTargets, keys: Sequence[str]
) -> dict[str, int]:
    return {
        "duckdb": _median_ns_per_query(lambda key: _duck_outgoing(source, "okf.links", key), keys),
        "sqlite": _median_ns_per_query(lambda key: _sqlite_outgoing(targets.sqlite, key), keys),
        "parquet": _median_ns_per_query(
            lambda key: _duck_outgoing(targets.parquet, "links", key), keys
        ),
        "arrow": _median_ns_per_query(
            lambda key: _duck_outgoing(targets.arrow.connection, "links", key), keys
        ),
    }


def _measure_scan(source: duckdb.DuckDBPyConnection, targets: OpenTargets) -> dict[str, int]:
    return {
        "duckdb": _median_ns(lambda: _duck_scan(source, "okf.concepts")),
        "sqlite": _median_ns(lambda: _sqlite_scan(targets.sqlite)),
        "parquet": _median_ns(lambda: _duck_scan(targets.parquet, "concepts")),
        "arrow": _median_ns(lambda: _duck_scan(targets.arrow.connection, "concepts")),
    }


def _measure_projection(source: duckdb.DuckDBPyConnection, targets: OpenTargets) -> dict[str, int]:
    return {
        "duckdb": _median_ns(lambda: _duck_projection(source, "okf.concepts")),
        "sqlite": _median_ns(lambda: _sqlite_projection(targets.sqlite)),
        "parquet": _median_ns(lambda: _duck_projection(targets.parquet, "concepts")),
        "arrow": _median_ns(lambda: _duck_projection(targets.arrow.connection, "concepts")),
    }


def _parquet_interchange_load(files: PhysicalFiles) -> int:
    return _median_ns(
        lambda: (pq.read_table(files.parquet_concepts), pq.read_table(files.parquet_links))
    )


def _arrow_interchange_load(files: PhysicalFiles) -> int:
    def load() -> tuple[pa.Table, pa.Table]:
        with (
            pa.memory_map(str(files.arrow_concepts), "r") as concept_source,
            pa.memory_map(str(files.arrow_links), "r") as link_source,
        ):
            return (
                ipc.open_file(concept_source).read_all(),
                ipc.open_file(link_source).read_all(),
            )

    return _median_ns(load)


def _sizes(files: PhysicalFiles) -> dict[str, int]:
    return {
        "sqlite": files.sqlite.stat().st_size,
        "parquet": files.parquet_concepts.stat().st_size + files.parquet_links.stat().st_size,
        "arrow": files.arrow_concepts.stat().st_size + files.arrow_links.stat().st_size,
    }


def _case(documents: int) -> dict[str, Any]:
    source = _source(documents)
    try:
        source.execute("INSTALL sqlite")
        source.execute("LOAD sqlite")
        keys = _keys(documents)
        with tempfile.TemporaryDirectory(prefix="okf-targets-") as directory_name:
            files, build_ns = _materialize_all(source, Path(directory_name))
            targets, open_ns = _open_all(files)
            try:
                _validate_parity(source, targets, keys[len(keys) // 2])
                return {
                    "documents": documents,
                    "links": documents * 2,
                    "build_ns": build_ns,
                    "open_ns": open_ns,
                    "bytes": _sizes(files),
                    "point_ns_per_query": _measure_point(source, targets, keys),
                    "outgoing_ns_per_query": _measure_outgoing(source, targets, keys),
                    "scan_ns": _measure_scan(source, targets),
                    "projection_ns": _measure_projection(source, targets),
                    "interchange_load_ns": {
                        "parquet": _parquet_interchange_load(files),
                        "arrow": _arrow_interchange_load(files),
                    },
                }
            finally:
                targets.close()
    finally:
        source.close()


def main() -> None:
    """Run the fixed workload matrix and emit benchmark JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, nargs="+", default=[1_000, 10_000, 50_000])
    args = parser.parse_args()
    payload = {
        "benchmark": "physical-target-shootout-v1",
        "query_count": _QUERY_COUNT,
        "rounds": _ROUNDS,
        "warmups": _WARMUPS,
        "targets": {
            "duckdb": "canonical in-memory query baseline",
            "sqlite": "indexed point/adjacency target",
            "parquet": "zstd persisted analytical target queried through DuckDB",
            "arrow": "IPC mmap interchange target queried through DuckDB",
        },
        "cases": [_case(documents) for documents in args.documents],
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
