# Codebase project manifest projection

The `codebase-to-okf` skill now includes standard Python project metadata in the one-shot projection when a PEP 621 `[project]` table is present in `pyproject.toml`.

- `CodeProject` preserves authored project identity and selected PEP 621 fields.
- `CodeDependency` preserves runtime and optional PEP 508 declarations with parsed navigation fields.
- dependency concepts use `manifest-declared` explicitly and do not claim installation, imports, reachability, or runtime use.
- the query recipe adds `--package` for compact dependency lookup.
- the normative finalizer documents both new producer-defined types through canonical `docs/types/{slug}.md` specs.
