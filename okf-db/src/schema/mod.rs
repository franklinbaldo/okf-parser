//! `okf-parser schema`: one contract per concept type, compiled from the
//! bundle's frontmatter, explicit casts, declared `.schema.sql` types,
//! declared foreign keys and RFC 0018 projections, then rendered as JSON
//! Schema, Zod or GraphQL SDL. Pydantic output stays in the Python shell,
//! which builds it from these same contracts.

mod contract;
mod graphql;
mod json;
mod relations;
mod zod;

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::path::{Path, PathBuf};

use globset::GlobBuilder;
use okf_engine::specs::{SLUG_PLACEHOLDER, SpecTemplate};
use okf_engine::{LoadError, load_bundle};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub use contract::{
    CastKind, Contract, Field, Node, Reference, classify_lexemes, model_name, py_repr,
};
pub use graphql::{GraphqlSdl, render_graphql};
pub use json::render_json_schema;
pub use relations::PROJECTION_TYPE;
pub use zod::{ZodImport, render_zod};

use crate::declared::{DeclaredSchemaError, declared_schema_relative_path, parse_declared_schema};
use crate::relational::{RelationalSchemaError, load_relational_schema};

/// How a declared foreign key appears in flat exports.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RefsMode {
    /// The key's value, annotated as a reference.
    #[default]
    Key,
    /// The referenced type's schema itself.
    Embed,
}

/// What a schema export reads.
#[derive(Debug, Clone, Default)]
pub struct SchemaOptions {
    pub exclude: Vec<String>,
    pub infer_types: bool,
    pub casts: Vec<String>,
    pub spec_template: Option<String>,
    pub relational_schema: Option<String>,
    pub refs: RefsMode,
}

/// Why a bundle's contracts could not be compiled.
#[derive(Debug)]
pub enum SchemaError {
    Load(LoadError),
    Cast(String),
    NameCollision(String),
    /// Two authored names map to one GraphQL name.
    GraphqlNameCollision(String),
    Reference(String),
    Projection(String),
    /// Anything else the export refuses, with its reason.
    Export(String),
    SpecTemplate(String),
    Relational(RelationalSchemaError),
}

impl fmt::Display for SchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::Relational(error) => error.fmt(f),
            Self::Cast(message)
            | Self::NameCollision(message)
            | Self::GraphqlNameCollision(message)
            | Self::Reference(message)
            | Self::Projection(message)
            | Self::Export(message)
            | Self::SpecTemplate(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for SchemaError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::Relational(error) => Some(error),
            _ => None,
        }
    }
}

impl SchemaError {
    /// The Python exception family this error surfaces as.
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Load(_) => "io",
            Self::Cast(_) => "schema_cast",
            Self::NameCollision(_) => "schema_name_collision",
            Self::GraphqlNameCollision(_) => "graphql_name_collision",
            Self::Reference(_) => "schema_reference",
            Self::Projection(_) => "projection",
            Self::Export(_) => "schema_export",
            Self::SpecTemplate(_) => "spec_template",
            Self::Relational(_) => "relational_schema",
        }
    }
}

/// Every concept's frontmatter, grouped by its authored `type`.
fn documents_by_type(
    root: &Path,
    exclude: &[String],
) -> Result<BTreeMap<String, Vec<Map<String, Value>>>, SchemaError> {
    let data = load_bundle(root, exclude, okf_engine::check::READ_CONCURRENCY)
        .map_err(SchemaError::Load)?;
    let mut by_type: BTreeMap<String, Vec<Map<String, Value>>> = BTreeMap::new();
    for concept in data.concepts {
        let concept_type = if concept.concept_type.is_empty() {
            "concept".to_owned()
        } else {
            concept.concept_type
        };
        let frontmatter: Map<String, Value> = serde_json::from_str(&concept.frontmatter_json)
            .map_err(|_| {
                SchemaError::Export(format!(
                    "concept {} frontmatter is not an object",
                    py_repr(&concept.path)
                ))
            })?;
        by_type.entry(concept_type).or_default().push(frontmatter);
    }
    Ok(by_type)
}

/// Types whose specification has a sibling `.schema.sql`: a declared type is
/// a contract even before its first document exists.
fn declared_concept_types(
    root: &Path,
    template: SpecTemplate<'_>,
) -> Result<BTreeSet<String>, SchemaError> {
    let pattern = template.as_str().replace(SLUG_PLACEHOLDER, "*");
    // A template may leave the bundle (`../specs/{slug}.md`): walk from its
    // literal leading directories and match the rest below them.
    let components: Vec<&str> = pattern.split('/').collect();
    let literal = components[..components.len() - 1]
        .iter()
        .take_while(|component| !component.contains(['*', '?', '[', '{']))
        .count();
    let base = components[..literal]
        .iter()
        .fold(root.to_path_buf(), |base, component| base.join(component));
    let rest = components[literal..].join("/");
    let glob = GlobBuilder::new(&rest)
        .literal_separator(true)
        .build()
        .map_err(|error| SchemaError::Export(error.to_string()))?
        .compile_matcher();
    let mut specs: Vec<PathBuf> = walkdir::WalkDir::new(&base)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| {
            entry
                .path()
                .strip_prefix(&base)
                .is_ok_and(|relative| glob.is_match(relative))
        })
        .map(walkdir::DirEntry::into_path)
        .collect();
    specs.sort();
    let mut types = BTreeSet::new();
    for spec in specs {
        let text = std::fs::read_to_string(&spec).map_err(|error| {
            SchemaError::Export(format!(
                "invalid specification document at {}: {error}",
                spec.display()
            ))
        })?;
        let Some(frontmatter) = okf_engine::frontmatter_of(&text) else {
            return Err(SchemaError::Export(format!(
                "invalid specification document at {}: no parseable frontmatter",
                spec.display()
            )));
        };
        if frontmatter.get("type").and_then(Value::as_str) != Some("ConceptSpecification") {
            continue;
        }
        let Some(concept_type) = frontmatter
            .get("concept_type")
            .and_then(Value::as_str)
            .filter(|concept_type| !concept_type.is_empty())
        else {
            continue;
        };
        if declared_schema_relative_path(template, concept_type)
            .is_some_and(|relative| root.join(relative).is_file())
        {
            types.insert(concept_type.to_owned());
        }
    }
    Ok(types)
}

