---
type: Release Note
title: load_bundle, check_report and validate_path accept text paths
---

# load_bundle, check_report and validate_path accept text paths

`load_bundle`, `check_report` and `validate_path` take `str | os.PathLike[str]`
and normalise with `Path(...).resolve()`. A `str` path used to fail with
`AttributeError: 'str' object has no attribute 'resolve'`.
