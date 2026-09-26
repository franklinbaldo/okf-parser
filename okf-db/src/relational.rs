//! Bundle-level relational identity declared by trusted DuckDB SQL.
//!
//! The bundle's `okf.schema.sql` is run on its own connection and only its
//! PRIMARY KEY, UNIQUE and FOREIGN KEY constraints are read back from the
//! catalog. The checks themselves run over the concepts' frontmatter, one
//! concept type per table: `OKF020` (a key column is not a scalar string),
//! `OKF021` (a missing or duplicate key) and `OKF022` (a dangling reference).

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt;
use std::path::Path;

use duckdb::Connection;
use duckdb::types::Value as DbValue;
use okf_engine::{BundleData, Code, Diagnostic, Severity};
use serde::Serialize;
use serde_json::{Map, Value};

use crate::catalog::identifier_key;

/// A relational schema that could not be read or run.
#[derive(Debug)]
pub enum RelationalSchemaError {
    Read {
        path: String,
        error: std::io::Error,
    },
    Script(duckdb::Error),
    Catalog(duckdb::Error),
    /// A foreign key without its referenced table or columns.
    IncompleteForeignKey {
        name: String,
    },
}

impl fmt::Display for RelationalSchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read { path, error } => {
                write!(f, "could not read relational schema {path}: {error}")
            }
            Self::Script(error) => write!(f, "relational schema script failed: {error}"),
            Self::Catalog(error) => write!(f, "could not read the relational catalog: {error}"),
            Self::IncompleteForeignKey { name } => {
                write!(f, "foreign key `{name}` has incomplete catalog metadata")
            }
        }
    }
}

impl std::error::Error for RelationalSchemaError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Read { error, .. } => Some(error),
            Self::Script(error) | Self::Catalog(error) => Some(error),
            Self::IncompleteForeignKey { .. } => None,
        }
    }
}

/// One PRIMARY KEY or UNIQUE constraint over a concept type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct KeyConstraint {
    pub table: String,
    pub columns: Vec<String>,
    pub name: String,
    pub primary: bool,
}

/// One ordered foreign-key mapping between two concept types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ForeignKeyConstraint {
    pub table: String,
    pub columns: Vec<String>,
    pub referenced_table: String,
    pub referenced_columns: Vec<String>,
    pub name: String,
}

/// The recognized relational subset of the catalog.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct RelationalSchema {
    pub keys: Vec<KeyConstraint>,
    pub foreign_keys: Vec<ForeignKeyConstraint>,
}

fn strings(value: DbValue) -> Option<Vec<String>> {
    match value {
        DbValue::List(items) | DbValue::Array(items) => items
            .into_iter()
            .map(|item| match item {
                DbValue::Text(text) => Some(text),
                _ => None,
            })
            .collect(),
        _ => None,
    }
}

struct ConstraintRow {
    table: String,
    kind: String,
    columns: Option<Vec<String>>,
    name: Option<String>,
    referenced_table: Option<String>,
    referenced_columns: Option<Vec<String>>,
}

/// Run `sql_text` whole and read its key and foreign-key constraints back.
pub fn parse_relational_schema(sql_text: &str) -> Result<RelationalSchema, RelationalSchemaError> {
    let connection = Connection::open_in_memory().map_err(RelationalSchemaError::Catalog)?;
    connection
        .execute_batch(sql_text)
        .map_err(RelationalSchemaError::Script)?;
    let tables: HashSet<String> = connection
        .prepare("SELECT table_name FROM duckdb_tables() WHERE NOT temporary")
        .and_then(|mut statement| {
            statement
                .query_map([], |row| row.get(0))?
                .collect::<Result<_, _>>()
        })
        .map_err(RelationalSchemaError::Catalog)?;
    let rows = connection
        .prepare(
            "SELECT table_name, constraint_type, constraint_column_names, constraint_name, \
             referenced_table, referenced_column_names \
             FROM duckdb_constraints() ORDER BY table_name, constraint_index",
        )
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok(ConstraintRow {
                        table: row.get(0)?,
                        kind: row.get(1)?,
                        columns: strings(row.get(2)?),
                        name: row.get(3)?,
                        referenced_table: row.get(4)?,
                        referenced_columns: strings(row.get(5)?),
                    })
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(RelationalSchemaError::Catalog)?;
    let mut schema = RelationalSchema::default();
    for row in rows {
        if !tables.contains(&row.table) {
            continue;
        }
        let name = row.name.filter(|name| !name.is_empty()).unwrap_or_else(|| {
            format!(
                "{}_{}",
                row.table,
                row.kind.to_lowercase().replace(' ', "_")
            )
        });
        let columns = row.columns.unwrap_or_default();
        match row.kind.as_str() {
            "PRIMARY KEY" | "UNIQUE" => schema.keys.push(KeyConstraint {
                primary: row.kind == "PRIMARY KEY",
                table: row.table,
                columns,
                name,
            }),
            "FOREIGN KEY" => {
                let (Some(referenced_table), Some(referenced_columns)) =
                    (row.referenced_table, row.referenced_columns)
                else {
                    return Err(RelationalSchemaError::IncompleteForeignKey { name });
                };
                schema.foreign_keys.push(ForeignKeyConstraint {
                    table: row.table,
                    columns,
                    referenced_table,
                    referenced_columns,
                    name,
                });
            }
            _ => {}
        }
    }
    Ok(schema)
}

