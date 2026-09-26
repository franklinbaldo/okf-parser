//! The read-only commands answered natively, shared by the CLI and MCP.
//!
//! Each returns a typed report; the CLI renders it as sorted, indented JSON
//! and MCP as structured content. `check --relational-schema` and
//! `init --infer-schema` still need DuckDB and are delegated to Python until
//! phase 4 (RFC 0024).

use std::path::Path;
use std::{fmt, io};

use okf_engine::check::{self, CheckError, CheckReport, Inventory, READ_CONCURRENCY, SpecRules};
use okf_engine::specs::{Scaffold, SpecTemplate, SpecTemplateError, scaffold_missing_specs};
use okf_engine::{ConceptGraph, GraphSummary, LoadError, load_bundle};
use serde::Serialize;
use serde_json::Value;

pub fn check(
    path: &Path,
    exclude: &[String],
    require_spec: Option<&str>,
    normative_spec: bool,
    classify: bool,
) -> Result<CheckReport, CheckError> {
    let rules = SpecRules {
        require_spec,
        normative: normative_spec,
    };
    check::check(path, exclude, rules, classify)
}

pub fn inventory(path: &Path, exclude: &[String], digests: bool) -> Result<Inventory, LoadError> {
    let data = load_bundle(path, exclude, READ_CONCURRENCY)?;
    Ok(check::inventory(data, digests))
}

/// The graph summary of a bundle, with its root.
#[derive(Debug, Serialize)]
pub struct GraphReport {
    pub root: String,
    #[serde(flatten)]
    pub summary: GraphSummary,
}

pub fn graph(path: &Path, exclude: &[String]) -> Result<GraphReport, LoadError> {
    let data = load_bundle(path, exclude, READ_CONCURRENCY)?;
    let summary = ConceptGraph::from_bundle(&data).summary();
    Ok(GraphReport {
        root: data.root,
        summary,
    })
}

/// Why `init` could not scaffold.
#[derive(Debug)]
pub enum InitError {
    Load(LoadError),
    SpecTemplate(SpecTemplateError),
    Io(io::Error),
}

impl fmt::Display for InitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::SpecTemplate(error) => error.fmt(f),
            Self::Io(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for InitError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::SpecTemplate(error) => Some(error),
            Self::Io(error) => Some(error),
        }
    }
}

/// `init` without `--infer-schema`: `{"specs": ...}`.
#[derive(Debug, Serialize)]
pub struct InitReport {
    pub specs: Scaffold,
}

/// Plan, or with `write` create, a stub for every type in use lacking a spec.
pub fn init_specs(
    path: &Path,
    exclude: &[String],
    spec_template: &str,
    write: bool,
) -> Result<Scaffold, InitError> {
    let template = SpecTemplate::new(spec_template).map_err(InitError::SpecTemplate)?;
    let data = load_bundle(path, exclude, READ_CONCURRENCY).map_err(InitError::Load)?;
    let types = data.concepts.iter().map(|c| c.concept_type.as_str());
    scaffold_missing_specs(Path::new(&data.root), types, template, write).map_err(InitError::Io)
}

/// Whether the caller, not the environment, is at fault for a load failure.
pub fn load_is_request(error: &LoadError) -> bool {
    !matches!(error, LoadError::Walk(_) | LoadError::ThreadPool(_))
}

/// `value` with every object's keys in sorted order, whatever map type
/// `serde_json` was built with; the CLI's output contract.
pub fn sorted(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut entries: Vec<(String, Value)> = map.into_iter().collect();
            entries.sort_by(|(a, _), (b, _)| a.cmp(b));
            Value::Object(entries.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        Value::Array(values) => Value::Array(values.into_iter().map(sorted).collect()),
        other => other,
    }
}

/// Render a report the way the CLI always has: indented JSON, sorted keys.
pub fn render(report: &impl Serialize) -> serde_json::Result<String> {
    let mut text = serde_json::to_string_pretty(&sorted(serde_json::to_value(report)?))?;
    text.push('\n');
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rendering_sorts_keys_at_every_level() {
        let report = serde_json::json!({"b": [{"z": 1, "a": 2}], "a": "é"});
        assert_eq!(
            render(&report).unwrap(),
            "{\n  \"a\": \"é\",\n  \"b\": [\n    {\n      \"a\": 2,\n      \"z\": 1\n    }\n  ]\n}\n"
        );
    }
}
