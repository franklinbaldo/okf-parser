"""Benchmark DuckDB-to-SQLite physical materialization and lookup break-even."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from okf_parser.materialization import materialize_sqlite_hot, open_sqlite_memory_copy

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_QUERY_COUNT = 1_000


def _source(documents: int) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA okf")
    connection.execute(
        "CREATE TABLE okf.concepts AS SELECT "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS concept_id, "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS logical_key, "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') || '.md' AS path, "
        "'Node' AS concept_type, "
        "'Title ' || CAST(i AS VARCHAR) AS title, "
        "NULL::VARCHAR AS description, "
        "'sha256:' || CAST(i AS VARCHAR) AS source_digest, "
        "'parsed:' || CAST(i AS VARCHAR) AS parsed_digest "
        "FROM range(?) t(i)",
        [documents],
    )
    connection.execute(
        "CREATE TABLE okf.links AS "
        "SELECT "
        "'concept-' || lpad(CAST(i AS VARCHAR), 8, '0') AS source_id, "
        "'concept-' || lpad(CAST((i + delta) % ? AS VARCHAR), 8, '0') || '.md' AS raw_target, "
        "'concept-' || lpad(CAST((i + delta) % ? AS VARCHAR), 8, '0') AS target_id, "
        "true AS exists, 'body' AS origin "
        "FROM range(?) t(i), (VALUES (1), (2)) d(delta)",
        [documents, documents, documents],
    )
    return connection


def _keys(documents: int) -> list[str]:
    stride = max(1, documents // _QUERY_COUNT)
    return [f"concept-{(index * stride) % documents:08d}" for index in range(_QUERY_COUNT)]


def _median_ns_per_query(operation: Callable[[str], object], keys: Sequence[str]) -> int:
    samples: list[int] = []
    operation(keys[0])
    for _ in range(5):
        started = time.perf_counter_ns()
        for key in keys:
            operation(key)
        samples.append((time.perf_counter_ns() - started) // len(keys))
    return int(statistics.median(samples))


def _duck_point(connection: duckdb.DuckDBPyConnection, key: str) -> object:
    return connection.execute(
        "SELECT path FROM okf.concepts WHERE concept_id = ?", [key]
    ).fetchone()


def _duck_outgoing(connection: duckdb.DuckDBPyConnection, key: str) -> object:
    return connection.execute(
        "SELECT target_id FROM okf.links WHERE source_id = ? ORDER BY target_id", [key]
    ).fetchall()


def _sqlite_point(connection: sqlite3.Connection, key: str) -> object:
    return connection.execute("SELECT path FROM concepts WHERE concept_id = ?", (key,)).fetchone()


def _sqlite_outgoing(connection: sqlite3.Connection, key: str) -> object:
    return connection.execute(
        "SELECT target_id FROM links WHERE source_id = ? ORDER BY target_id", (key,)
    ).fetchall()


def _require_equal(label: str, expected: object, actual: object) -> None:
    if actual != expected:
        msg = f"{label} parity failure: expected {expected!r}, got {actual!r}"
        raise RuntimeError(msg)


def _break_even(build_ns: int, baseline_ns: int, accelerated_ns: int) -> int | None:
    saving = baseline_ns - accelerated_ns
    if saving <= 0:
        return None
    return (build_ns + saving - 1) // saving


def _case(documents: int) -> dict[str, Any]:
    source = _source(documents)
    source.execute("INSTALL sqlite")
    source.execute("LOAD sqlite")
    keys = _keys(documents)
    with tempfile.TemporaryDirectory(prefix="okf-hot-") as directory:
        destination = Path(directory) / "hot.sqlite"
        started = time.perf_counter_ns()
        materialize_sqlite_hot(source, destination)
        export_ns = time.perf_counter_ns() - started

        file_db = sqlite3.connect(destination)
        started = time.perf_counter_ns()
        memory_db = open_sqlite_memory_copy(destination)
        memory_copy_ns = time.perf_counter_ns() - started
        try:
            sample = keys[len(keys) // 2]
            expected_point = _duck_point(source, sample)
            expected_outgoing = _duck_outgoing(source, sample)
            _require_equal("file point", expected_point, _sqlite_point(file_db, sample))
            _require_equal("memory point", expected_point, _sqlite_point(memory_db, sample))
            _require_equal("file outgoing", expected_outgoing, _sqlite_outgoing(file_db, sample))
            _require_equal(
                "memory outgoing", expected_outgoing, _sqlite_outgoing(memory_db, sample)
            )

            duck_point = _median_ns_per_query(lambda key: _duck_point(source, key), keys)
            file_point = _median_ns_per_query(lambda key: _sqlite_point(file_db, key), keys)
            memory_point = _median_ns_per_query(lambda key: _sqlite_point(memory_db, key), keys)
            duck_outgoing = _median_ns_per_query(lambda key: _duck_outgoing(source, key), keys)
            file_outgoing = _median_ns_per_query(lambda key: _sqlite_outgoing(file_db, key), keys)
            memory_outgoing = _median_ns_per_query(
                lambda key: _sqlite_outgoing(memory_db, key), keys
            )
        finally:
            memory_db.close()
            file_db.close()
            source.close()

    return {
        "documents": documents,
        "links": documents * 2,
        "export_ns": export_ns,
        "memory_copy_ns": memory_copy_ns,
        "duckdb_point_ns": duck_point,
        "sqlite_file_point_ns": file_point,
        "sqlite_memory_point_ns": memory_point,
        "duckdb_outgoing_ns": duck_outgoing,
        "sqlite_file_outgoing_ns": file_outgoing,
        "sqlite_memory_outgoing_ns": memory_outgoing,
        "file_point_break_even_queries": _break_even(export_ns, duck_point, file_point),
        "memory_point_break_even_queries": _break_even(
            export_ns + memory_copy_ns, duck_point, memory_point
        ),
        "file_outgoing_break_even_queries": _break_even(export_ns, duck_outgoing, file_outgoing),
        "memory_outgoing_break_even_queries": _break_even(
            export_ns + memory_copy_ns, duck_outgoing, memory_outgoing
        ),
    }


def main() -> None:
    """Run the fixed synthetic matrix and emit machine-readable benchmark JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, nargs="+", default=[1_000, 10_000, 50_000])
    args = parser.parse_args()
    payload = {
        "benchmark": "duckdb-sqlite-hotpath-v1",
        "query_count": _QUERY_COUNT,
        "cases": [_case(documents) for documents in args.documents],
    }
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
