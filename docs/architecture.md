# Architecture boundary

`okf-parser` keeps one strict core and puts source adaptation outside that core.

```text
discovery / classification
        ↓
strict authored OKF parse
        ↓
authoritative normalized relations
        ↓
TypeContract
        ↓
typed DuckDB / Ibis relations
        ↓
consumer projections and adapters
```

The core answers what an authored OKF bundle says and whether it conforms. A non-reserved Markdown concept therefore needs authored OKF frontmatter and a non-empty `type`. Core graph relations come from authored Markdown links; producer-defined frontmatter strings remain data, even when they look like paths.

External corpora use a separate adaptation boundary:

```text
external source (MDX, legacy Markdown, Agent Skills, ...)
        ↓
source adapter / projection policy
        ↓
projection plan + explicit provenance
        ↓
canonical OKF representation
        ↓
ordinary core pipeline
```

An adapter may derive an effective type, rewrite a source relation into the projected namespace, or recognize a source dialect. It must preserve enough provenance to distinguish authored evidence from projection policy. The downstream graph, schema, DuckDB/Ibis and MCP surfaces should consume the canonical OKF representation instead of learning every source dialect independently.

This is primarily an internal architecture rule. Ordinary conformant bundles should not need adapter configuration, profiles or extra command hierarchy. Advanced adaptation is progressive disclosure.
