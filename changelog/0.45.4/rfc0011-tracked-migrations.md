---
type: Release Note
title: tracked bundle migrations proposal
---

- Propose RFC 0011 for idempotent, ledger-backed bundle migrations that reuse the RFC 0005 relational write contract and record each migration as an ordinary `type: Migration` OKF concept. The design also makes atomic creation of new candidate files a shared write primitive so migrations and imports can converge on one staging/validation/conflict path.
