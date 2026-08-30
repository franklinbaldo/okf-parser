---
type: Release Note
title: self-contained codebase-to-OKF recipe skill
---

- Add RFC 0019, keeping source-specific extraction out of `okf-parser` core and defining typed recipe skill directories as the preferred boundary for deterministic source-to-OKF adapters.
- Add `skills/codebase-to-okf/SKILL.md` plus bundled PEP 723 generation and query recipes under `scripts/`, keeping agent instructions compact while recipe-only dependencies stay outside the package runtime.
- Extract Python modules, classes, functions, methods, imports, signatures, parameters, returns, docstrings, decorators, bases, class fields and explicit `CodeCall` syntax observations into deterministic derived OKF.
- Preserve epistemic boundaries by marking calls and imports `syntactic-unresolved`; name-matched call targets are navigation candidates rather than dispatch claims.
- Add compact code-aware lookup by symbol, caller, callee, source and producer-defined type on top of the public generic `load_bundle()` API, without adding code semantics to parser core; compact JSON is the default so agents can decide whether a source read is still necessary before paying for it.
- Keep the standalone recipes under the repository's normal Ruff quality rules, with only the explicit `T201` exception appropriate to command-line output rather than a blanket recipe lint exemption.
- Test the skill as typed OKF knowledge and both bundled recipes as executable code, including rich metadata, same-name redefinitions, code-aware lookup, conformant output and byte-for-byte deterministic regeneration.
