//! Contracts as Zod declarations.

use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;

use super::SchemaError;
use super::contract::{CastKind, Contract, Node, Reference, model_name, unique_model_names};
use crate::catalog::{LogicalType, TypeFamily};

/// Where `z` is imported from.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ZodImport {
    #[default]
    Zod,
    /// Astro content collections re-export Zod.
    Astro,
}

/// A JSON string literal, which JavaScript reads the same way.
fn literal(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

fn declared_scalar(declared: &LogicalType) -> &'static str {
    match declared.family {
        TypeFamily::Integer => {
            let safe = matches!(
                declared.sql.to_uppercase().as_str(),
                "TINYINT" | "SMALLINT" | "INTEGER" | "UTINYINT" | "USMALLINT" | "UINTEGER"
            );
            if safe {
                "z.number().int()"
            } else {
                "z.union([z.number().int(), z.bigint()])"
            }
        }
        TypeFamily::String => "z.string()",
        TypeFamily::Boolean => "z.boolean()",
        TypeFamily::Float => "z.number()",
        TypeFamily::Decimal | TypeFamily::Timestamp => "z.string()",
        TypeFamily::Date => "z.iso.date()",
        TypeFamily::Timestamptz => "z.iso.datetime({ offset: true })",
        TypeFamily::Uuid => "z.uuid()",
        TypeFamily::List | TypeFamily::Unsupported => "z.unknown()",
    }
}

const fn scalar(kind: CastKind) -> &'static str {
    match kind {
        CastKind::Boolean => "z.boolean()",
        CastKind::Integer => "z.number().int()",
        CastKind::Number => "z.number()",
        CastKind::Date => "z.iso.date()",
        CastKind::Datetime => "z.iso.datetime({ offset: true, local: true })",
        CastKind::String => "z.string()",
    }
}

struct Renderer<'a> {
    names: &'a BTreeMap<String, String>,
    lazy: &'a BTreeSet<String>,
}

impl Renderer<'_> {
    fn variable(&self, concept_type: &str) -> String {
        self.names
            .get(concept_type)
            .cloned()
            .unwrap_or_else(|| model_name(concept_type, "Schema"))
    }

    fn reference(&self, reference: &Reference, indent: &str) -> String {
        if reference.embedded {
            let name = self.variable(&reference.concept_type);
            return if self.lazy.contains(&reference.concept_type) {
                format!("z.lazy(() => {name})")
            } else {
                name
            };
        }
        format!(
            "{}.describe({})",
            self.node(&reference.value, indent),
            literal(&reference.description())
        )
    }

    fn node(&self, node: &Node, indent: &str) -> String {
        match node {
            Node::Ref(reference) => self.reference(reference, indent),
            Node::Scalar {
                kind,
                declared_type,
            } => declared_type
                .as_ref()
                .map_or(scalar(*kind), declared_scalar)
                .to_owned(),
            Node::Literal { value } => format!("z.literal({})", literal(value)),
            Node::Any => "z.unknown()".to_owned(),
            Node::List {
                item,
                item_nullable,
                ..
            } => {
                let mut item = self.node(item, indent);
                if *item_nullable {
                    item.push_str(".nullable()");
                }
                format!("z.array({item})")
            }
            Node::Object { fields } => {
                let child = format!("{indent}  ");
                let rows: Vec<String> = fields
                    .iter()
                    .map(|field| {
                        let mut rendered = self.node(&field.value, &child);
                        if field.nullable {
                            rendered.push_str(".nullable()");
                        }
                        if !field.required {
                            rendered.push_str(".optional()");
                        }
                        format!("{child}{}: {rendered}", literal(&field.name))
                    })
                    .collect();
                format!("z.object({{\n{}\n{indent}}})", rows.join(",\n"))
            }
        }
    }
}

/// Declarations ordered so each embedded target precedes its user; the
/// targets a cycle makes impossible to order are closed with `z.lazy`.
fn declaration_order(contracts: &[Contract]) -> (Vec<&Contract>, BTreeSet<String>) {
    let by_type: BTreeMap<&str, &Contract> = contracts
        .iter()
        .map(|contract| (contract.concept_type.as_str(), contract))
        .collect();
    let mut ordered = Vec::with_capacity(contracts.len());
    let mut emitted = BTreeSet::new();
    let mut visiting: Vec<&str> = Vec::new();
    let mut lazy = BTreeSet::new();

    fn visit<'a>(
        concept_type: &'a str,
        by_type: &BTreeMap<&str, &'a Contract>,
        ordered: &mut Vec<&'a Contract>,
        emitted: &mut BTreeSet<&'a str>,
        visiting: &mut Vec<&'a str>,
        lazy: &mut BTreeSet<String>,
    ) {
        let Some(contract) = by_type.get(concept_type) else {
            return;
        };
        if emitted.contains(concept_type) {
            return;
        }
        if visiting.contains(&concept_type) {
            lazy.insert(concept_type.to_owned());
            return;
        }
        visiting.push(concept_type);
        for field in &contract.fields {
            if let Node::Ref(reference) = &field.value
                && reference.embedded
            {
                visit(
                    &reference.concept_type,
                    by_type,
                    ordered,
                    emitted,
                    visiting,
                    lazy,
                );
            }
        }
        visiting.pop();
        emitted.insert(concept_type);
        ordered.push(contract);
    }

    for contract in contracts {
        visit(
            &contract.concept_type,
            &by_type,
            &mut ordered,
            &mut emitted,
            &mut visiting,
            &mut lazy,
        );
    }
    (ordered, lazy)
}

/// Every contract as an exported Zod schema.
pub fn render_zod(contracts: &[Contract], import: ZodImport) -> Result<String, SchemaError> {
    let names = unique_model_names(contracts.iter().map(|c| c.concept_type.as_str()), "Schema")?;
    let (ordered, lazy) = declaration_order(contracts);
    let renderer = Renderer {
        names: &names,
        lazy: &lazy,
    };
    let mut lines = vec![
        "// Generated by okf-parser".to_owned(),
        match import {
            ZodImport::Zod => "import { z } from 'zod';",
            ZodImport::Astro => "import { z } from 'astro:content';",
        }
        .to_owned(),
        String::new(),
    ];
    for contract in ordered {
        let root = Node::Object {
            fields: contract.fields.clone(),
        };
        lines.push(format!(
            "export const {} = {};",
            names[&contract.concept_type],
            renderer.node(&root, "")
        ));
        lines.push(String::new());
    }
    Ok(lines.join("\n"))
}
