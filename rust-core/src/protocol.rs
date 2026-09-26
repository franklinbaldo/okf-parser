//! The JSON protocol between the Python shell and this binary (RFC 0024).
//!
//! Every response carries `protocol`, so the shell can refuse a binary it does
//! not understand instead of misreading it. Bump it on any breaking change to a
//! response shape; adding a field is not breaking.

use okf_engine::write::{
    EditOutcome, EditReport, EditRequest, ValidationItem, WriteError, edit_concept,
};
use std::path::PathBuf;

use okf_engine::check::{CheckError, CheckReport};
use okf_engine::specs::Scaffold;
use okf_engine::{BundleData, ConceptGraph, GraphSummary, LoadError};
use serde::{Deserialize, Serialize};

use crate::commands::InitError;

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
}

/// `__check`: the native check report, for the Python shell's `validate_path`.
pub fn check(request: &str) -> Result<Response<CheckReport>, serde_json::Error> {
    let request: CheckRequest = serde_json::from_str(request)?;
    let outcome = crate::commands::check(
        &request.path,
        &request.exclude,
        request.require_spec.as_deref(),
        request.normative_spec,
        request.classify,
    )
    .map_err(|error| match &error {
        CheckError::SpecTemplate(_) => ProtocolError::spec_template(&error),
        CheckError::Load(load) => ProtocolError::from_load(load),
    });
    Ok(outcome.into())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct InitSpecsRequest {
    path: PathBuf,
    spec_template: String,
    #[serde(default)]
    exclude: Vec<String>,
    #[serde(default)]
    write: bool,
}

/// `__init-specs`: the specification scaffold, for `init --infer-schema`.
pub fn init_specs(request: &str) -> Result<Response<Scaffold>, serde_json::Error> {
    let request: InitSpecsRequest = serde_json::from_str(request)?;
    let outcome = crate::commands::init_specs(
        &request.path,
        &request.exclude,
        &request.spec_template,
        request.write,
    )
    .map_err(|error| match &error {
        InitError::SpecTemplate(_) => ProtocolError::spec_template(&error),
        InitError::Load(load) => ProtocolError::from_load(load),
        InitError::Io(_) => ProtocolError::io(&error),
    });
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
        let value = serde_json::to_value(init_specs(init_request).unwrap()).unwrap();
        assert_eq!(value["error"]["kind"], "request");
    }

    #[test]
    fn malformed_requests_are_rejected() {
        assert!(edit(r#"{"path": "/b", "unknown": 1}"#).is_err());
    }
}
