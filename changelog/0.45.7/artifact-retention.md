---
type: Release Note
title: stop keeping intermediate build artifacts for a quarter
---

- Give every wheel and sdist upload in the release and dry-run workflows a one-day retention. They exist so the collecting job can download them inside the same run, and nothing reads them afterwards, but they inherited the 90-day default and accumulated: this repository was holding 1,692 live artifacts totalling 3.7 GB against a 500 MB allowance, of which the wheels and sdists were 3.5 GB.
- Leave the tested release set and the dry-run evidence at their existing fourteen days. Those are the artifacts `docs/releasing.md` describes as evidence for review, and they account for 41 MB.
