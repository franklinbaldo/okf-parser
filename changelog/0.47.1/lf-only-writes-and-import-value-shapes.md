---
type: Release Note
title: Every write path is LF-only, and import preserves list/dict/number shape
---

# Every write path is LF-only, and import preserves list/dict/number shape

Fixed two reported bugs:

- `import`, `format --write`, and `init`'s specification scaffolders wrote
  through `Path.write_text` without `newline="\n"`, so on Windows every `\n`
  got translated to `os.linesep` (`\r\n`). `apply`/`edit` already avoided this
  because they write bytes directly, so the same bundle could end up mixing
  LF and CRLF depending on which command touched a file. All four write sites
  now pass `newline="\n"` explicitly.
- `import` collapsed every non-string source value through `str(value)`, so a
  `LIST`/`STRUCT` source column (Parquet, DuckDB) came out as a Python `repr`
  string (`pastas: "['u1', 'u2']"`) instead of a real YAML sequence or
  mapping, and plain numbers/booleans came out quoted. Lists and structs now
  serialize as YAML sequences/mappings, and numbers/booleans as native YAML
  scalars.
