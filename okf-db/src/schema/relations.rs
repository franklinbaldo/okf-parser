//! Declared foreign keys as reference nodes (RFC 0018), and projection
//! documents composed from them (RFC 0018 section 5).
//!
//! A reference never invents a relationship: every one follows a `FOREIGN
//! KEY` declared in `okf.schema.sql`. A projection names one root type and
//! the declared relations to traverse; it is a composed shape, never a
//! concept type of its own.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

use super::SchemaError;
use super::contract::{CastKind, Contract, Field, Node, Reference, py_repr, unique_model_names};
use crate::relational::ForeignKeyConstraint;

/// The authored `type` of a projection document.
pub const PROJECTION_TYPE: &str = "Projection";

fn reference_error(message: String) -> SchemaError {
    SchemaError::Reference(message)
}

/// The contracts with every declared foreign key compiled to a reference. A
/// key whose type has no documents contributes nothing.
pub fn apply_references(
    contracts: &[Contract],
    foreign_keys: &[ForeignKeyConstraint],
    embed: bool,
) -> Result<Vec<Contract>, SchemaError> {
    if embed {
        let composite: Vec<String> = foreign_keys
            .iter()
            .filter(|key| key.columns.len() > 1)
            .map(|key| format!("{}({})", key.table, key.columns.join(", ")))
            .collect();
        if !composite.is_empty() {
            return Err(reference_error(format!(
                "cannot embed a composite foreign key: {}. Embedding replaces the key's columns \
                 with one member, and only a projection can name it; export with --refs=key, or \
                 declare a projection member for it",
                composite.join(", ")
            )));
        }
    }
    let mut by_table: BTreeMap<&str, Vec<&ForeignKeyConstraint>> = BTreeMap::new();
    let mut sorted: Vec<&ForeignKeyConstraint> = foreign_keys.iter().collect();
    sorted.sort_by(|a, b| (&a.table, &a.name).cmp(&(&b.table, &b.name)));
    for key in sorted {
        by_table.entry(key.table.as_str()).or_default().push(key);
    }
    contracts
        .iter()
        .map(
            |contract| match by_table.get(contract.concept_type.as_str()) {
                Some(keys) => referenced(contract, keys, embed),
                None => Ok(contract.clone()),
            },
        )
        .collect()
}

fn referenced(
    contract: &Contract,
    foreign_keys: &[&ForeignKeyConstraint],
    embed: bool,
) -> Result<Contract, SchemaError> {
    let mut positions: BTreeMap<&str, (&ForeignKeyConstraint, usize)> = BTreeMap::new();
    for key in foreign_keys {
        for (position, column) in key.columns.iter().enumerate() {
            positions.insert(column.as_str(), (key, position));
        }
    }
    let present: BTreeSet<&str> = contract.fields.iter().map(|f| f.name.as_str()).collect();
    let missing: Vec<String> = positions
        .keys()
        .filter(|column| !present.contains(*column))
        .map(|column| format!("{}.{column}", contract.concept_type))
        .collect();
    if !missing.is_empty() {
        return Err(reference_error(format!(
            "declared foreign key column is absent from every {} document: {}",
            contract.concept_type,
            missing.join(", ")
        )));
    }
    let mut fields = Vec::with_capacity(contract.fields.len());
    for field in &contract.fields {
        let Some((key, position)) = positions.get(field.name.as_str()) else {
            fields.push(field.clone());
            continue;
        };
        if matches!(field.value, Node::Ref(_)) {
            return Err(reference_error(format!(
                "{}.{} participates in more than one declared foreign key; a column can carry \
                 only one reference",
                key.table, field.name
            )));
        }
        fields.push(Field {
            value: Node::Ref(Reference {
                concept_type: key.referenced_table.clone(),
                columns: key.columns.clone(),
                referenced_columns: key.referenced_columns.clone(),
                position: *position,
                value: Box::new(field.value.clone()),
                embedded: embed,
            }),
            ..field.clone()
        });
    }
    Ok(Contract {
        fields,
        ..contract.clone()
    })
}

/// One declared member of a projection, resolved to its foreign key.
#[derive(Debug, Clone)]
pub struct Member {
    alias: String,
    concept_type: String,
    columns: Vec<String>,
    referenced_columns: Vec<String>,
    collection: bool,
    optional: bool,
}

