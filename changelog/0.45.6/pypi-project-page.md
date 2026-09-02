---
type: Release Note
title: give the PyPI project page metadata and a clean description
---

- Stop publishing the README's OKF frontmatter as page content. `README.md` is a `type: Project` concept and must open with `---`, which PyPI rendered as a thematic break plus a setext heading, so every project page opened with `type: Project title: okf-parser description: ...` above the real title. `scripts/pypi_readme.py` now derives `README.pypi.md`, applying the same correction `changelog_notes.py` already applies to GitHub Release bodies.
- Rewrite repository-relative links in the derived description to absolute URLs. `docs/architecture.md` resolved on GitHub and pointed nowhere on PyPI.
- Declare `MIT` and ship a root `LICENSE`. The three npm packages already declared MIT and shipped the file, while the Python distribution carried no licence at all.
- Declare `authors`, `keywords`, `classifiers` and `project.urls`. The project page previously offered no link back to the repository, its issues or its releases.
- Verify the derived description in CI, so it cannot drift from `README.md`, and exclude it from the repository's own OKF validation because it is a packaging artifact rather than authored knowledge.
