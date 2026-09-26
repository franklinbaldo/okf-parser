//! `init --infer-schema`: a starter `.schema.sql` for each type lacking one.
//!
//! Each scalar field gets the narrowest DuckDB type every observed value
//! casts into cleanly; a field that is always a list or mapping becomes
//! `JSON`; a field mixing both is left out for the author to decide.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use duckdb::Connection;
use duckdb::params_from_iter;
use duckdb::types::Value as DbValue;
use okf_engine::BundleData;
use okf_engine::specs::{Collision, Scaffold, SpecTemplate};
use okf_engine::write::create_exclusive;
use serde_json::Value;

use crate::catalog::quote_ident;
use crate::declared::declared_schema_relative_path;

/// A starter column's type.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum StarterKind {
    String,
    Boolean,
    Integer,
    Number,
    Date,
    Datetime,
    Json,
}

impl StarterKind {
    fn sql(self) -> &'static str {
        match self {
            Self::String => "VARCHAR",
            Self::Boolean => "BOOLEAN",
            Self::Integer => "BIGINT",
            Self::Number => "DOUBLE",
            Self::Date => "DATE",
            Self::Datetime => "TIMESTAMPTZ",
            Self::Json => "JSON",
        }
    }
}

/// How strictly a value must survive a candidate cast.
#[derive(Clone, Copy)]
enum Fit {
    /// Any successful cast.
    Cast,
    /// The cast prints back as the authored text: for the casts DuckDB makes
    /// lossy instead of refusing (`'10.50'` rounds to a `BIGINT`, a
    /// timestamp truncates to a `DATE`).
    RoundTrip,
    /// As `RoundTrip`, ignoring case: `True` is a boolean, `1` and `t` are not.
    RoundTripIgnoringCase,
}

/// Narrowest to widest; the first candidate every value fits wins.
const CANDIDATES: [(&str, StarterKind, Fit); 5] = [
    ("BOOLEAN", StarterKind::Boolean, Fit::RoundTripIgnoringCase),
    ("BIGINT", StarterKind::Integer, Fit::RoundTrip),
    ("DOUBLE", StarterKind::Number, Fit::Cast),
    ("DATE", StarterKind::Date, Fit::RoundTrip),
    ("TIMESTAMPTZ", StarterKind::Datetime, Fit::Cast),
];

/// The kind of every column with at least one non-null value, in one
/// DuckDB pass over all columns at once.
pub fn infer_kinds(
    columns: &BTreeMap<String, Vec<Option<String>>>,
) -> Result<BTreeMap<String, StarterKind>, duckdb::Error> {
    if columns.is_empty() {
        return Ok(BTreeMap::new());
    }
    let connection = Connection::open_in_memory()?;
    let definition: Vec<String> = columns
        .keys()
        .map(|name| format!("{} VARCHAR", quote_ident(name)))
        .collect();
    connection.execute_batch(&format!("CREATE TABLE t ({})", definition.join(", ")))?;
    let rows = columns.values().map(Vec::len).max().unwrap_or(0);
    if rows > 0 {
        let placeholders = vec!["?"; columns.len()].join(", ");
        let mut insert = connection.prepare(&format!("INSERT INTO t VALUES ({placeholders})"))?;
        for index in 0..rows {
            insert.execute(params_from_iter(columns.values().map(|values| {
                values
                    .get(index)
                    .cloned()
                    .flatten()
                    .map_or(DbValue::Null, DbValue::Text)
            })))?;
        }
    }
    let mut select = Vec::new();
    for name in columns.keys() {
        let column = quote_ident(name);
        select.push(format!("count({column})"));
        for (sql, _, fit) in CANDIDATES {
            let cast = format!("TRY_CAST({column} AS {sql})");
            select.push(match fit {
                Fit::Cast => format!("count({cast})"),
                Fit::RoundTrip => {
                    format!("count(*) FILTER (WHERE CAST({cast} AS VARCHAR) = {column})")
                }
                Fit::RoundTripIgnoringCase => {
                    format!("count(*) FILTER (WHERE CAST({cast} AS VARCHAR) = lower({column}))")
                }
            });
        }
    }
    let counts: Vec<i64> =
        connection.query_row(&format!("SELECT {} FROM t", select.join(", ")), [], |row| {
            (0..select.len()).map(|index| row.get(index)).collect()
        })?;
    let stride = CANDIDATES.len() + 1;
    let mut kinds = BTreeMap::new();
    for (name, counts) in columns.keys().zip(counts.chunks(stride)) {
        let non_null = counts[0];
        if non_null == 0 {
            continue;
        }
        let kind = CANDIDATES
            .iter()
            .zip(&counts[1..])
            .find(|(_, count)| **count == non_null)
            .map_or(StarterKind::String, |((_, kind, _), _)| *kind);
        kinds.insert(name.clone(), kind);
    }
    Ok(kinds)
}