/// A composed shape: one root type plus its declared members.
#[derive(Debug, Clone)]
pub struct Projection {
    name: String,
    root: String,
    members: Vec<Member>,
}

fn projection_error(message: String) -> SchemaError {
    SchemaError::Projection(message)
}

fn text<'a>(
    document: &'a Map<String, Value>,
    key: &str,
    missing: String,
) -> Result<&'a str, SchemaError> {
    document
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| projection_error(missing))
}

/// A value as Python's `repr` spells the frontmatter view of it.
fn value_repr(value: &Value) -> String {
    match value {
        Value::String(text) => py_repr(text),
        Value::Null => "None".to_owned(),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(value_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", py_repr(k), value_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => other.to_string(),
    }
}

fn member(
    entry: &Map<String, Value>,
    name: &str,
    root: &str,
    foreign_keys: &[ForeignKeyConstraint],
) -> Result<Member, SchemaError> {
    let unrecognized: Vec<&str> = entry
        .keys()
        .map(String::as_str)
        .filter(|key| !matches!(*key, "relation" | "as" | "optional"))
        .collect();
    if !unrecognized.is_empty() {
        return Err(projection_error(format!(
            "projection {}: unrecognized member key(s) {}; a projection declares composition only",
            py_repr(name),
            unrecognized.join(", ")
        )));
    }
    let relation = text(
        entry,
        "relation",
        format!("projection {}: member has no relation", py_repr(name)),
    )?;
    let alias = text(
        entry,
        "as",
        format!(
            "projection {}: member {} has no 'as' name",
            py_repr(name),
            py_repr(relation)
        ),
    )?;
    let (from_type, column) = relation
        .split_once('.')
        .map(|(from, column)| (from.trim(), column.trim()))
        .filter(|(from, column)| !from.is_empty() && !column.is_empty())
        .ok_or_else(|| {
            projection_error(format!(
                "projection {}: relation {} must be written 'FromType.field', naming a declared \
                 foreign key",
                py_repr(name),
                py_repr(relation)
            ))
        })?;
    let matches: Vec<&ForeignKeyConstraint> = foreign_keys
        .iter()
        .filter(|key| key.table == from_type && key.columns.iter().any(|c| c == column))
        .collect();
    let key = match matches.as_slice() {
        [] => {
            return Err(projection_error(format!(
                "projection {}: the relational contract does not declare a foreign key for {}; a \
                 projection cannot invent a relationship",
                py_repr(name),
                py_repr(relation)
            )));
        }
        [key] => *key,
        many => {
            let mut named: Vec<&str> = many.iter().map(|key| key.name.as_str()).collect();
            named.sort_unstable();
            return Err(projection_error(format!(
                "projection {}: {} is ambiguous, it participates in more than one declared \
                 foreign key ({})",
                py_repr(name),
                py_repr(relation),
                named.join(", ")
            )));
        }
    };
    // RFC 0007 makes N:1 the primitive: a key pointing at the root is the
    // root's list; a key on the root (or a self-reference) is one value.
    let collection = key.table != root;
    if collection && key.referenced_table != root {
        return Err(projection_error(format!(
            "projection {}: {} connects {} to {}, not {root}",
            py_repr(name),
            py_repr(relation),
            key.referenced_table,
            key.table
        )));
    }
    let optional = match entry.get("optional") {
        None => false,
        Some(Value::Bool(flag)) => *flag,
        Some(Value::String(flag)) if flag == "true" => true,
        Some(Value::String(flag)) if flag == "false" => false,
        Some(other) => {
            return Err(projection_error(format!(
                "projection {}: {} optional must be a boolean, got {}",
                py_repr(name),
                py_repr(relation),
                value_repr(other)
            )));
        }
    };
    Ok(Member {
        alias: alias.to_owned(),
        concept_type: if collection {
            key.table.clone()
        } else {
            key.referenced_table.clone()
        },
        columns: key.columns.clone(),
        referenced_columns: key.referenced_columns.clone(),
        collection,
        optional,
    })
}

fn projection(
    document: &Map<String, Value>,
    foreign_keys: &[ForeignKeyConstraint],
    concept_types: &[&str],
) -> Result<Projection, SchemaError> {
    let name = text(
        document,
        "name",
        "projection document has no name".to_owned(),
    )?;
    if concept_types.contains(&name) {
        return Err(projection_error(format!(
            "projection name {0} collides with concept type {0}; a projection is not a concept type",
            py_repr(name)
        )));
    }
    let root = text(
        document,
        "root",
        format!("projection {} has no root", py_repr(name)),
    )?;
    if !concept_types.contains(&root) {
        return Err(projection_error(format!(
            "projection {}: root {} is an unknown concept type",
            py_repr(name),
            py_repr(root)
        )));
    }
    let entries: Vec<&Map<String, Value>> = match document.get("include") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| {
                item.as_object().ok_or_else(|| {
                    projection_error(format!(
                        "projection {}: include member must be a mapping, got {}",
                        py_repr(name),
                        value_repr(item)
                    ))
                })
            })
            .collect::<Result<_, _>>()?,
        Some(_) => {
            return Err(projection_error(format!(
                "projection {}: include must be a list of members",
                py_repr(name)
            )));
        }
    };
    let members = entries
        .into_iter()
        .map(|entry| member(entry, name, root, foreign_keys))
        .collect::<Result<Vec<_>, _>>()?;
    let mut seen = BTreeSet::new();
    for member in &members {
        if !seen.insert(member.alias.as_str()) {
            return Err(projection_error(format!(
                "projection {} declares {} twice",
                py_repr(name),
                py_repr(&member.alias)
            )));
        }
    }
    Ok(Projection {
        name: name.to_owned(),
        root: root.to_owned(),
        members,
    })
}

