# Canonical frontmatter writer

OKF write paths that synthesize frontmatter use one semantic contract:

- JSON-shaped values remain typed: strings, numbers, booleans, sequences, mappings and nulls are not stringified before YAML emission;
- the Rust engine renders canonical YAML with `yaml-rust2` when the packaged core is available;
- source checkouts without the Rust binary use a compatibility fallback with the same typed value contract;
- generated text is UTF-8 with LF newlines independent of the host operating system;
- filesystem replacement is staged and atomic where the caller writes an existing path.

The writer is intentionally about synthesized frontmatter. Whole-document formatting keeps Markdown structure and therefore uses the shared UTF-8/LF byte writer rather than reparsing arbitrary Markdown through the YAML emitter.