/// A starter `CREATE TABLE`, columns in name order.
pub fn render_starter_schema(
    concept_type: &str,
    columns: &BTreeMap<String, StarterKind>,
) -> String {
    let lines: Vec<String> = columns
        .iter()
        .map(|(name, kind)| format!("    {} {}", quote_ident(name), kind.sql()))
        .collect();
    format!(
        "CREATE TABLE {} (\n{}\n);\n",
        quote_ident(concept_type),
        lines.join(",\n")
    )
}

/// The starter columns of one type, from its documents' frontmatter.
pub fn starter_columns(
    documents: &[serde_json::Map<String, Value>],
) -> Result<BTreeMap<String, StarterKind>, duckdb::Error> {
    let mut names = BTreeSet::new();
    let mut structured = BTreeSet::new();
    let mut scalar = BTreeSet::new();
    for document in documents {
        for (key, value) in document {
            if key == "type" {
                continue;
            }
            names.insert(key.as_str());
            match value {
                Value::Null => {}
                Value::Array(_) | Value::Object(_) => {
                    structured.insert(key.as_str());
                }
                _ => {
                    scalar.insert(key.as_str());
                }
            }
        }
    }
    let columns: BTreeMap<String, Vec<Option<String>>> = names
        .difference(&structured)
        .map(|name| {
            let values = documents
                .iter()
                .map(|document| match document.get(*name) {
                    None | Some(Value::Null) => None,
                    Some(Value::String(text)) => Some(text.clone()),
                    Some(other) => Some(other.to_string()),
                })
                .collect();
            ((*name).to_owned(), values)
        })
        .collect();
    let mut kinds = infer_kinds(&columns)?;
    for name in structured.difference(&scalar) {
        kinds.insert((*name).to_owned(), StarterKind::Json);
    }
    Ok(kinds)
}

/// Why starter schemas could not be proposed or written.
#[derive(Debug)]
pub enum InferError {
    Db(duckdb::Error),
    Io(std::io::Error),
}

impl std::fmt::Display for InferError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Db(error) => write!(f, "DuckDB: {error}"),
            Self::Io(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for InferError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Db(error) => Some(error),
            Self::Io(error) => Some(error),
        }
    }
}

