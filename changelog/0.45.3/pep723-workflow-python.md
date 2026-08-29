---
type: Release Note
title: PEP 723 for the workflows' inline Python
---

- Finish the PEP 723 migration in the workflows themselves. Standalone Python that lived as `python -c` and `python - <<PY` heredocs became three declared helpers -- `scripts/project_version.py`, `scripts/verify_wheel_scripts.py` and `scripts/release_summary.py` -- each run through `uv run --script`. The project version had five inline copies reaching for the runner's ambient interpreter, the one-unified-executable wheel check had two, and the dry run's job summary could only ever be exercised by pushing to CI. All three now have unit tests, run off CI against a downloaded release tree, and are covered by the workflow policy tests, which reject a return to inline Python. The one surviving `python -c` reads `platform.python_version()` for the manifest's build provenance, where an ephemeral script interpreter would report the wrong number.
