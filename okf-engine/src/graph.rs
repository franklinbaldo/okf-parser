//! The concept graph: concepts as nodes, resolved Markdown links as edges.
//!
//! This is the one definition of "the bundle's graph" that every surface
//! (the MCP `graph` tool, `Bundle.graph()` in Python) projects from, so it
//! lives next to ingestion rather than in any adapter.

use std::collections::HashMap;

use petgraph::algo::{connected_components, is_cyclic_directed, tarjan_scc};
use petgraph::graph::{DiGraph, NodeIndex};
use serde::Serialize;

use crate::engine::{BundleData, ConceptRecord, LinkRecord};

pub struct ConceptGraph<'a> {
    graph: DiGraph<&'a ConceptRecord, &'a LinkRecord>,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct GraphSummary {
    pub nodes: usize,
    pub edges: usize,
    pub weakly_connected_components: usize,
    pub strongly_connected_components: usize,
    pub directed_acyclic: bool,
}

impl<'a> ConceptGraph<'a> {
    /// Project a loaded bundle into a directed multigraph.
    ///
    /// A link becomes an edge only when its target resolved to a concept: a
    /// target that exists on disk but never parsed must not appear as an
    /// attribute-less node.
    pub fn from_bundle(data: &'a BundleData) -> Self {
        let mut graph = DiGraph::new();
        let mut index: HashMap<&str, NodeIndex> = HashMap::new();
        for concept in &data.concepts {
            index
                .entry(concept.concept_id.as_str())
                .or_insert_with(|| graph.add_node(concept));
        }
        for link in &data.links {
            let Some(target) = link.target_id.as_deref() else {
                continue;
            };
            if let (Some(&source), Some(&target)) =
                (index.get(link.source_id.as_str()), index.get(target))
            {
                graph.add_edge(source, target, link);
            }
        }
        Self { graph }
    }

    pub fn summary(&self) -> GraphSummary {
        GraphSummary {
            nodes: self.graph.node_count(),
            edges: self.graph.edge_count(),
            weakly_connected_components: connected_components(&self.graph),
            strongly_connected_components: tarjan_scc(&self.graph).len(),
            directed_acyclic: !is_cyclic_directed(&self.graph),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn concept(id: &str) -> ConceptRecord {
        ConceptRecord {
            concept_id: id.into(),
            logical_key: id.into(),
            path: format!("{id}.md"),
            concept_type: "note".into(),
            title: None,
            description: None,
            source_digest: String::new(),
            parsed_digest: String::new(),
            frontmatter_json: "{}".into(),
            body: String::new(),
        }
    }

    fn link(source: &str, target: Option<&str>) -> LinkRecord {
        LinkRecord {
            source_id: source.into(),
            raw_target: format!("{}.md", target.unwrap_or("missing")),
            target_id: target.map(Into::into),
            exists: target.is_some(),
            origin: "body".into(),
        }
    }

    fn bundle(ids: &[&str], links: Vec<LinkRecord>) -> BundleData {
        BundleData {
            root: "/bundle".into(),
            concepts: ids.iter().map(|id| concept(id)).collect(),
            reserved: Vec::new(),
            links,
            diagnostics: Vec::new(),
            markdown_count: ids.len(),
        }
    }

    #[test]
    fn empty_bundle_is_an_empty_acyclic_graph() {
        let data = bundle(&[], Vec::new());
        assert_eq!(
            ConceptGraph::from_bundle(&data).summary(),
            GraphSummary {
                nodes: 0,
                edges: 0,
                weakly_connected_components: 0,
                strongly_connected_components: 0,
                directed_acyclic: true,
            }
        );
    }

    #[test]
    fn chain_is_one_weak_component_of_singleton_strong_components() {
        let data = bundle(
            &["a", "b", "c", "d"],
            vec![link("a", Some("b")), link("b", Some("c"))],
        );
        let summary = ConceptGraph::from_bundle(&data).summary();
        assert_eq!(summary.nodes, 4);
        assert_eq!(summary.edges, 2);
        assert_eq!(summary.weakly_connected_components, 2);
        assert_eq!(summary.strongly_connected_components, 4);
        assert!(summary.directed_acyclic);
    }

    #[test]
    fn cycle_collapses_into_one_strong_component() {
        let data = bundle(
            &["a", "b", "c"],
            vec![
                link("a", Some("b")),
                link("b", Some("c")),
                link("c", Some("a")),
            ],
        );
        let summary = ConceptGraph::from_bundle(&data).summary();
        assert_eq!(summary.strongly_connected_components, 1);
        assert!(!summary.directed_acyclic);
    }

    #[test]
    fn self_link_makes_the_graph_cyclic() {
        let data = bundle(&["a"], vec![link("a", Some("a"))]);
        let summary = ConceptGraph::from_bundle(&data).summary();
        assert_eq!(summary.edges, 1);
        assert!(!summary.directed_acyclic);
    }

    #[test]
    fn parallel_links_are_kept_as_distinct_edges() {
        let data = bundle(
            &["a", "b"],
            vec![link("a", Some("b")), link("a", Some("b"))],
        );
        assert_eq!(ConceptGraph::from_bundle(&data).summary().edges, 2);
    }

    #[test]
    fn unresolved_and_non_concept_targets_are_not_edges() {
        let data = bundle(
            &["a"],
            vec![
                link("a", None),
                link("a", Some("index-that-failed-to-parse")),
            ],
        );
        let summary = ConceptGraph::from_bundle(&data).summary();
        assert_eq!(summary.nodes, 1);
        assert_eq!(summary.edges, 0);
    }
}
