//! Contracts as canonical JSON Schema.

use serde_json::{Map, Value, json};

use super::contract::{CastKind, Contract, Field, Node, Reference};
use crate::catalog::{LogicalType, TypeFamily};

fn declared_scalar(declared: &LogicalType) -> Map<String, Value> {
    let mut schema = Map::new();
    schema.insert("x-okf-duckdb-type".to_owned(), json!(declared.sql));
    let mut set = |key: &str, value: Value| {
        schema.insert(key.to_owned(), value);
    };
    match declared.family {
        TypeFamily::String => set("type", json!("string")),
        TypeFamily::Boolean => set("type", json!("boolean")),
        TypeFamily::Integer => set("type", json!("integer")),
        TypeFamily::Float => set("type", json!("number")),
        TypeFamily::Decimal => {
            set("type", json!("number"));
            if let Some(scale) = declared.scale.filter(|scale| *scale > 0) {
                set("multipleOf", json!(10f64.powf(-f64::from(scale))));
            }
        }
        TypeFamily::Date => {
            set("type", json!("string"));
            set("format", json!("date"));
        }
        TypeFamily::Timestamp => {
            set("type", json!("string"));
            set("x-okf-temporal-kind", json!("timestamp-without-time-zone"));
        }
        TypeFamily::Timestamptz => {
            set("type", json!("string"));
            set("format", json!("date-time"));
        }
        TypeFamily::Uuid => {
            set("type", json!("string"));
            set("format", json!("uuid"));
        }
        TypeFamily::List | TypeFamily::Unsupported => {}
    }
    schema
}

fn scalar(kind: CastKind, declared: Option<&LogicalType>) -> Map<String, Value> {
    if let Some(declared) = declared {
        return declared_scalar(declared);
    }
    let value = match kind {
        CastKind::Boolean => json!({"type": "boolean"}),
        CastKind::Integer => json!({"type": "integer"}),
        CastKind::Number => json!({"type": "number"}),
        CastKind::Date => json!({"type": "string", "format": "date"}),
        CastKind::Datetime => json!({"type": "string", "format": "date-time"}),
        CastKind::String => json!({"type": "string"}),
    };
    object(value)
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

fn reference(reference: &Reference) -> Map<String, Value> {
    let mut schema = if reference.embedded {
        let mut schema = Map::new();
        schema.insert(
            "$ref".to_owned(),
            json!(format!("#/$defs/{}", reference.concept_type)),
        );
        schema
    } else {
        node(&reference.value)
    };
    schema.insert("x-okf-references".to_owned(), reference.metadata());
    schema
}

/// One canonical JSON Schema fragment.
pub fn node(node: &Node) -> Map<String, Value> {
    match node {
        Node::Ref(reference_node) => reference(reference_node),
        Node::Scalar {
            kind,
            declared_type,
        } => scalar(*kind, declared_type.as_ref()),
        Node::Literal { value } => object(json!({"type": "string", "const": value})),
        Node::Any => Map::new(),
        Node::List {
            item,
            item_nullable,
            declared_type,
        } => {
            let mut item = Value::Object(self::node(item));
            if *item_nullable {
                item = json!({"anyOf": [item, {"type": "null"}]});
            }
            let mut schema = object(json!({"type": "array", "items": item}));
            if let Some(declared) = declared_type {
                schema.insert("x-okf-duckdb-type".to_owned(), json!(declared.sql));
            }
            schema
        }
        Node::Object { fields } => fields_schema(fields),
    }
}

fn title(name: &str) -> String {
    let mut chars = name.chars();
    chars.next().map_or_else(String::new, |first| {
        first.to_uppercase().chain(chars).collect()
    })
}

fn fields_schema(fields: &[Field]) -> Map<String, Value> {
    let mut properties = Map::new();
    let mut required = Vec::new();
    for field in fields {
        let base = node(&field.value);
        let mut property = if field.nullable {
            object(json!({"anyOf": [base, {"type": "null"}]}))
        } else {
            base
        };
        property.insert("title".to_owned(), json!(title(&field.name)));
        properties.insert(field.name.clone(), Value::Object(property));
        if field.required {
            required.push(json!(field.name));
        }
    }
    let mut schema = object(json!({"type": "object", "properties": properties}));
    if !required.is_empty() {
        schema.insert("required".to_owned(), Value::Array(required));
    }
    schema
}

fn has_embedded(node: &Node) -> bool {
    match node {
        Node::Ref(reference) => reference.embedded,
        Node::List { item, .. } => has_embedded(item),
        Node::Object { fields } => fields.iter().any(|field| has_embedded(&field.value)),
        _ => false,
    }
}

/// One complete concept schema.
pub fn contract_schema(contract: &Contract) -> Value {
    let mut schema = fields_schema(&contract.fields);
    schema.insert("title".to_owned(), json!(contract.model_name));
    Value::Object(schema)
}

/// The `schema --format json` payload: every contract's schema, plus the
/// shared definitions when a reference embeds another type.
pub fn render_json_schema(
    root: &str,
    contracts: &[Contract],
    infer_types: bool,
    casts: &[String],
) -> Value {
    let schemas: Map<String, Value> = contracts
        .iter()
        .map(|contract| (contract.concept_type.clone(), contract_schema(contract)))
        .collect();
    let mut payload = json!({
        "root": root,
        "total_types": schemas.len(),
        "inferred_types": infer_types,
        "casts": casts,
        "schemas": schemas,
    });
    if contracts.iter().any(|contract| {
        contract
            .fields
            .iter()
            .any(|field| has_embedded(&field.value))
    }) {
        payload["defs"] = payload["schemas"].clone();
    }
    payload
}
