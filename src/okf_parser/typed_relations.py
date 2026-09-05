"""Expose RFC 0006 typed materialization as live in-process Ibis relations."""

from __future__ import annotations

from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import duckdb
import ibis

from okf_parser.bundle_relations import RELATIONS_SCHEMA, execute_bundle_relations
from okf_parser.typed_tables import discover_declared_schemas, materialize_typed_tables

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Self

    from ibis.backends.duckdb import Backend
    from ibis.expr.types import Table

    from okf_parser.bundle import Bundle
    from okf_parser.models import Violation

_TYPED_SCHEMA = "okf_types"


@dataclass(slots=True)
class TypedRelations:
    """Own one ephemeral typed DuckDB/Ibis projection of an OKF bundle.

    Use the object as a context manager when practical. Returned Ibis table
    expressions share this object's DuckDB connection and are valid until
    :meth:`close` is called.
    """

    _backend: Backend = field(repr=False)
    schema: str
    tables: tuple[str, ...]
    diagnostics: tuple[Violation, ...]
    relation_schema: str = RELATIONS_SCHEMA
    relation_names: tuple[str, ...] = ()
    _closed: bool = field(default=False, init=False, repr=False)

    def relation(self, concept_type: str) -> Table:
        """Return the typed Ibis relation for one declared concept type."""
        if self._closed:
            msg = "typed relations are closed"
            raise RuntimeError(msg)
        if concept_type not in self.tables:
            raise KeyError(concept_type)
        return self._backend.table(concept_type, database=self.schema)

    def bundle_relation(self, name: str) -> Table:
        """Return one producer-defined relation published under ``okf_relations``."""
        if self._closed:
            msg = "typed relations are closed"
            raise RuntimeError(msg)
        if name not in self.relation_names:
            raise KeyError(name)
        return self._backend.table(name, database=self.relation_schema)

    def __getitem__(self, concept_type: str) -> Table:
        """Return the typed Ibis relation for ``concept_type``."""
        return self.relation(concept_type)

    def __iter__(self) -> Iterator[str]:
        """Iterate declared concept type names in deterministic order."""
        return iter(self.tables)

    def __len__(self) -> int:
        """Return the number of compiled declared type relations."""
        return len(self.tables)

    def close(self) -> None:
        """Close the owned ephemeral DuckDB/Ibis backend once."""
        if self._closed:
            return
        self._backend.disconnect()
        self._closed = True

    def __enter__(self) -> Self:
        """Return this live relation set for context-manager use."""
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        """Close the ephemeral backend when leaving a context manager."""
        self.close()


def compile_bundle_types(
    bundle: Bundle,
    spec_template: str | None = None,
    *,
    relations: bool = False,
) -> TypedRelations:
    """Compile one loaded bundle's declared types into live Ibis relations.

    The implementation deliberately reuses the same declared-schema discovery
    and typed-table materializer as persistent DuckDB export. With no matching
    declarations, the result is a valid empty relation set rather than an
    independently inferred schema.

    When ``relations`` is true, an optional bundle-root ``okf.relations.sql``
    program executes after typed materialization in the same DuckDB connection.
    Relations it publishes under ``okf_relations`` are available through
    :meth:`TypedRelations.bundle_relation`.
    """
    declarations = discover_declared_schemas(bundle.root, bundle.concept_types, spec_template)
    with ExitStack() as stack:
        connection = stack.enter_context(closing(duckdb.connect()))
        materialized = materialize_typed_tables(
            connection,
            bundle,
            schema=_TYPED_SCHEMA,
            declarations=declarations,
            overwrite=False,
        )
        relation_catalog = (
            execute_bundle_relations(connection, bundle.root)
            if relations
            else None
        )
        backend = ibis.duckdb.from_connection(connection)
        stack.pop_all()
    return TypedRelations(
        _backend=backend,
        schema=materialized.schema,
        tables=materialized.tables,
        diagnostics=tuple(bundle.validate()),
        relation_schema=(relation_catalog.schema if relation_catalog is not None else RELATIONS_SCHEMA),
        relation_names=(relation_catalog.relations if relation_catalog is not None else ()),
    )
