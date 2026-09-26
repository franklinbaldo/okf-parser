//! The JSON protocol between the Python shell and this binary (RFC 0024).
//!
//! Every response carries `protocol`, so the shell can refuse a binary it does
//! not understand instead of misreading it. Bump it on any breaking change to a
//! response shape; adding a field is not breaking.

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
}
