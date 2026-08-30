---
type: Release Note
title: self-contained codebase-to-OKF recipe skill
---

- Add RFC 0019, keeping source-specific extraction out of `okf-parser` core and defining repository recipe skills as the preferred boundary for deterministic source-to-OKF adapters.
- Add `skills/codebase-to-okf/SKILL.md` with an executable PEP 723 Python recipe embedded directly in the skill. The reference frontend projects Python modules, classes, functions, methods and imports into a deterministic derived OKF bundle, then validates it through the public `validate_path()` API.
- Test the embedded fence as executable code, including OKF conformance and byte-for-byte deterministic regeneration, without adding recipe-only dependencies to the package runtime.