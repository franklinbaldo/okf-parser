//! The read-only bundle reports: `check`, its classification, and `inventory`.
//!
//! Each report is computed from one [`BundleData`] load, so the native
//! diagnostics stay the only authority on bundle semantics.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::Path;

use serde::Serialize;

use crate::specs::{
    SpecTemplate, SpecTemplateError, missing_type_specs, required_type_spec_fields,
};
use crate::{
    BundleData, BundlePath, Diagnostic, LoadError, ReservedFile, Severity, discover_unfiltered,
    load_bundle,
};

/// How many documents a check reads at once.
pub const READ_CONCURRENCY: usize = 32;

/// Why a check could not produce a report.
#[derive(Debug)]
pub enum CheckError {
    Load(LoadError),
    SpecTemplate(SpecTemplateError),
}

impl fmt::Display for CheckError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::SpecTemplate(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for CheckError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::SpecTemplate(error) => Some(error),
        }
    }
}

impl From<LoadError> for CheckError {
    fn from(error: LoadError) -> Self {
        Self::Load(error)
    }
}

impl From<SpecTemplateError> for CheckError {
    fn from(error: SpecTemplateError) -> Self {
        Self::SpecTemplate(error)
    }
}

/// The optional type-specification rules of a check.
#[derive(Debug, Clone, Copy, Default)]
pub struct SpecRules<'a> {
    /// Require every type in use to have the document this template derives.
    pub require_spec: Option<&'a str>,
    /// Report the spec rules as errors rather than warnings.
    pub normative: bool,
}

/// How candidate Markdown participated in a check (explanatory, not a
/// second conformance mode).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Classification {
    pub concepts: Vec<String>,
    pub reserved: Vec<String>,
    pub ignored: Vec<String>,
    pub invalid_or_untyped: Vec<String>,
}

/// The result of checking one bundle.
#[derive(Debug, Clone, Serialize)]
pub struct CheckReport {
    pub root: String,
    pub conformant: bool,
    pub markdown_count: usize,
    pub concept_count: usize,
    pub reserved_count: usize,
    pub diagnostics: Vec<Diagnostic>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub classification: Option<Classification>,
}

/// Sort diagnostics by path, severity, code and message.
pub fn order(diagnostics: &mut [Diagnostic]) {
    diagnostics.sort_by(|a, b| {
        (&a.path, a.severity, a.code, &a.message).cmp(&(&b.path, b.severity, b.code, &b.message))
    });
}

/// Check the bundle at `root`: the native diagnostics, the optional
/// type-specification rules, and optionally a classification of every
/// candidate Markdown file.
pub fn check(
    root: &Path,
    exclude: &[String],
    rules: SpecRules<'_>,
    classify: bool,
) -> Result<CheckReport, CheckError> {
    let template = rules.require_spec.map(SpecTemplate::new).transpose()?;
    let data = load_bundle(root, exclude, READ_CONCURRENCY)?;
    let classification = classify.then(|| classification(&data)).transpose()?;
    let mut diagnostics = data.diagnostics.clone();
    if let Some(template) = template {
        let bundle_root = Path::new(&data.root);
        let types = data.concepts.iter().map(|c| c.concept_type.as_str());
        diagnostics.extend(missing_type_specs(
            bundle_root,
            types,
            template,
            rules.normative,
        ));
        diagnostics.extend(required_type_spec_fields(
            bundle_root,
            &data.concepts,
            template,
            rules.normative,
        ));
    }
    order(&mut diagnostics);
    Ok(CheckReport {
        conformant: !diagnostics.iter().any(|d| d.severity == Severity::Error),
        markdown_count: data.markdown_count,
        concept_count: data.concepts.len(),
        reserved_count: data.reserved.len(),
        root: data.root,
        diagnostics,
        classification,
    })
}

/// Classify every candidate Markdown file against one load of the bundle.
///
/// The candidates come from an unfiltered walk (no `.okfignore`, no
/// `--exclude`), so a file the rules hid shows up as `ignored`.
pub fn classification(data: &BundleData) -> Result<Classification, LoadError> {
    let root = Path::new(&data.root);
    let all_concepts = data.concepts.iter().map(|c| c.path.as_str());
    let typed: BTreeSet<&str> = data
        .concepts
        .iter()
        .filter(|c| !c.concept_type.is_empty())
        .map(|c| c.path.as_str())
        .collect();
    let active: BTreeSet<&str> = all_concepts
        .chain(data.reserved.iter().map(|r| r.path.as_str()))
        .chain(data.diagnostics.iter().map(|d| d.path.as_str()))
        .collect();
    let reserved: BTreeSet<&str> = active
        .iter()
        .copied()
        .filter(|path| ReservedFile::of(Path::new(path)).is_some())
        .collect();
    let candidates = discover_unfiltered(root)?
        .iter()
        .map(|path| BundlePath::new(root, path).map(BundlePath::into_string))
        .collect::<Result<BTreeSet<_>, _>>()?;
    let owned = |paths: BTreeSet<&str>| paths.into_iter().map(str::to_owned).collect();
    Ok(Classification {
        ignored: candidates
            .iter()
            .filter(|path| !active.contains(path.as_str()))
            .cloned()
            .collect(),
        invalid_or_untyped: owned(
            active
                .iter()
                .copied()
                .filter(|path| !typed.contains(path) && !reserved.contains(path))
                .collect(),
        ),
        concepts: owned(typed),
        reserved: owned(reserved),
    })
}

/// How many concepts use one producer-defined type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TypeCount {
    pub concept_type: String,
    pub concept_count: usize,
}

/// One concept's identity and content digests.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConceptDigest {
    pub concept_id: String,
    pub path: String,
    pub source_digest: String,
    pub parsed_digest: String,
}

/// Concepts counted by type, and optionally every concept's digests.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Inventory {
    pub root: String,
    pub types: Vec<TypeCount>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub digests: Option<Vec<ConceptDigest>>,
}

/// Count `data`'s concepts by type (untyped concepts count under `""`).
pub fn inventory(data: BundleData, digests: bool) -> Inventory {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for concept in &data.concepts {
        *counts.entry(concept.concept_type.as_str()).or_default() += 1;
    }
    let types = counts
        .into_iter()
        .map(|(concept_type, concept_count)| TypeCount {
            concept_type: concept_type.to_owned(),
            concept_count,
        })
        .collect();
    let digests = digests.then(|| {
        let mut rows: Vec<ConceptDigest> = data
            .concepts
            .into_iter()
            .map(|concept| ConceptDigest {
                concept_id: concept.concept_id,
                path: concept.path,
                source_digest: concept.source_digest,
                parsed_digest: concept.parsed_digest,
            })
            .collect();
        rows.sort_by(|a, b| a.path.cmp(&b.path));
        rows
    });
    Inventory {
        root: data.root,
        types,
        digests,
    }
}
