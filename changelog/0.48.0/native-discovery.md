---
type: Release Note
title: ingestion discovers files the way a load does
---

# ingestion discovers files the way a load does

`ingest_documents()` asked a Python walker which Markdown files a bundle
has, while every command asked the engine. The two disagreed on one thing:
the Python walker descended into `node_modules`, so ingestion could yield
documents a bundle load never reads. Discovery is now the engine's alone,
over a `__discover` request, and `okf_parser.ingestion.discover()` exposes
it.

`.okfignore` semantics are unchanged; `conformance/exclusion.json` is now
pinned by a Rust test against the engine's matcher, where the nearest rule
decides and `!vendor/knowledge` re-includes below an excluded `vendor`.
A corrupt `.okfignore` is a `ValueError` naming the file. Removed Python
internals: `okf_parser.discovery` and `okf_parser.exclusion`.

The release workflow's conformance job no longer names the deleted Python
corpus tests (it would have failed at the next tag); it runs the engine's
corpus tests instead, and a test now checks that every test file a
workflow names exists.
