//! The commands answered natively, shared by the CLI and MCP.
//!
//! Each returns a typed report; the CLI renders it as sorted, indented JSON
//! and MCP as structured content.

use std::path::Path;
use std::{fmt, io};

use okf_db::export::{ExportError, ExportOptions, ExportReport, export_bundle};
use okf_db::infer::{InferError, scaffold_starter_schemas};
use okf_db::query::{QueryError, QueryOptions, QueryResult, query_bundle};
use okf_db::relational::{RelationalSchemaError, load_relational_schema, validate_relations};
use okf_engine::check::{
    self, CheckError, CheckReport, Inventory, READ_CONCURRENCY, SpecRules, check_loaded,
};
use okf_engine::specs::{
    Scaffold, SpecTemplate, SpecTemplateError, commit_scaffold, plan_scaffold,
};
use okf_engine::{ConceptGraph, GraphSummary, LoadError, Severity, load_bundle};
use serde::Serialize;
use serde_json::Value;

/// Why `check` could not produce a report.
#[derive(Debug)]
pub enum CheckFailure {
    Check(CheckError),
    Relational(RelationalSchemaError),
}

impl fmt::Display for CheckFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Check(error) => error.fmt(f),
            Self::Relational(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for CheckFailure {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Check(error) => Some(error),
            Self::Relational(error) => Some(error),
        }
    }
}

impl From<CheckError> for CheckFailure {
    fn from(error: CheckError) -> Self {
        Self::Check(error)
    }
}

/// The check options beyond the bundle itself.
#[derive(Debug, Clone, Copy, Default)]
pub struct CheckOptions<'a> {
    pub require_spec: Option<&'a str>,
    pub normative_spec: bool,
    pub classify: bool,
    /// The bundle's relational schema (`okf.schema.sql`), relative to its root.
    pub relational_schema: Option<&'a Path>,
}

pub fn check(
    path: &Path,
    exclude: &[String],
    options: CheckOptions<'_>,
) -> Result<CheckReport, CheckFailure> {
    let Some(relational) = options.relational_schema else {
        let rules = SpecRules {
            require_spec: options.require_spec,
            normative: options.normative_spec,
        };
        return Ok(check::check(path, exclude, rules, options.classify)?);
    };
    let template = options
        .require_spec
        .map(SpecTemplate::new)
        .transpose()
        .map_err(CheckError::from)?;
    let data = load_bundle(path, exclude, READ_CONCURRENCY).map_err(CheckError::from)?;
    let mut report = check_loaded(&data, template, options.normative_spec, options.classify)
        .map_err(CheckError::from)?;
    let schema = load_relational_schema(&Path::new(&data.root).join(relational))
        .map_err(CheckFailure::Relational)?;
    report
        .diagnostics
        .extend(validate_relations(&data, &schema));
    check::order(&mut report.diagnostics);
    report.conformant = !report
        .diagnostics
        .iter()
        .any(|item| item.severity == Severity::Error);
    Ok(report)
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
    Infer(InferError),
}

impl fmt::Display for InitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::SpecTemplate(error) => error.fmt(f),
            Self::Io(error) => error.fmt(f),
            Self::Infer(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for InitError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::SpecTemplate(error) => Some(error),
            Self::Io(error) => Some(error),
            Self::Infer(error) => Some(error),
        }
    }
}

/// `init`: `{"specs": ...}`, plus `"schemas"` with `--infer-schema`.
#[derive(Debug, Serialize)]
pub struct InitReport {
    pub specs: Scaffold,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schemas: Option<Scaffold>,
}

impl InitReport {
    /// Whether a derived-path collision blocked part of the scaffold.
    pub fn collided(&self) -> bool {
        !self.specs.collisions.is_empty()
            || self
                .schemas
                .as_ref()
                .is_some_and(|schemas| !schemas.collisions.is_empty())
    }
}

/// Plan, or with `write` create, a stub for every type in use lacking a
/// spec and, with `infer_schema`, a starter `.schema.sql` for every type
/// lacking a declaration. Both read one load of the bundle.
pub fn init(
    path: &Path,
    exclude: &[String],
    spec_template: &str,
    write: bool,
    infer_schema: bool,
) -> Result<InitReport, InitError> {
    let template = SpecTemplate::new(spec_template).map_err(InitError::SpecTemplate)?;
    let data = load_bundle(path, exclude, READ_CONCURRENCY).map_err(InitError::Load)?;
    let root = Path::new(&data.root);
    let types = data.concepts.iter().map(|c| c.concept_type.as_str());
    let plan = plan_scaffold(root, types, template);
    let schemas = infer_schema
        .then(|| scaffold_starter_schemas(&data, template, write))
        .transpose()
        .map_err(InitError::Infer)?;
    let specs = commit_scaffold(root, plan, write).map_err(InitError::Io)?;
    Ok(InitReport { specs, schemas })
}

/// What `okf-parser duckdb` answers. Refusing to replace existing tables is
/// an answer the caller can act on, not a failure.
#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ExportAnswer {
    Exported(ExportReport),
    Refused {
        error: String,
        schema: String,
        existing_tables: Vec<String>,
    },
}

impl ExportAnswer {
    pub fn refused(&self) -> bool {
        matches!(self, Self::Refused { .. })
    }
}

/// `okf-parser duckdb`: the bundle as tables in a DuckDB database.
pub fn export(path: &Path, options: &ExportOptions<'_>) -> Result<ExportAnswer, ExportError> {
    match export_bundle(path, options) {
        Ok(report) => Ok(ExportAnswer::Exported(report)),
        Err(ExportError::Collision { schema, tables }) => {
            let error = ExportError::Collision {
                schema: schema.clone(),
                tables: tables.clone(),
            };
            Ok(ExportAnswer::Refused {
                error: error.to_string(),
                schema,
                existing_tables: tables,
            })
        }
        Err(error) => Err(error),
    }
}

/// Why `sql` produced no rows.
#[derive(Debug)]
pub enum SqlError {
    Load(LoadError),
    Query(QueryError),
}

impl fmt::Display for SqlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::Query(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for SqlError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::Query(error) => Some(error),
        }
    }
}

/// `okf-parser sql`: one read-only query over the bundle at `path`.
pub fn sql(
    path: &Path,
    exclude: &[String],
    query: &str,
    options: QueryOptions<'_>,
) -> Result<QueryResult, SqlError> {
    let data = load_bundle(path, exclude, READ_CONCURRENCY).map_err(SqlError::Load)?;
    query_bundle(&data, query, options).map_err(SqlError::Query)
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
