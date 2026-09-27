//! The JSON protocol between the Python shell and this binary (RFC 0024).
//!
//! Every response carries `protocol`, so the shell can refuse a binary it does
//! not understand instead of misreading it. Bump it on any breaking change to a
//! response shape; adding a field is not breaking.

use okf_engine::frontmatter::render_document;
use okf_engine::write::{
    EditOutcome, EditReport, EditRequest, ValidationItem, WriteError, edit_concept,
};
use serde_json::{Map, Value};
use std::path::PathBuf;

use okf_db::declared::{DeclaredSchema, parse_declared_schema};
use okf_db::export::{ExportError, ExportOptions};
use okf_db::query::{QueryError, QueryOptions, QueryResult, query_bundle};
use okf_db::relational::{RelationalSchema, RelationalSchemaError, parse_relational_schema};
use okf_db::source::SourceRows;
use okf_engine::check::{CheckError, CheckReport};
use okf_engine::{BundleData, ConceptGraph, GraphSummary, LoadError};
use serde::{Deserialize, Serialize};

use crate::commands::{
    ApplyFailure, ApplyInput, ApplyResult, CheckFailure, CheckOptions, ExportAnswer, InitError,
    InitReport,
};

pub const PROTOCOL_VERSION: u32 = 1;

/// `__engine-load`: the bundle's records, diagnostics and graph summary.
#[derive(Serialize)]
pub struct LoadResponse<'a> {
    protocol: u32,
    #[serde(flatten)]
    data: &'a BundleData,
    graph: GraphSummary,
}

impl<'a> LoadResponse<'a> {
    pub fn new(data: &'a BundleData) -> Self {
        Self {
            protocol: PROTOCOL_VERSION,
            graph: ConceptGraph::from_bundle(data).summary(),
            data,
        }
    }
}

/// Why a command could not produce a result; the shell maps `kind` to an exception.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct ProtocolError {
    kind: &'static str,
    message: String,
}

impl ProtocolError {
    /// The shell-facing form of an engine error, for a command named `command`.
    /// `kind` says whether the request (`request`) or the environment (`io`)
    /// is at fault; only this layer turns errors into prose.
    fn from_write(error: &WriteError, command: &str) -> Self {
        let kind = if error.is_request() { "request" } else { "io" };
        let message = match error {
            WriteError::ChangedDuringRead { path } => {
                format!("file changed while {command} was reading it: {path}")
            }
            other => other.to_string(),
        };
        Self { kind, message }
    }
}

/// A command's answer: `{"protocol", "result"}` or `{"protocol", "error"}`.
#[derive(Debug, Serialize)]
pub struct Response<T> {
    protocol: u32,
    #[serde(flatten)]
    answer: Answer<T>,
}

#[derive(Debug, Serialize)]
enum Answer<T> {
    #[serde(rename = "result")]
    Success(T),
    #[serde(rename = "error")]
    Error(ProtocolError),
}

impl<T> Response<T> {
    fn new(answer: Answer<T>) -> Self {
        Self {
            protocol: PROTOCOL_VERSION,
            answer,
        }
    }
}

/// The JSON shape `preview_concept_edit` / `write_concept_edit` have always
/// returned; built only here, from the engine's `EditReport`.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct EditResult {
    concept_id: String,
    path: String,
    source_digest: String,
    candidate_source_digest: String,
    candidate_parsed_digest: String,
    changed: bool,
    succeeded: bool,
    written: bool,
    validation: Vec<ValidationItem>,
    conflict_paths: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<&'static str>,
}

impl From<EditReport> for EditResult {
    fn from(report: EditReport) -> Self {
        let mut validation = Vec::new();
        let mut conflict_paths = Vec::new();
        let (changed, written, error) = match report.outcome {
            EditOutcome::Unchanged => (false, false, None),
            EditOutcome::Stale => {
                conflict_paths.push(report.path.clone());
                (
                    false,
                    false,
                    Some("concept source changed since it was read"),
                )
            }
            EditOutcome::Previewed => (true, false, None),
            EditOutcome::Written => (true, true, None),
            EditOutcome::Invalid(items) => {
                validation = items;
                let error = "candidate bundle introduces new normative diagnostics";
                (true, false, Some(error))
            }
            EditOutcome::Conflict(paths) => {
                conflict_paths = paths;
                (
                    true,
                    false,
                    Some("the bundle changed since edit validated it"),
                )
            }
        };
        Self {
            concept_id: report.concept_id,
            path: report.path,
            source_digest: report.source_digest,
            candidate_source_digest: report.candidate.source,
            candidate_parsed_digest: report.candidate.parsed,
            changed,
            succeeded: error.is_none(),
            written,
            validation,
            conflict_paths,
            error,
        }
    }
}

