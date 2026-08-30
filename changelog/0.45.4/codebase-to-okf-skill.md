---
type: Release Note
title: self-contained codebase-to-OKF recipe skill
---

- Add RFC 0019, keeping source-specific extraction out of `okf-parser` core and defining typed recipe skill directories as the preferred boundary for deterministic source-to-OKF adapters.
- Add `skills/codebase-to-okf/SKILL.md` plus a bundled PEP 723 Python recipe under `scripts/`, keeping agent instructions compact while recipe-only dependencies stay outside the package runtime.
- Extract Python modules, classes, functions, methods, imports, signatures, parameters, returns, docstrings, decorators, bases, class fields and explicit `CodeCall` syntax observations into deterministic derived OKF.
- Preserve epistemic boundaries by marking calls and imports `syntactic-unresolved`; name-matched call targets are navigation candidates rather than dispatch claims.
- Test the skill as typed OKF knowledge and the bundled recipe as executable code, including rich metadata, same-name redefinitions, conformant output and byte-for-byte deterministic regeneration.
