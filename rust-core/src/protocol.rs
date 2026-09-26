//! The JSON protocol between the Python shell and this binary (RFC 0024).
//!
//! Every response carries `protocol`, so the shell can refuse a binary it does
//! not understand instead of misreading it. Bump it on any breaking change to a
//! response shape; adding a field is not breaking.

use okf_engine::write::{EditRequest, EditResult, WriteError, edit_concept};
use okf_engine::{BundleData, ConceptGraph, GraphSummary};
use serde::Serialize;

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

/// A command's answer: exactly one of `result` or `error`.
#[derive(Debug, Serialize)]
pub struct Response<T: Serialize> {
    protocol: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<T>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ProtocolError>,
}

impl<T: Serialize> Response<T> {
    fn from_write(outcome: Result<T, WriteError>) -> Self {
        let (result, error) = match outcome {
            Ok(result) => (Some(result), None),
            Err(WriteError::Request(message)) => (
                None,
                Some(ProtocolError {
                    kind: "request",
                    message,
                }),
            ),
            Err(WriteError::Io(message)) => (
                None,
                Some(ProtocolError {
                    kind: "io",
                    message,
                }),
            ),
        };
        Self {
            protocol: PROTOCOL_VERSION,
            result,
            error,
        }
    }
}

/// `__edit`: preview or commit one concept's body replacement.
pub fn edit(request: &str) -> Result<Response<EditResult>, serde_json::Error> {
    let request: EditRequest = serde_json::from_str(request)?;
    Ok(Response::from_write(edit_concept(&request)))
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
    fn malformed_requests_are_rejected() {
        assert!(edit(r#"{"path": "/b", "unknown": 1}"#).is_err());
    }
}