/// Read and run the relational schema at `path`.
pub fn load_relational_schema(path: &Path) -> Result<RelationalSchema, RelationalSchemaError> {
    let sql_text = std::fs::read_to_string(path).map_err(|error| RelationalSchemaError::Read {
        path: path.display().to_string(),
        error,
    })?;
    parse_relational_schema(&sql_text)
}

struct Concept<'a> {
    path: &'a str,
    frontmatter: Map<String, Value>,
}

/// A JSON value's kind, for "found a list" style messages.
fn kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "a list",
        Value::Object(_) => "a mapping",
    }
}

/// `isbn = "123"`, or `(a, b) = ("1", "2")` for a compound key.
fn describe_key(columns: &[String], values: &[String]) -> String {
    let quoted: Vec<String> = values.iter().map(|value| format!("{value:?}")).collect();
    match (columns, quoted.as_slice()) {
        ([column], [value]) => format!("{column} = {value}"),
        _ => format!("({}) = ({})", columns.join(", "), quoted.join(", ")),
    }
}

/// One key column: absent, a string, or unusable (already diagnosed).
enum Cell {
    Absent,
    Text(String),
    Invalid,
}

struct Checker<'a> {
    concepts: BTreeMap<&'a str, Vec<Concept<'a>>>,
    diagnostics: Vec<Diagnostic>,
}

impl<'a> Checker<'a> {
    fn new(data: &'a BundleData) -> Self {
        let mut concepts: BTreeMap<&str, Vec<Concept<'_>>> = BTreeMap::new();
        for record in &data.concepts {
            let Ok(Value::Object(frontmatter)) = serde_json::from_str(&record.frontmatter_json)
            else {
                continue;
            };
            concepts
                .entry(record.concept_type.as_str())
                .or_default()
                .push(Concept {
                    path: &record.path,
                    frontmatter,
                });
        }
        Self {
            concepts,
            diagnostics: Vec::new(),
        }
    }

    fn of_type(&self, table: &str) -> &[Concept<'a>] {
        self.concepts.get(table).map_or(&[], Vec::as_slice)
    }

    /// The values of `columns` in `concept`: `Some(None)` when one is
    /// absent, `None` when one is unusable (and diagnosed as `OKF020`).
    fn key(
        concept: &Concept<'_>,
        table: &str,
        columns: &[String],
        constraint: &str,
        diagnostics: &mut Vec<Diagnostic>,
    ) -> Option<Option<Vec<String>>> {
        let mut values = Vec::with_capacity(columns.len());
        let mut invalid = false;
        for column in columns {
            let cell = cell(concept, table, column, constraint, diagnostics);
            invalid |= matches!(cell, Cell::Invalid);
            values.push(cell);
        }
        if invalid {
            return None;
        }
        Some(
            values
                .into_iter()
                .map(|cell| match cell {
                    Cell::Text(text) => Some(text),
                    Cell::Absent | Cell::Invalid => None,
                })
                .collect(),
        )
    }

