---
type: Release Note
title: type packs are data, and the Python CLI is gone
---

# type packs are data, and the Python CLI is gone

Phase 6c of [RFC 0024](../../rfcs/0024-rust-native-core.md). A type pack is
now a directory with a `pack.json` manifest and the ordinary OKF files it
lists. `okf-parser packs` and `add-pack` run in the binary
(`okf_engine::packs`): the shipped `journalism` pack is embedded, and
`add-pack ./my-pack` installs any local pack directory. Output is unchanged,
a collision still writes nothing and now exits `1` from the binary, and a
path or symbolic link that would leave the destination is refused.

Breaking: the `okf_parser.packs` entry-point group and
`okf_parser.type_packs` are gone; publish a pack as a directory instead.
With the last delegated command native, `python -m okf_parser.cli` and the
cyclopts dependency (with rich, rich-rst and docstring-parser) leave.
