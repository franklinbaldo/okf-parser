---
type: Release Note
title: uv-first release validation
---

- Make `uv` the Python environment/install/runner path for release validation. The platform wheel smokes in both the release workflow and the release dry run now use `astral-sh/setup-uv`, `uv venv`, and `uv pip install`; release helpers that declare PEP 723 metadata (`check_no_duckdb_link.py`, `release_contract.py`, `native_from_wheel.py`, and `registry_state.py`) run through `uv run --script` instead of a bare Python interpreter. Regression tests lock this uv-first policy into both workflows.
- Stop depending on `rustup self uninstall` to prove that a missing wheel fails loudly. The public-index smoke now enforces `uv pip install --only-binary :all:`, which directly forbids a source fallback on every platform and avoids the macOS runner failure where Homebrew rustup refuses self-uninstallation.
- Replace the fixed `sleep 30` and auxiliary PyPI JSON polling with retry of the real consumer operation: `uv pip install --refresh-package okf-parser --only-binary :all: okf-parser==<version>`. The gate only advances when the public index actually serves a compatible wheel to the installer, eliminating the propagation race that hit 0.45.2.
- Keep the dry run semantically aligned with release: wheel-consumer installs are binary-only through uv, while the explicit sdist-consumer path remains allowed to build from source so that fallback is still tested deliberately rather than accidentally.