/// Resolve projection documents against the declared foreign keys, by name.
pub fn parse_projections(
    documents: &[Map<String, Value>],
    foreign_keys: &[ForeignKeyConstraint],
    concept_types: &[&str],
) -> Result<Vec<Projection>, SchemaError> {
    let mut projections = documents
        .iter()
        .map(|document| projection(document, foreign_keys, concept_types))
        .collect::<Result<Vec<_>, _>>()?;
    projections.sort_by(|a, b| a.name.cmp(&b.name));
    for pair in projections.windows(2) {
        if pair[0].name == pair[1].name {
            return Err(projection_error(format!(
                "projection {} is declared twice",
                py_repr(&pair[0].name)
            )));
        }
    }
    Ok(projections)
}

/// Each projection as a contract: its root's fields, then one named
/// reference per member (a list for a collection).
pub fn compile_projections(
    projections: &[Projection],
    contracts: &[Contract],
) -> Result<Vec<Contract>, SchemaError> {
    let by_type: BTreeMap<&str, &Contract> = contracts
        .iter()
        .map(|contract| (contract.concept_type.as_str(), contract))
        .collect();
    let names = unique_model_names(projections.iter().map(|p| p.name.as_str()), "Projection")?;
    let mut compiled = Vec::with_capacity(projections.len());
    for projection in projections {
        let root = by_type.get(projection.root.as_str()).ok_or_else(|| {
            projection_error(format!(
                "projection {}: root contract {} was not compiled",
                py_repr(&projection.name),
                py_repr(&projection.root)
            ))
        })?;
        let mut fields = root.fields.clone();
        for member in &projection.members {
            if root.fields.iter().any(|field| field.name == member.alias) {
                return Err(projection_error(format!(
                    "projection {}: member {} collides with a field of the root contract",
                    py_repr(&projection.name),
                    py_repr(&member.alias)
                )));
            }
            let reference = Node::Ref(Reference {
                concept_type: member.concept_type.clone(),
                columns: member.columns.clone(),
                referenced_columns: member.referenced_columns.clone(),
                position: 0,
                value: Box::new(Node::Scalar {
                    kind: CastKind::String,
                    declared_type: None,
                }),
                embedded: true,
            });
            fields.push(Field {
                name: member.alias.clone(),
                required: true,
                nullable: member.optional,
                value: if member.collection {
                    Node::List {
                        item: Box::new(reference),
                        item_nullable: false,
                        declared_type: None,
                    }
                } else {
                    reference
                },
            });
        }
        compiled.push(Contract {
            concept_type: projection.name.clone(),
            model_name: names[&projection.name].clone(),
            fields,
        });
    }
    Ok(compiled)
}