impl ProtocolError {
    fn request(error: &dyn std::error::Error) -> Self {
        Self {
            kind: "request",
            message: error.to_string(),
        }
    }

    fn io(error: &dyn std::error::Error) -> Self {
        Self {
            kind: "io",
            message: error.to_string(),
        }
    }

    fn spec_template(error: &dyn std::error::Error) -> Self {
        Self {
            kind: "spec_template",
            message: error.to_string(),
        }
    }

    fn from_load(error: &LoadError) -> Self {
        if crate::commands::load_is_request(error) {
            Self::request(error)
        } else {
            Self::io(error)
        }
    }
}

impl<T> From<Result<T, ProtocolError>> for Response<T> {
    fn from(outcome: Result<T, ProtocolError>) -> Self {
        Self::new(match outcome {
            Ok(result) => Answer::Success(result),
            Err(error) => Answer::Error(error),
        })
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckRequest {
    path: PathBuf,
    #[serde(default)]
    exclude: Vec<String>,
    #[serde(default)]
    require_spec: Option<String>,
    #[serde(default)]
    normative_spec: bool,
    #[serde(default)]
    classify: bool,
    #[serde(default)]
    relational_schema: Option<PathBuf>,
}

impl ProtocolError {
    fn from_check(error: &CheckFailure) -> Self {
        match error {
            CheckFailure::Check(CheckError::SpecTemplate(_)) => Self::spec_template(error),
            CheckFailure::Check(CheckError::Load(load)) => Self::from_load(load),
            CheckFailure::Relational(RelationalSchemaError::Script(_)) => {
                Self::relational_schema(error)
            }
            CheckFailure::Relational(RelationalSchemaError::Read { .. }) => Self::request(error),
            CheckFailure::Relational(_) => Self::relational_schema(error),
        }
    }

    fn relational_schema(error: &dyn std::error::Error) -> Self {
        Self {
            kind: "relational_schema",
            message: error.to_string(),
        }
    }

    fn declared_schema(error: &dyn std::error::Error) -> Self {
        Self {
            kind: "declared_schema",
            message: error.to_string(),
        }
    }
}

/// `__check`: the native check report, for the Python shell's `validate_path`.
pub fn check(request: &str) -> Result<Response<CheckReport>, serde_json::Error> {
    let request: CheckRequest = serde_json::from_str(request)?;
    let options = CheckOptions {
        require_spec: request.require_spec.as_deref(),
        normative_spec: request.normative_spec,
        classify: request.classify,
        relational_schema: request.relational_schema.as_deref(),
    };
    let outcome = crate::commands::check(&request.path, &request.exclude, options)
        .map_err(|error| ProtocolError::from_check(&error));
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct InitRequest {
    path: PathBuf,
    spec_template: String,
    #[serde(default)]
    exclude: Vec<String>,
    #[serde(default)]
    write: bool,
    #[serde(default)]
    infer_schema: bool,
}

/// `__init`: the specification scaffold and, on request, starter schemas.
pub fn init(request: &str) -> Result<Response<InitReport>, serde_json::Error> {
    let request: InitRequest = serde_json::from_str(request)?;
    let outcome = crate::commands::init(
        &request.path,
        &request.exclude,
        &request.spec_template,
        request.write,
        request.infer_schema,
    )
    .map_err(|error| match &error {
        InitError::SpecTemplate(_) => ProtocolError::spec_template(&error),
        InitError::Load(load) => ProtocolError::from_load(load),
        InitError::Io(_) | InitError::Infer(_) => ProtocolError::io(&error),
    });
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExportRequest {
    path: PathBuf,
    database: String,
    schema: String,
    #[serde(default)]
    overwrite: bool,
    #[serde(default)]
    exclude: Vec<String>,
    #[serde(default)]
    spec_template: Option<String>,
}

/// `__export-duckdb`: `Bundle`'s DuckDB export for the Python API.
pub fn export_duckdb(request: &str) -> Result<Response<ExportAnswer>, serde_json::Error> {
    let request: ExportRequest = serde_json::from_str(request)?;
    let options = ExportOptions {
        database: &request.database,
        schema: &request.schema,
        overwrite: request.overwrite,
        exclude: &request.exclude,
        spec_template: request.spec_template.as_deref(),
    };
    let outcome = crate::commands::export(&request.path, &options).map_err(|error| match &error {
        ExportError::SpecTemplate(_) => ProtocolError::spec_template(&error),
        ExportError::Load(load) => ProtocolError::from_load(load),
        ExportError::Declared(_) => ProtocolError::declared_schema(&error),
        ExportError::Db(_) => ProtocolError::io(&error),
        _ => ProtocolError::request(&error),
    });
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SqlRequest {
    /// The records of the `Bundle` being queried, exactly as `__engine-load`
    /// answered them, so the query sees the caller's snapshot.
    bundle: BundleData,
    query: String,
    #[serde(default)]
    spec_template: Option<String>,
    #[serde(default)]
    limit: Option<usize>,
}

/// `__sql`: one read-only query over a loaded bundle, for `Bundle.sql()`.
pub fn sql(request: &str) -> Result<Response<QueryResult>, serde_json::Error> {
    let request: SqlRequest = serde_json::from_str(request)?;
    let options = QueryOptions {
        spec_template: request.spec_template.as_deref(),
        limit: request.limit,
    };
    let outcome =
        query_bundle(&request.bundle, &request.query, options).map_err(|error| match &error {
            QueryError::SpecTemplate(_) => ProtocolError::spec_template(&error),
            QueryError::Declared(_) => ProtocolError::declared_schema(&error),
            QueryError::Query(_) | QueryError::NotAQuery => ProtocolError {
                kind: "query",
                message: error.to_string(),
            },
            QueryError::Typed(_) => ProtocolError::request(&error),
            QueryError::Db(_) => ProtocolError::io(&error),
        });
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DeclaredSchemaRequest {
    sql: String,
    concept_type: String,
}

/// `__declared-schema`: run one `.schema.sql` and read its table back.
pub fn declared_schema(request: &str) -> Result<Response<DeclaredSchema>, serde_json::Error> {
    let request: DeclaredSchemaRequest = serde_json::from_str(request)?;
    let outcome = parse_declared_schema(&request.sql, &request.concept_type)
        .map_err(|error| ProtocolError::declared_schema(&error));
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RelationalSchemaRequest {
    sql: String,
}

/// `__relational-schema`: run one relational schema and read its keys back.
pub fn relational_schema(request: &str) -> Result<Response<RelationalSchema>, serde_json::Error> {
    let request: RelationalSchemaRequest = serde_json::from_str(request)?;
    let outcome = parse_relational_schema(&request.sql)
        .map_err(|error| ProtocolError::relational_schema(&error));
    Ok(outcome.into())
}

/// `__edit`: preview or commit one concept's body replacement.
pub fn edit(request: &str) -> Result<Response<EditResult>, serde_json::Error> {
    let request: EditRequest = serde_json::from_str(request)?;
    Ok(Response::new(match edit_concept(&request) {
        Ok(report) => Answer::Success(report.into()),
        Err(error) => Answer::Error(ProtocolError::from_write(&error, "edit")),
    }))
}

/// `__apply`: plan an apply in DuckDB and commit it on the write engine.
pub fn apply(request: &str) -> Result<Response<ApplyResult>, serde_json::Error> {
    let request: ApplyInput = serde_json::from_str(request)?;
    Ok(crate::commands::apply(&request)
        .map_err(|error| match &error {
            ApplyFailure::Request(_) => ProtocolError::request(&error),
            ApplyFailure::Write(write) => ProtocolError::from_write(write, "apply"),
        })
        .into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReadSourceRequest {
    source: String,
}

/// `__read-source`: every row of a DuckDB-readable source, for `import`.
pub fn read_source(request: &str) -> Result<Response<SourceRows>, serde_json::Error> {
    let request: ReadSourceRequest = serde_json::from_str(request)?;
    Ok(okf_db::source::read_source(&request.source)
        .map_err(|error| ProtocolError::request(&error))
        .into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct NewDocument {
    frontmatter: Map<String, Value>,
    #[serde(default)]
    body: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderRequest {
    documents: Vec<NewDocument>,
}

/// The rendered documents, in request order.
#[derive(Debug, Serialize)]
pub struct Rendered {
    documents: Vec<String>,
}

/// `__render`: the canonical text of new OKF documents, in one batch.
pub fn render(request: &str) -> Result<Response<Rendered>, serde_json::Error> {
    let request: RenderRequest = serde_json::from_str(request)?;
    let rendered = request
        .documents
        .iter()
        .map(|document| render_document(&document.frontmatter, &document.body))
        .collect::<Result<Vec<_>, _>>()
        .map(|documents| Rendered { documents })
        .map_err(|error| ProtocolError::request(&error));
    Ok(rendered.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_response_is_versioned_and_carries_the_graph() {
        let data = BundleData {
            root: "/b".into(),
            concepts: Vec::new(),
            reserved: Vec::new(),
            links: Vec::new(),
            diagnostics: Vec::new(),
            markdown_count: 0,
        };
        let value = serde_json::to_value(LoadResponse::new(&data)).unwrap();
        assert_eq!(value["protocol"], PROTOCOL_VERSION);
        assert_eq!(value["root"], "/b");
        assert_eq!(value["graph"]["nodes"], 0);
        assert_eq!(value["graph"]["directed_acyclic"], true);
    }

    #[test]
    fn request_errors_are_reported_not_raised() {
        let request = r#"{"path": "/definitely/not/a/bundle", "concept_id": "a",
            "body": "", "expected_source_digest": "d"}"#;
        let value = serde_json::to_value(edit(request).unwrap()).unwrap();
        assert_eq!(value["protocol"], PROTOCOL_VERSION);
        assert_eq!(value["error"]["kind"], "request");
        assert!(value.get("result").is_none());
    }

    #[test]
    fn a_result_carries_no_error_key() {
        let value = serde_json::to_value(Response::new(Answer::<u8>::Success(7))).unwrap();
        assert_eq!(
            value,
            serde_json::json!({"protocol": PROTOCOL_VERSION, "result": 7})
        );
    }

    #[test]
    fn a_file_changing_during_the_read_is_worded_for_the_command() {
        let error = WriteError::ChangedDuringRead {
            path: "a.md".into(),
        };
        assert_eq!(
            ProtocolError::from_write(&error, "edit"),
            ProtocolError {
                kind: "request",
                message: "file changed while edit was reading it: a.md".into(),
            }
        );
    }

    #[test]
    fn stale_and_refused_edits_keep_the_legacy_shape() {
        let report = |outcome| EditReport {
            concept_id: "a".into(),
            path: "a.md".into(),
            source_digest: "s".into(),
            candidate: okf_engine::write::Digests {
                source: "cs".into(),
                parsed: "cp".into(),
            },
            outcome,
        };
        let stale = serde_json::to_value(EditResult::from(report(EditOutcome::Stale))).unwrap();
        assert_eq!(stale["succeeded"], false);
        assert_eq!(stale["changed"], false);
        assert_eq!(stale["conflict_paths"], serde_json::json!(["a.md"]));
        let written = serde_json::to_value(EditResult::from(report(EditOutcome::Written))).unwrap();
        assert_eq!(written["written"], true);
        assert!(written.get("error").is_none());
        let conflict = EditResult::from(report(EditOutcome::Conflict(vec!["b.md".into()])));
        assert!(!conflict.succeeded && conflict.changed && !conflict.written);
        assert_eq!(conflict.conflict_paths, ["b.md"]);
    }

    #[test]
    fn engine_errors_map_to_request_or_io() {
        let unknown = ProtocolError::from_write(&WriteError::UnknownConcept("x".into()), "edit");
        assert_eq!(unknown.kind, "request");
        assert_eq!(unknown.message, "concept does not exist exactly once: x");
        let io =
            ProtocolError::from_write(&WriteError::Io(std::io::Error::other("disk full")), "edit");
        assert_eq!(
            io,
            ProtocolError {
                kind: "io",
                message: "disk full".into(),
            }
        );
    }

    #[test]
    fn check_requests_report_a_bad_template_as_its_own_kind() {
        let dir = std::env::temp_dir();
        let request = serde_json::json!({
            "path": dir, "require_spec": "docs/types.md"
        })
        .to_string();
        let value = serde_json::to_value(check(&request).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "spec_template");
        assert_eq!(
            value["error"]["message"],
            r#"specification template must contain {slug}: "docs/types.md""#
        );
    }

    #[test]
    fn a_missing_root_is_a_request_error_for_every_command() {
        let check_request = r#"{"path": "/definitely/not/a/bundle"}"#;
        let value = serde_json::to_value(check(check_request).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "request");
        let init_request = r#"{"path": "/definitely/not/a/bundle", "spec_template": "{slug}.md"}"#;
        let value = serde_json::to_value(init(init_request).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "request");
    }

    fn bundle(name: &str, files: &[(&str, &str)]) -> PathBuf {
        let root = std::env::temp_dir().join(format!("okf-protocol-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        for (path, text) in files {
            std::fs::write(root.join(path), text).unwrap();
        }
        root
    }

    #[test]
    fn a_check_runs_the_relational_schema_natively() {
        let root = bundle(
            "relational",
            &[
                ("a.md", "---\ntype: Book\nisbn: '1'\n---\n"),
                ("b.md", "---\ntype: Book\nisbn: '1'\n---\n"),
                ("okf.schema.sql", "CREATE TABLE Book (isbn VARCHAR UNIQUE);"),
            ],
        );
        let request =
            serde_json::json!({"path": root, "relational_schema": "okf.schema.sql"}).to_string();
        let value = serde_json::to_value(check(&request).unwrap()).unwrap();
        let broken = serde_json::json!({"path": root, "relational_schema": "missing.sql"});
        let missing = serde_json::to_value(check(&broken.to_string()).unwrap()).unwrap();
        std::fs::write(root.join("bad.sql"), "CREATE TABLE (").unwrap();
        let script = serde_json::json!({"path": root, "relational_schema": "bad.sql"});
        let failed = serde_json::to_value(check(&script.to_string()).unwrap()).unwrap();
        std::fs::remove_dir_all(&root).unwrap();
        let result = &value["result"];
        assert_eq!(result["conformant"], false);
        assert_eq!(result["diagnostics"][0]["code"], "OKF021");
        assert_eq!(result["diagnostics"][0]["path"], "b.md");
        assert_eq!(missing["error"]["kind"], "request");
        assert_eq!(failed["error"]["kind"], "relational_schema");
    }

    #[test]
    fn sql_runs_over_the_snapshot_it_is_given() {
        let request = serde_json::json!({
            "bundle": {
                "root": "/nowhere", "concepts": [], "reserved": [], "links": [],
                "diagnostics": [], "markdown_count": 0, "graph": {"nodes": 0}
            },
            "query": "SELECT count(*) AS n FROM concepts"
        });
        let value = serde_json::to_value(sql(&request.to_string()).unwrap()).unwrap();
        assert_eq!(value["result"]["rows"], serde_json::json!([[0]]));
        assert_eq!(value["result"]["columns"][0]["type"], "BIGINT");
        let broken = serde_json::json!({"bundle": request["bundle"], "query": "SELEC 1"});
        let value = serde_json::to_value(sql(&broken.to_string()).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "query");
    }

    #[test]
    fn schemas_are_read_back_from_the_catalog() {
        let declared = serde_json::json!({
            "sql": "CREATE TABLE note (due DATE, n DECIMAL(9,2));", "concept_type": "Note"
        });
        let value = serde_json::to_value(declared_schema(&declared.to_string()).unwrap()).unwrap();
        assert_eq!(value["result"]["table_name"], "note");
        assert_eq!(
            value["result"]["columns"][1]["logical_type"]["precision"],
            9
        );
        let missing = serde_json::json!({"sql": "SELECT 1;", "concept_type": "Note"});
        let value = serde_json::to_value(declared_schema(&missing.to_string()).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "declared_schema");
        let relational = serde_json::json!({
            "sql": "CREATE TABLE a (k VARCHAR PRIMARY KEY); \
                    CREATE TABLE b (r VARCHAR REFERENCES a(k));"
        });
        let value =
            serde_json::to_value(relational_schema(&relational.to_string()).unwrap()).unwrap();
        assert_eq!(value["result"]["keys"][0]["primary"], true);
        assert_eq!(value["result"]["foreign_keys"][0]["referenced_table"], "a");
    }

    #[test]
    fn an_export_refusal_is_a_result_with_the_existing_tables() {
        let root = bundle("export", &[("a.md", "---\ntype: Note\n---\n")]);
        let database = root.join("out.duckdb");
        let request = serde_json::json!({
            "path": root, "database": database, "schema": "okf"
        })
        .to_string();
        let first = serde_json::to_value(export_duckdb(&request).unwrap()).unwrap();
        let second = serde_json::to_value(export_duckdb(&request).unwrap()).unwrap();
        let bad = serde_json::json!({"path": root, "database": database, "schema": "1x"});
        let invalid = serde_json::to_value(export_duckdb(&bad.to_string()).unwrap()).unwrap();
        std::fs::remove_dir_all(&root).unwrap();
        assert_eq!(first["result"]["concept_count"], 1);
        assert_eq!(
            second["result"]["existing_tables"],
            serde_json::json!(["concepts", "links", "reserved", "diagnostics"])
        );
        assert_eq!(invalid["error"]["kind"], "request");
    }

    #[test]
    fn apply_outcomes_keep_the_legacy_shape() {
        use okf_engine::write::{ApplyOutcome, ApplyReport};
        let result = |outcome| {
            serde_json::to_value(ApplyResult::from(ApplyReport {
                outcome,
                preview_token: Some("t".into()),
            }))
            .unwrap()
        };
        let written = result(ApplyOutcome::Written(vec!["a.md".into()]));
        assert_eq!(
            written,
            serde_json::json!({
                "changed_paths": ["a.md"], "skipped_paths": [], "succeeded": true,
                "written": true, "validation": [], "conflict_paths": [], "preview_token": "t"
            })
        );
        let mismatch = result(ApplyOutcome::TokenMismatch(vec!["a.md".into()]));
        assert_eq!(mismatch["succeeded"], false);
        assert_eq!(mismatch["conflict_paths"], serde_json::json!(["a.md"]));
        assert_eq!(
            mismatch["error"],
            "apply candidate no longer matches the reviewed preview"
        );
        let lossy = serde_json::to_value(ApplyResult::from(ApplyReport {
            outcome: ApplyOutcome::Lossy(vec!["b.md".into()]),
            preview_token: None,
        }))
        .unwrap();
        assert!(lossy.get("preview_token").is_none());
        assert_eq!(lossy["skipped_paths"], serde_json::json!(["b.md"]));
    }

    #[test]
    fn apply_plans_and_previews_in_one_request() {
        let root = bundle(
            "apply",
            &[
                ("a.md", "---\ntype: Note\nstatus: draft # keep\n---\n"),
                ("b.md", "---\ntype: Note\nstatus: final\n---\n"),
            ],
        );
        let sugar = serde_json::json!({
            "path": root, "type": "Note", "field": "status", "from": "draft", "to": "final"
        });
        let preview = serde_json::to_value(apply(&sugar.to_string()).unwrap()).unwrap();
        let broken = serde_json::json!({"path": root, "sql": "UPDATE Nope SET x = 1"});
        let failed = serde_json::to_value(apply(&broken.to_string()).unwrap()).unwrap();
        let incomplete = serde_json::json!({"path": root, "type": "Note"});
        let refused = serde_json::to_value(apply(&incomplete.to_string()).unwrap()).unwrap();
        let untouched = std::fs::read_to_string(root.join("a.md")).unwrap();
        std::fs::remove_dir_all(&root).unwrap();
        assert_eq!(
            preview["result"]["changed_paths"],
            serde_json::json!(["a.md"])
        );
        assert_eq!(preview["result"]["written"], false);
        assert!(untouched.contains("draft"));
        assert_eq!(failed["result"]["succeeded"], false);
        assert!(
            failed["result"]["error"]
                .as_str()
                .unwrap()
                .starts_with("script failed")
        );
        assert_eq!(refused["error"]["kind"], "request");
    }

    #[test]
    fn render_batches_documents_and_refuses_non_string_scalars() {
        let ok = serde_json::to_value(
            render(r##"{"documents": [{"frontmatter": {"type": "Note"}, "body": "# A\n"}]}"##)
                .unwrap(),
        )
        .unwrap();
        assert_eq!(
            ok["result"]["documents"],
            serde_json::json!(["---\ntype: Note\n---\n# A\n"])
        );
        let refused = serde_json::to_value(
            render(r#"{"documents": [{"frontmatter": {"type": "Note", "n": 1}}]}"#).unwrap(),
        )
        .unwrap();
        assert_eq!(refused["error"]["kind"], "request");
    }

    #[test]
    fn malformed_requests_are_rejected() {
        assert!(edit(r#"{"path": "/b", "unknown": 1}"#).is_err());
    }
}