    fn check_key(&mut self, constraint: &KeyConstraint) {
        let mut diagnostics = Vec::new();
        let mut seen: HashMap<Vec<String>, &str> = HashMap::new();
        for concept in self.of_type(&constraint.table) {
            let Some(key) = Self::key(
                concept,
                &constraint.table,
                &constraint.columns,
                &constraint.name,
                &mut diagnostics,
            ) else {
                continue;
            };
            let Some(key) = key else {
                if constraint.primary {
                    diagnostics.push(error(
                        Code::Okf021,
                        concept.path,
                        format!(
                            "`{}` has no {} but primary key `{}` requires it",
                            constraint.table,
                            constraint.columns.join(", "),
                            constraint.name
                        ),
                    ));
                }
                continue;
            };
            if let Some(first) = seen.get(&key) {
                diagnostics.push(error(
                    Code::Okf021,
                    concept.path,
                    format!(
                        "`{}` {} is already used by {first} (constraint `{}`)",
                        constraint.table,
                        describe_key(&constraint.columns, &key),
                        constraint.name
                    ),
                ));
            } else {
                seen.insert(key, concept.path);
            }
        }
        self.diagnostics.extend(diagnostics);
    }

    fn check_foreign_key(&mut self, constraint: &ForeignKeyConstraint) {
        let mut diagnostics = Vec::new();
        let targets: HashSet<Vec<String>> = self
            .of_type(&constraint.referenced_table)
            .iter()
            .filter_map(|concept| {
                Self::key(
                    concept,
                    &constraint.referenced_table,
                    &constraint.referenced_columns,
                    &constraint.name,
                    &mut diagnostics,
                )
                .flatten()
            })
            .collect();
        for concept in self.of_type(&constraint.table) {
            let Some(Some(key)) = Self::key(
                concept,
                &constraint.table,
                &constraint.columns,
                &constraint.name,
                &mut diagnostics,
            ) else {
                continue;
            };
            if !targets.contains(&key) {
                diagnostics.push(error(
                    Code::Okf022,
                    concept.path,
                    format!(
                        "`{}` {} matches no `{}` {} (foreign key `{}`)",
                        constraint.table,
                        describe_key(&constraint.columns, &key),
                        constraint.referenced_table,
                        constraint.referenced_columns.join(", "),
                        constraint.name
                    ),
                ));
            }
        }
        self.diagnostics.extend(diagnostics);
    }
}

fn error(code: Code, path: &str, message: String) -> Diagnostic {
    Diagnostic {
        code,
        severity: Severity::Error,
        path: path.to_owned(),
        message,
    }
}

fn cell(
    concept: &Concept<'_>,
    table: &str,
    field: &str,
    constraint: &str,
    diagnostics: &mut Vec<Diagnostic>,
) -> Cell {
    let wanted = identifier_key(field);
    let mut matches: Vec<&String> = concept
        .frontmatter
        .keys()
        .filter(|name| identifier_key(name) == wanted)
        .collect();
    match matches.as_slice() {
        [] => Cell::Absent,
        [name] => match &concept.frontmatter[name.as_str()] {
            Value::Null => Cell::Absent,
            Value::String(text) => Cell::Text(text.clone()),
            other => {
                diagnostics.push(error(
                    Code::Okf020,
                    concept.path,
                    format!(
                        "`{table}`.`{field}` must be a single string for constraint \
                         `{constraint}`, but it is {}",
                        kind(other)
                    ),
                ));
                Cell::Invalid
            }
        },
        _ => {
            matches.sort();
            let names: Vec<String> = matches.iter().map(|name| format!("`{name}`")).collect();
            diagnostics.push(error(
                Code::Okf020,
                concept.path,
                format!(
                    "`{table}`.`{field}` is ambiguous for constraint `{constraint}`: \
                     frontmatter keys {} are the same DuckDB identifier; keep one",
                    names.join(" and ")
                ),
            ));
            Cell::Invalid
        }
    }
}

/// `OKF020`-`OKF022` for every concept in `data` against `schema`, keys
/// first, then foreign keys, each in constraint order.
pub fn validate_relations(data: &BundleData, schema: &RelationalSchema) -> Vec<Diagnostic> {
    let mut checker = Checker::new(data);
    for constraint in &schema.keys {
        checker.check_key(constraint);
    }
    for constraint in &schema.foreign_keys {
        checker.check_foreign_key(constraint);
    }
    checker.diagnostics
}