type DeclaredTypes = BTreeMap<String, BTreeMap<String, crate::catalog::LogicalType>>;

/// Each type's declared columns, keeping DuckDB's full catalog types.
fn declared_types(
    root: &Path,
    concept_types: &[&str],
    template: SpecTemplate<'_>,
) -> Result<DeclaredTypes, SchemaError> {
    let mut owners: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for concept_type in concept_types {
        if let Some(relative) = declared_schema_relative_path(template, concept_type) {
            owners.entry(relative).or_default().push(concept_type);
        }
    }
    let mut declared = BTreeMap::new();
    for (relative, types) in owners {
        let path = root.join(&relative);
        if !path.is_file() {
            continue;
        }
        if types.len() > 1 {
            let mut names: Vec<String> = types.iter().map(|t| py_repr(t)).collect();
            names.sort();
            return Err(SchemaError::Export(format!(
                "declared schema path collision at {}: {}",
                py_repr(&relative),
                names.join(", ")
            )));
        }
        let concept_type = types[0];
        let failed = |error: &dyn fmt::Display| {
            SchemaError::Export(format!(
                "invalid declared schema for {} at {}: {error}",
                py_repr(concept_type),
                py_repr(&relative)
            ))
        };
        let sql = std::fs::read_to_string(&path).map_err(|error| failed(&error))?;
        let schema = parse_declared_schema(&sql, concept_type)
            .map_err(|error: DeclaredSchemaError| failed(&error))?;
        if !schema.columns.is_empty() {
            declared.insert(
                concept_type.to_owned(),
                schema
                    .columns
                    .into_iter()
                    .map(|column| (column.name, column.logical_type))
                    .collect(),
            );
        }
    }
    Ok(declared)
}

/// Compile the bundle at `root` into one contract per concept type, then
/// its references and projections.
pub fn build_contracts(root: &Path, options: &SchemaOptions) -> Result<Vec<Contract>, SchemaError> {
    let template = options
        .spec_template
        .as_deref()
        .map(SpecTemplate::new)
        .transpose()
        .map_err(|error| SchemaError::SpecTemplate(error.to_string()))?;
    let mut observed = documents_by_type(root, &options.exclude)?;
    let projection_documents = observed.remove(PROJECTION_TYPE).unwrap_or_default();
    let mut declared = DeclaredTypes::new();
    if let Some(template) = template {
        for concept_type in declared_concept_types(root, template)? {
            observed.entry(concept_type).or_default();
        }
        let types: Vec<&str> = observed.keys().map(String::as_str).collect();
        declared = declared_types(root, &types, template)?;
    }
    let casts = contract::parse_casts(&options.casts)?;
    let contracts = contract::compile_contracts(
        &observed,
        contract::CompileOptions {
            infer_types: options.infer_types,
            casts,
            declared: &declared,
        },
    )?;

    let relational = match &options.relational_schema {
        Some(path) => {
            let declared = Path::new(path);
            let resolved = if declared.is_absolute() {
                declared.to_path_buf()
            } else {
                root.join(declared)
            };
            Some(load_relational_schema(&resolved).map_err(SchemaError::Relational)?)
        }
        None => None,
    };
    let concept_types: Vec<&str> = observed.keys().map(String::as_str).collect();
    let Some(relational) = relational else {
        if !projection_documents.is_empty() {
            return Err(SchemaError::Projection(format!(
                "bundle declares {} projection document(s) but no relational schema; pass \
                 --relational-schema so the relations can be resolved",
                projection_documents.len()
            )));
        }
        if options.refs == RefsMode::Embed {
            return Err(SchemaError::Export(
                "--refs=embed needs a relational schema to know what to embed".to_owned(),
            ));
        }
        return Ok(contracts);
    };
    let projections = relations::parse_projections(
        &projection_documents,
        &relational.foreign_keys,
        &concept_types,
    )?;
    let keyed = relations::apply_references(&contracts, &relational.foreign_keys, false)?;
    let mut flat = match options.refs {
        RefsMode::Key => keyed.clone(),
        RefsMode::Embed => relations::apply_references(&contracts, &relational.foreign_keys, true)?,
    };
    flat.extend(relations::compile_projections(&projections, &keyed)?);
    Ok(flat)
}
