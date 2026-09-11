"""Application-facing bundle loading and validation with automatic engine selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from okf_parser.bundle import load_bundle as _load_bundle_native
from okf_parser.models import ValidationReport
from okf_parser.relational_schema import validate_relations
from okf_parser.rust_core import resolve_rust_core
from okf_parser.type_specs import missing_type_specs

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from okf_parser.bundle import Bundle
    from okf_parser.rust_core import EngineMode


def load_bundle(
    root: Path,
    exclude: Sequence[str] = (),
    *,
    engine: EngineMode = "auto",
    rust_core: Path | None = None,
) -> Bundle:
    """Load a bundle with the best available compatible engine."""
    executable = resolve_rust_core(engine=engine, explicit=rust_core)
    return _load_bundle_native(root, exclude, rust_core=executable)


def validate_path(
    path: Path,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
    relational_schema: Path | None = None,
    engine: EngineMode = "auto",
    rust_core: Path | None = None,
) -> ValidationReport:
    """Validate a bundle using the same automatic engine policy as ``load_bundle``.

    Validation is intentionally layered on the loaded ``Bundle``. Discovery,
    parsing, link resolution and the base diagnostics can therefore use the
    release-matched Rust engine when available, while relation/type-spec rules
    remain ordinary Python consumers of the resulting relational bundle.
    """
    bundle = load_bundle(path, exclude, engine=engine, rust_core=rust_core)
    diagnostics = list(bundle.diagnostics)
    if relational_schema is not None:
        schema_path = (
            relational_schema
            if relational_schema.is_absolute()
            else bundle.root / relational_schema
        )
        diagnostics.extend(validate_relations(bundle, schema_path))
    if require_spec is not None:
        diagnostics.extend(
            missing_type_specs(
                bundle.root,
                bundle.concept_types,
                require_spec,
                normative=normative_spec,
            )
        )
    return ValidationReport(
        root=bundle.root,
        markdown_count=bundle.markdown_count,
        concept_count=cast("int", bundle.concepts.count().execute()),
        reserved_count=cast("int", bundle.reserved.count().execute()),
        violations=tuple(sorted(diagnostics, key=lambda item: (item.path, item.code, item.message))),
    )