#[cfg(test)]
mod tests {
    use super::*;
    use okf_engine::ConceptRecord;

    const SCHEMA: &str = "CREATE TABLE Book (isbn VARCHAR PRIMARY KEY, slug VARCHAR UNIQUE);\n\
        CREATE TABLE Chapter (book VARCHAR REFERENCES Book(isbn), title VARCHAR);";

    fn concept(path: &str, concept_type: &str, frontmatter: serde_json::Value) -> ConceptRecord {
        ConceptRecord {
            concept_id: path.to_owned(),
            logical_key: path.to_owned(),
            path: path.to_owned(),
            concept_type: concept_type.to_owned(),
            title: None,
            description: None,
            source_digest: String::new(),
            parsed_digest: String::new(),
            frontmatter_json: frontmatter.to_string(),
            body: String::new(),
        }
    }

    fn bundle(concepts: Vec<ConceptRecord>) -> BundleData {
        BundleData {
            root: "/b".into(),
            concepts,
            reserved: Vec::new(),
            links: Vec::new(),
            diagnostics: Vec::new(),
            markdown_count: 0,
        }
    }

    fn messages(data: &BundleData) -> Vec<(Code, String, String)> {
        let schema = parse_relational_schema(SCHEMA).unwrap();
        validate_relations(data, &schema)
            .into_iter()
            .map(|d| (d.code, d.path, d.message))
            .collect()
    }

    #[test]
    fn keys_and_foreign_keys_come_from_the_catalog() {
        let schema = parse_relational_schema(SCHEMA).unwrap();
        assert_eq!(schema.keys.len(), 2);
        assert!(
            schema
                .keys
                .iter()
                .any(|key| key.primary && key.columns == ["isbn"])
        );
        assert_eq!(schema.foreign_keys[0].referenced_table, "Book");
        assert_eq!(schema.foreign_keys[0].referenced_columns, ["isbn"]);
    }

    #[test]
    fn a_consistent_bundle_has_no_diagnostics() {
        let data = bundle(vec![
            concept(
                "a.md",
                "Book",
                serde_json::json!({"type": "Book", "isbn": "1"}),
            ),
            concept(
                "c.md",
                "Chapter",
                serde_json::json!({"type": "Chapter", "book": "1"}),
            ),
            concept("d.md", "Chapter", serde_json::json!({"type": "Chapter"})),
        ]);
        assert!(messages(&data).is_empty());
    }

    #[test]
    fn duplicates_missing_keys_and_dangling_references_are_reported() {
        let data = bundle(vec![
            concept(
                "a.md",
                "Book",
                serde_json::json!({"isbn": "1", "slug": "s"}),
            ),
            concept(
                "b.md",
                "Book",
                serde_json::json!({"ISBN": "1", "slug": "s"}),
            ),
            concept("n.md", "Book", serde_json::json!({"slug": "t"})),
            concept("c.md", "Chapter", serde_json::json!({"book": "9"})),
            concept("l.md", "Chapter", serde_json::json!({"book": ["1"]})),
        ]);
        let found = messages(&data);
        assert!(
            found
                .iter()
                .any(|(code, path, message)| *code == Code::Okf021
                    && path == "b.md"
                    && message.starts_with("`Book` isbn = \"1\" is already used by a.md"))
        );
        assert!(
            found
                .iter()
                .any(|(code, path, message)| *code == Code::Okf021
                    && path == "n.md"
                    && message.contains("has no isbn"))
        );
        assert!(
            found
                .iter()
                .any(|(code, path, message)| *code == Code::Okf022
                    && path == "c.md"
                    && message.starts_with("`Chapter` book = \"9\" matches no `Book` isbn"))
        );
        assert!(
            found
                .iter()
                .any(|(code, path, message)| *code == Code::Okf020
                    && path == "l.md"
                    && message.contains("must be a single string")
                    && message.ends_with("it is a list"))
        );
    }

    #[test]
    fn keys_spelled_twice_are_ambiguous() {
        let data = bundle(vec![concept(
            "a.md",
            "Book",
            serde_json::json!({"isbn": "1", "ISBN": "2"}),
        )]);
        let found = messages(&data);
        assert!(found.iter().any(|(code, _, message)| *code == Code::Okf020
            && message.contains("`ISBN` and `isbn` are the same DuckDB identifier")));
    }
}
