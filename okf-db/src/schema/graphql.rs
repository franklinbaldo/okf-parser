//! Contracts as read-only GraphQL SDL.
//!
//! Every concept type implements the generic `Concept` interface; its own
//! scalar fields follow, renamed to GraphQL names where they must be (the
//! original name is kept in `@okfField`). The Python shell builds the
//! executable schema from this SDL and the returned name mapping.

use std::collections::BTreeMap;

use serde::Serialize;
use unicode_normalization::UnicodeNormalization;

use super::SchemaError;
use super::contract::{CastKind, Contract, Field, Node};
use crate::catalog::{LogicalType, TypeFamily};

const GENERIC_FIELDS: [&str; 13] = [
    "id",
    "logicalKey",
    "path",
    "type",
    "title",
    "description",
    "sourceDigest",
    "parsedDigest",
    "body",
    "frontmatter",
    "links",
    "reverseLinks",
    "diagnostics",
];

const GENERIC_LINES: [&str; 13] = [
    "  id: ID!",
    "  logicalKey: String",
    "  path: String!",
    "  type: String!",
    "  title: String",
    "  description: String",
    "  sourceDigest: String!",
    "  parsedDigest: String!",
    "  body: String!",
    "  frontmatter: JSON!",
    "  links: [Link!]!",
    "  reverseLinks: [Link!]!",
    "  diagnostics: [Diagnostic!]!",
];

/// One concept type's GraphQL names.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphqlType {
    pub name: String,
    /// Each GraphQL field name and the authored field it reads.
    pub fields: BTreeMap<String, String>,
}

/// The SDL and, per concept type, the names it uses.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GraphqlSdl {
    pub sdl: String,
    pub types: BTreeMap<String, GraphqlType>,
}

fn ascii_identifier(value: &str, prefix: &str) -> String {
    let ascii: String = value.nfkd().filter(char::is_ascii).collect();
    let mut identifier = String::with_capacity(ascii.len());
    for c in ascii.chars() {
        let c = if c == '_' || c.is_ascii_alphanumeric() {
            c
        } else {
            '_'
        };
        if !(c == '_' && identifier.ends_with('_')) {
            identifier.push(c);
        }
    }
    let mut identifier = identifier.trim_matches('_').to_owned();
    if identifier.is_empty() {
        identifier = prefix.to_owned();
    }
    if identifier.starts_with(|c: char| c.is_ascii_digit()) {
        identifier = format!("{prefix}_{identifier}");
    }
    identifier
}

fn field_name(name: &str) -> String {
    let plain = name
        .chars()
        .next()
        .is_some_and(|c| c == '_' || c.is_ascii_alphabetic())
        && name.chars().all(|c| c == '_' || c.is_ascii_alphanumeric());
    if plain && !GENERIC_FIELDS.contains(&name) && !name.starts_with("__") {
        name.to_owned()
    } else {
        format!("field_{}", ascii_identifier(name, "value"))
    }
}

fn declared_scalar(declared: &LogicalType) -> &'static str {
    match declared.family {
        TypeFamily::Integer => {
            let small = matches!(
                declared.sql.to_uppercase().as_str(),
                "TINYINT" | "SMALLINT" | "INTEGER" | "UTINYINT" | "USMALLINT"
            );
            if small { "Int" } else { "BigInt" }
        }
        TypeFamily::String => "String",
        TypeFamily::Boolean => "Boolean",
        TypeFamily::Float => "Float",
        TypeFamily::Decimal => "Decimal",
        TypeFamily::Date => "Date",
        TypeFamily::Timestamp | TypeFamily::Timestamptz => "DateTime",
        TypeFamily::Uuid => "UUID",
        TypeFamily::List | TypeFamily::Unsupported => "JSON",
    }
}