/// Propose, or with `write` create, a starter `.schema.sql` for every type
/// whose declaration is missing and that has at least one inferable field.
/// A derived-path collision blocks the whole call; existing files are never
/// touched.
pub fn scaffold_starter_schemas(
    data: &BundleData,
    template: SpecTemplate<'_>,
    write: bool,
) -> Result<Scaffold, InferError> {
    let root = Path::new(&data.root);
    let mut documents: BTreeMap<&str, Vec<serde_json::Map<String, Value>>> = BTreeMap::new();
    for concept in &data.concepts {
        if let Ok(Value::Object(frontmatter)) = serde_json::from_str(&concept.frontmatter_json) {
            documents
                .entry(concept.concept_type.as_str())
                .or_default()
                .push(frontmatter);
        }
    }
    let mut by_path: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for concept_type in documents.keys() {
        if let Some(relative) = declared_schema_relative_path(template, concept_type) {
            by_path.entry(relative).or_default().push(concept_type);
        }
    }
    let collisions: Vec<Collision> = by_path
        .iter()
        .filter(|(_, types)| types.len() > 1)
        .map(|(path, types)| Collision {
            path: path.clone(),
            types: types.iter().map(|name| (*name).to_owned()).collect(),
        })
        .collect();
    if !collisions.is_empty() {
        return Ok(Scaffold {
            created: Vec::new(),
            would_create: Vec::new(),
            collisions,
            written: false,
        });
    }
    let mut planned = Vec::new();
    for (relative, types) in by_path {
        if root.join(&relative).is_file() {
            continue;
        }
        let concept_type = types[0];
        let columns = starter_columns(&documents[concept_type]).map_err(InferError::Db)?;
        if !columns.is_empty() {
            planned.push((relative, render_starter_schema(concept_type, &columns)));
        }
    }
    if !write {
        return Ok(Scaffold {
            created: Vec::new(),
            would_create: planned.into_iter().map(|(relative, _)| relative).collect(),
            collisions: Vec::new(),
            written: false,
        });
    }
    let mut created = Vec::new();
    for (relative, content) in planned {
        let destination = root.join(&relative);
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent).map_err(InferError::Io)?;
        }
        if create_exclusive(&destination, content.as_bytes()).map_err(InferError::Io)? {
            created.push(relative);
        }
    }
    Ok(Scaffold {
        created,
        would_create: Vec::new(),
        collisions: Vec::new(),
        written: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn column(values: &[Option<&str>]) -> Vec<Option<String>> {
        values
            .iter()
            .map(|value| value.map(str::to_owned))
            .collect()
    }

    #[test]
    fn the_narrowest_clean_cast_wins() {
        let columns = BTreeMap::from([
            ("flag".to_owned(), column(&[Some("true"), Some("False")])),
            ("bit".to_owned(), column(&[Some("1"), Some("0")])),
            ("n".to_owned(), column(&[Some("1"), None, Some("42")])),
            ("price".to_owned(), column(&[Some("10.50"), Some("3")])),
            ("day".to_owned(), column(&[Some("2026-01-15")])),
            (
                "at".to_owned(),
                column(&[Some("2026-01-15"), Some("2026-01-15T09:30:00Z")]),
            ),
            ("name".to_owned(), column(&[Some("Ada"), Some("7")])),
            ("empty".to_owned(), column(&[None, None])),
        ]);
        let kinds = infer_kinds(&columns).unwrap();
        assert_eq!(kinds["flag"], StarterKind::Boolean);
        assert_eq!(kinds["bit"], StarterKind::Integer);
        assert_eq!(kinds["n"], StarterKind::Integer);
        assert_eq!(kinds["price"], StarterKind::Number);
        assert_eq!(kinds["day"], StarterKind::Date);
        assert_eq!(kinds["at"], StarterKind::Datetime);
        assert_eq!(kinds["name"], StarterKind::String);
        assert!(!kinds.contains_key("empty"));
    }

    #[test]
    fn structured_fields_become_json_unless_mixed() {
        let documents: Vec<serde_json::Map<String, Value>> = [
            serde_json::json!({"type": "Note", "tags": ["a"], "mixed": "x", "n": "1"}),
            serde_json::json!({"type": "Note", "tags": [], "mixed": {"k": "v"}}),
        ]
        .into_iter()
        .map(|value| value.as_object().unwrap().clone())
        .collect();
        let columns = starter_columns(&documents).unwrap();
        assert_eq!(
            columns,
            BTreeMap::from([
                ("n".to_owned(), StarterKind::Integer),
                ("tags".to_owned(), StarterKind::Json),
            ])
        );
        assert_eq!(
            render_starter_schema("Note", &columns),
            "CREATE TABLE \"Note\" (\n    \"n\" BIGINT,\n    \"tags\" JSON\n);\n"
        );
    }
}