fn node_type(node: &Node) -> String {
    match node {
        Node::Scalar {
            kind,
            declared_type,
        } => match declared_type {
            Some(declared) => declared_scalar(declared),
            None => match kind {
                CastKind::String => "String",
                CastKind::Boolean => "Boolean",
                CastKind::Integer => "BigInt",
                CastKind::Number => "Float",
                CastKind::Date => "Date",
                CastKind::Datetime => "DateTime",
            },
        }
        .to_owned(),
        Node::Literal { .. } => "String".to_owned(),
        Node::List {
            item,
            item_nullable,
            ..
        } => {
            let mut item = node_type(item);
            if !item_nullable {
                item.push('!');
            }
            format!("[{item}]")
        }
        Node::Ref(reference) => node_type(&reference.value),
        Node::Any | Node::Object { .. } => "JSON".to_owned(),
    }
}

fn field_type(field: &Field) -> String {
    let rendered = node_type(&field.value);
    if field.required && !field.nullable {
        format!("{rendered}!")
    } else {
        rendered
    }
}

fn literal(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

/// Every contract as a GraphQL object type implementing `Concept`.
pub fn render_graphql(contracts: &[Contract]) -> Result<GraphqlSdl, SchemaError> {
    let collision = SchemaError::GraphqlNameCollision;
    let mut types: BTreeMap<String, GraphqlType> = BTreeMap::new();
    let mut type_owners: BTreeMap<String, &str> = BTreeMap::new();
    let mut lines: Vec<String> = [
        "scalar JSON",
        "scalar BigInt",
        "scalar Decimal",
        "scalar Date",
        "scalar DateTime",
        "scalar UUID",
        "",
        "directive @okfType(name: String!) on OBJECT",
        "directive @okfField(name: String!) on FIELD_DEFINITION",
        "",
        "type Link {",
        "  sourceId: ID!",
        "  rawTarget: String!",
        "  targetId: ID",
        "  exists: Boolean!",
        "  origin: String!",
        "}",
        "",
        "type Diagnostic {",
        "  code: String!",
        "  severity: String!",
        "  path: String!",
        "  message: String!",
        "}",
        "",
        "interface Concept {",
    ]
    .map(str::to_owned)
    .to_vec();
    lines.extend(GENERIC_LINES.map(str::to_owned));
    lines.extend(["}".to_owned(), String::new()]);

    for contract in contracts {
        let type_name = ascii_identifier(&contract.model_name, "Concept");
        if let Some(previous) = type_owners.get(&type_name)
            && *previous != contract.concept_type
        {
            return Err(collision(format!(
                "concept types {} and {} both map to GraphQL type {}",
                super::py_repr(previous),
                super::py_repr(&contract.concept_type),
                super::py_repr(&type_name)
            )));
        }
        type_owners.insert(type_name.clone(), &contract.concept_type);
        lines.push(format!(
            "type {type_name} implements Concept @okfType(name: {}) {{",
            literal(&contract.concept_type)
        ));
        lines.extend(GENERIC_LINES.map(str::to_owned));
        let mut fields: BTreeMap<String, String> = BTreeMap::new();
        for field in &contract.fields {
            if matches!(field.name.as_str(), "type" | "title" | "description") {
                continue;
            }
            let name = field_name(&field.name);
            if let Some(previous) = fields.get(&name)
                && *previous != field.name
            {
                return Err(collision(format!(
                    "fields {0}.{previous} and {0}.{1} both map to GraphQL field {type_name}.{name}",
                    contract.concept_type, field.name
                )));
            }
            let rendered = field_type(field);
            if name == field.name {
                lines.push(format!("  {name}: {rendered}"));
            } else {
                lines.push(format!(
                    "  {name}: {rendered} @okfField(name: {})",
                    literal(&field.name)
                ));
            }
            fields.insert(name, field.name.clone());
        }
        lines.extend(["}".to_owned(), String::new()]);
        types.insert(
            contract.concept_type.clone(),
            GraphqlType {
                name: type_name,
                fields,
            },
        );
    }
    lines.extend(
        [
            "type Query {",
            "  concept(id: ID!): Concept",
            "  concepts(type: String, first: Int = 50, offset: Int = 0): [Concept!]!",
            "}",
        ]
        .map(str::to_owned),
    );
    let mut sdl = lines.join("\n");
    sdl.push('\n');
    Ok(GraphqlSdl { sdl, types })
}
