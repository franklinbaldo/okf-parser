//! RFC 0006 typed tables: one table per declared concept type, each declared
//! field as a lossless raw column beside its typed column, every undeclared
//! scalar field as VARCHAR.
//!
//! Typed values are `TRY_CAST` per value, so one malformed date is NULL in
//! the typed column while its authored text survives in the raw one.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;

use duckdb::types::Value as DbValue;
use duckdb::{Connection, params_from_iter};
use okf_engine::BundleData;
use okf_engine::specs::split_lines;
use serde::Serialize;
use serde_json::{Map, Value};

use crate::catalog::{LogicalType, Schema, TypeFamily, identifier_key, quote_ident, quote_literal};
use crate::declared::DeclaredSchema;

const OKF_PREFIX: &str = "__okf_";
const INTERNAL_COLUMNS: [(&str, &str); 5] = [
    ("__okf_path", "VARCHAR"),
    ("__okf_concept_id", "VARCHAR"),
    ("__okf_logical_key", "VARCHAR"),
    ("__okf_body", "VARCHAR"),
    ("__okf_body_lines", "VARCHAR[]"),
];

/// Why the typed tables could not be planned or written.
#[derive(Debug)]
pub enum TypedTableError {
    /// Two frontmatter keys resolve to one declared column.
    DeclaredAlias {
        path: String,
        column: String,
        keys: [String; 2],
    },
    /// Two spellings of one undeclared field across a type's documents.
    UndeclaredAlias {
        concept_type: String,
        keys: Vec<String>,
    },
    /// Two concept types are the same DuckDB identifier.
    TypeAlias {
        types: [String; 2],
    },
    /// The schema already holds more than one table for one type.
    AmbiguousTable {
        tables: Vec<String>,
    },
    /// The schema already holds these tables and overwriting was not asked for.
    Collision {
        schema: String,
        tables: Vec<String>,
    },
    Db(duckdb::Error),
}

impl fmt::Display for TypedTableError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DeclaredAlias { path, column, keys } => write!(
                f,
                "{path}: frontmatter keys `{}` and `{}` both fill declared column `{column}` \
                 (DuckDB identifiers ignore ASCII case); keep one",
                keys[0], keys[1]
            ),
            Self::UndeclaredAlias { concept_type, keys } => write!(
                f,
                "type `{concept_type}` spells one field several ways ({}); DuckDB identifiers \
                 ignore ASCII case, so pick one spelling or declare the column",
                keys.iter()
                    .map(|key| format!("`{key}`"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            Self::TypeAlias { types } => write!(
                f,
                "concept types `{}` and `{}` would share one DuckDB table (identifiers ignore \
                 ASCII case)",
                types[0], types[1]
            ),
            Self::AmbiguousTable { tables } => write!(
                f,
                "the typed schema already has several tables for one type: {}",
                tables.join(", ")
            ),
            Self::Collision { schema, tables } => write!(
                f,
                "schema `{schema}` already contains {}",
                tables.join(", ")
            ),
            Self::Db(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for TypedTableError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Db(error) => Some(error),
            _ => None,
        }
    }
}

impl From<duckdb::Error> for TypedTableError {
    fn from(error: duckdb::Error) -> Self {
        Self::Db(error)
    }
}

/// One declared field: its type and the raw column carrying authored text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Declared {
    pub logical_type: LogicalType,
    pub raw_name: String,
}

/// One public column of a typed table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypedField {
    pub name: String,
    pub declared: Option<Declared>,
    pub comment: Option<String>,
}

impl TypedField {
    /// The column authored values are inserted into.
    fn insert_name(&self) -> &str {
        self.declared
            .as_ref()
            .map_or(self.name.as_str(), |declared| declared.raw_name.as_str())
    }
}

/// The DuckDB-independent plan of one declared concept type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypedTablePlan {
    pub concept_type: String,
    pub fields: Vec<TypedField>,
    pub table_comment: Option<String>,
}

/// What `materialize_typed_tables` left in the schema.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct TypedMaterialization {
    pub schema: String,
    pub tables: Vec<String>,
    pub unrecognized_tables: Vec<String>,
}

fn is_reserved(name: &str) -> bool {
    name.get(..OKF_PREFIX.len())
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case(OKF_PREFIX))
}

/// The authored key filling each declared column, by column name.
fn declared_aliases<'f>(
    path: &str,
    frontmatter: &'f Map<String, Value>,
    declared: &HashMap<String, &str>,
) -> Result<HashMap<String, &'f str>, TypedTableError> {
    let mut aliases: HashMap<String, &str> = HashMap::new();
    for authored in frontmatter.keys() {
        let Some(column) = declared.get(&identifier_key(authored)) else {
            continue;
        };
        if let Some(previous) = aliases.get(*column)
            && *previous != authored
        {
            return Err(TypedTableError::DeclaredAlias {
                path: path.to_owned(),
                column: (*column).to_owned(),
                keys: [(*previous).to_owned(), authored.clone()],
            });
        }
        aliases.insert((*column).to_owned(), authored);
    }
    Ok(aliases)
}

/// One concept's row source: its identity, body and frontmatter.
pub struct ConceptRow<'a> {
    pub path: &'a str,
    pub concept_id: &'a str,
    pub logical_key: &'a str,
    pub body: &'a str,
    pub frontmatter: Map<String, Value>,
}

/// Every concept of `data` with a frontmatter mapping, grouped by type.
pub fn rows_by_type(data: &BundleData) -> BTreeMap<&str, Vec<ConceptRow<'_>>> {
    let mut rows: BTreeMap<&str, Vec<ConceptRow<'_>>> = BTreeMap::new();
    for record in &data.concepts {
        let Ok(Value::Object(frontmatter)) = serde_json::from_str(&record.frontmatter_json) else {
            continue;
        };
        rows.entry(record.concept_type.as_str())
            .or_default()
            .push(ConceptRow {
                path: &record.path,
                concept_id: &record.concept_id,
                logical_key: &record.logical_key,
                body: &record.body,
                frontmatter,
            });
    }
    rows
}

fn declared_keys(declaration: &DeclaredSchema) -> HashMap<String, &str> {
    declaration
        .columns
        .iter()
        .filter(|column| !is_reserved(&column.name))
        .map(|column| (identifier_key(&column.name), column.name.as_str()))
        .collect()
}

/// Declared fields in catalog order, then the undeclared scalar fields the
/// documents use, in key order. A field that is a list or mapping on any
/// document is left out.
pub fn compile_plan(
    concept_type: &str,
    rows: &[ConceptRow<'_>],
    declaration: &DeclaredSchema,
) -> Result<TypedTablePlan, TypedTableError> {
    let declared = declared_keys(declaration);
    let mut spellings: BTreeMap<String, BTreeSet<&str>> = BTreeMap::new();
    let mut structured: BTreeSet<String> = BTreeSet::new();
    for row in rows {
        declared_aliases(row.path, &row.frontmatter, &declared)?;
        for (name, value) in &row.frontmatter {
            let key = identifier_key(name);
            if declared.contains_key(&key) || is_reserved(name) {
                continue;
            }
            spellings.entry(key.clone()).or_default().insert(name);
            if !matches!(value, Value::String(_) | Value::Null) {
                structured.insert(key);
            }
        }
    }
    let mut fields: Vec<TypedField> = declaration
        .columns
        .iter()
        .filter(|column| !is_reserved(&column.name))
        .map(|column| TypedField {
            name: column.name.clone(),
            declared: Some(Declared {
                logical_type: column.logical_type.clone(),
                raw_name: format!("__okf_raw_{}", column.name),
            }),
            comment: column.comment.clone(),
        })
        .collect();
    for (key, names) in spellings {
        if structured.contains(&key) {
            continue;
        }
        let names: Vec<&str> = names.into_iter().collect();
        let [name] = names.as_slice() else {
            return Err(TypedTableError::UndeclaredAlias {
                concept_type: concept_type.to_owned(),
                keys: names.iter().map(|name| (*name).to_owned()).collect(),
            });
        };
        fields.push(TypedField {
            name: (*name).to_owned(),
            declared: None,
            comment: None,
        });
    }
    Ok(TypedTablePlan {
        concept_type: concept_type.to_owned(),
        fields,
        table_comment: declaration.table_comment.clone(),
    })
}

fn ddl(plan: &TypedTablePlan, schema: &Schema) -> String {
    let mut columns: Vec<String> = INTERNAL_COLUMNS
        .iter()
        .map(|(name, kind)| format!("{} {kind}", quote_ident(name)))
        .collect();
    for field in &plan.fields {
        match &field.declared {
            None => columns.push(format!("{} VARCHAR", quote_ident(&field.name))),
            Some(declared) => {
                columns.push(format!(
                    "{} {}",
                    quote_ident(&declared.raw_name),
                    declared.logical_type.raw_sql_type()
                ));
                columns.push(format!(
                    "{} {}",
                    quote_ident(&field.name),
                    declared.logical_type.sql
                ));
            }
        }
    }
    format!(
        "CREATE TABLE {} (\n    {}\n)",
        schema.table(&plan.concept_type),
        columns.join(",\n    ")
    )
}

/// A list parameter, bound as JSON text and cast back to `VARCHAR[]`.
fn list_value(items: impl IntoIterator<Item = Option<String>>) -> DbValue {
    let items: Vec<Value> = items
        .into_iter()
        .map(|item| item.map_or(Value::Null, Value::String))
        .collect();
    DbValue::Text(Value::Array(items).to_string())
}

fn text(value: Option<&Value>) -> DbValue {
    match value {
        Some(Value::String(text)) => DbValue::Text(text.clone()),
        _ => DbValue::Null,
    }
}

/// The authored value's lossless raw form for a declared type.
fn raw_value(value: Option<&Value>, logical_type: &LogicalType) -> DbValue {
    if logical_type.family != TypeFamily::List {
        return text(value);
    }
    let Some(Value::Array(items)) = value else {
        return DbValue::Null;
    };
    let strings: Option<Vec<Option<String>>> = items
        .iter()
        .map(|item| match item {
            Value::String(text) => Some(Some(text.clone())),
            Value::Null => Some(None),
            _ => None,
        })
        .collect();
    strings.map_or(DbValue::Null, list_value)
}

fn row_values(
    row: &ConceptRow<'_>,
    plan: &TypedTablePlan,
) -> Result<Vec<DbValue>, TypedTableError> {
    let declared: HashMap<String, &str> = plan
        .fields
        .iter()
        .filter(|field| field.declared.is_some())
        .map(|field| (identifier_key(&field.name), field.name.as_str()))
        .collect();
    let aliases = declared_aliases(row.path, &row.frontmatter, &declared)?;
    let mut values = vec![
        DbValue::Text(row.path.to_owned()),
        DbValue::Text(row.concept_id.to_owned()),
        DbValue::Text(row.logical_key.to_owned()),
        DbValue::Text(row.body.to_owned()),
        list_value(split_lines(row.body).map(|line| Some(line.to_owned()))),
    ];
    for field in &plan.fields {
        values.push(match &field.declared {
            Some(declared) => raw_value(
                aliases
                    .get(&field.name)
                    .and_then(|authored| row.frontmatter.get(*authored)),
                &declared.logical_type,
            ),
            None => text(row.frontmatter.get(&field.name)),
        });
    }
    Ok(values)
}

fn matching_table<'e>(
    existing: &'e [String],
    name: &str,
) -> Result<Option<&'e str>, TypedTableError> {
    let wanted = identifier_key(name);
    let matches: Vec<&String> = existing
        .iter()
        .filter(|table| identifier_key(table) == wanted)
        .collect();
    match matches.as_slice() {
        [] => Ok(None),
        [table] => Ok(Some(table.as_str())),
        _ => Err(TypedTableError::AmbiguousTable {
            tables: matches.into_iter().cloned().collect(),
        }),
    }
}

/// Comments a replaced table carried, so an overwrite keeps the ones its
/// declaration does not restate.
fn existing_comments(
    connection: &Connection,
    schema: &Schema,
    table: &str,
) -> Result<(Option<String>, HashMap<String, String>), duckdb::Error> {
    let key = [schema.catalog.as_str(), schema.name.as_str(), table];
    let table_comment = connection
        .prepare(
            "SELECT comment FROM duckdb_tables() \
             WHERE database_name = ? AND schema_name = ? AND table_name = ?",
        )?
        .query_map(key, |row| row.get::<_, Option<String>>(0))?
        .next()
        .transpose()?
        .flatten();
    let columns = connection
        .prepare(
            "SELECT column_name, comment FROM duckdb_columns() \
             WHERE database_name = ? AND schema_name = ? AND table_name = ? \
             AND comment IS NOT NULL",
        )?
        .query_map(key, |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<Result<_, _>>()?;
    Ok((table_comment, columns))
}

/// Cast every raw column into its typed column, in UTC when the build has a
/// time-zone setting at all, so a `TIMESTAMPTZ` never depends on the session.
fn populate_typed_columns(
    connection: &Connection,
    schema: &Schema,
    plan: &TypedTablePlan,
) -> Result<(), duckdb::Error> {
    let assignments: Vec<String> = plan
        .fields
        .iter()
        .filter_map(|field| {
            let declared = field.declared.as_ref()?;
            Some(format!(
                "{} = TRY_CAST({} AS {})",
                quote_ident(&field.name),
                quote_ident(&declared.raw_name),
                declared.logical_type.sql
            ))
        })
        .collect();
    if assignments.is_empty() {
        return Ok(());
    }
    let update = format!(
        "UPDATE {} SET {}",
        schema.table(&plan.concept_type),
        assignments.join(", ")
    );
    let timezone: Option<String> = connection
        .query_row("SELECT current_setting('TimeZone')", [], |row| row.get(0))
        .ok();
    let Some(previous) = timezone else {
        return connection.execute_batch(&update);
    };
    connection.execute_batch("SET TimeZone = 'UTC'")?;
    let result = connection.execute_batch(&update);
    connection.execute_batch(&format!("SET TimeZone = {}", quote_literal(&previous)))?;
    result
}

fn apply_comments(
    connection: &Connection,
    schema: &Schema,
    plan: &TypedTablePlan,
    prior_table: Option<String>,
    prior_columns: &HashMap<String, String>,
) -> Result<(), duckdb::Error> {
    let table = schema.table(&plan.concept_type);
    if let Some(comment) = plan.table_comment.clone().or(prior_table) {
        connection.execute_batch(&format!(
            "COMMENT ON TABLE {table} IS {}",
            quote_literal(&comment)
        ))?;
    }
    for field in &plan.fields {
        let comment = field
            .comment
            .as_ref()
            .or_else(|| prior_columns.get(&field.name));
        if let Some(comment) = comment {
            connection.execute_batch(&format!(
                "COMMENT ON COLUMN {table}.{} IS {}",
                quote_ident(&field.name),
                quote_literal(comment)
            ))?;
        }
    }
    Ok(())
}

/// Plan every declared type of `data`.
pub fn build_plans(
    data: &BundleData,
    declarations: &BTreeMap<String, DeclaredSchema>,
) -> Result<Vec<TypedTablePlan>, TypedTableError> {
    let rows = rows_by_type(data);
    declarations
        .iter()
        .map(|(concept_type, declaration)| {
            let type_rows = rows
                .get(concept_type.as_str())
                .map_or(&[][..], Vec::as_slice);
            compile_plan(concept_type, type_rows, declaration)
        })
        .collect()
}

/// Write every declared type table into `schema`, inside the caller's
/// transaction. Tables the schema holds for no declared type are reported,
/// never dropped.
pub fn materialize_typed_tables(
    connection: &Connection,
    data: &BundleData,
    schema: &Schema,
    declarations: &BTreeMap<String, DeclaredSchema>,
    overwrite: bool,
) -> Result<TypedMaterialization, TypedTableError> {
    let plans = build_plans(data, declarations)?;
    let existing = schema.tables(connection)?;
    let mut planned: HashMap<String, &str> = HashMap::new();
    for plan in &plans {
        let key = identifier_key(&plan.concept_type);
        if let Some(previous) = planned.insert(key, &plan.concept_type)
            && previous != plan.concept_type
        {
            return Err(TypedTableError::TypeAlias {
                types: [previous.to_owned(), plan.concept_type.clone()],
            });
        }
    }
    let mut collisions = Vec::new();
    for plan in &plans {
        if let Some(table) = matching_table(&existing, &plan.concept_type)? {
            collisions.push(table.to_owned());
        }
    }
    if !collisions.is_empty() && !overwrite {
        collisions.sort();
        return Err(TypedTableError::Collision {
            schema: schema.name.clone(),
            tables: collisions,
        });
    }
    let unrecognized = existing
        .iter()
        .filter(|table| !planned.contains_key(&identifier_key(table)))
        .cloned()
        .collect();
    let rows = rows_by_type(data);
    connection.execute_batch(&format!("CREATE SCHEMA IF NOT EXISTS {}", schema.sql()))?;
    for plan in &plans {
        let (prior_table, prior_columns) = match matching_table(&existing, &plan.concept_type)? {
            Some(table) => {
                let comments = existing_comments(connection, schema, table)?;
                connection.execute_batch(&format!("DROP TABLE {}", schema.table(table)))?;
                comments
            }
            None => (None, HashMap::new()),
        };
        connection.execute_batch(&ddl(plan, schema))?;
        let type_rows = rows
            .get(plan.concept_type.as_str())
            .map_or(&[][..], Vec::as_slice);
        if !type_rows.is_empty() {
            let columns: Vec<(&str, &str)> = INTERNAL_COLUMNS
                .iter()
                .copied()
                .chain(plan.fields.iter().map(|field| {
                    let kind = field
                        .declared
                        .as_ref()
                        .map_or("VARCHAR", |declared| declared.logical_type.raw_sql_type());
                    (field.insert_name(), kind)
                }))
                .collect();
            let names: Vec<String> = columns.iter().map(|(name, _)| quote_ident(name)).collect();
            let placeholders: Vec<String> = columns
                .iter()
                .map(|(_, kind)| {
                    if *kind == "VARCHAR[]" {
                        "CAST(CAST(? AS JSON) AS VARCHAR[])".to_owned()
                    } else {
                        "?".to_owned()
                    }
                })
                .collect();
            let mut insert = connection.prepare(&format!(
                "INSERT INTO {} ({}) VALUES ({})",
                schema.table(&plan.concept_type),
                names.join(", "),
                placeholders.join(", ")
            ))?;
            for row in type_rows {
                insert.execute(params_from_iter(row_values(row, plan)?))?;
            }
        }
        populate_typed_columns(connection, schema, plan)?;
        apply_comments(connection, schema, plan, prior_table, &prior_columns)?;
    }
    Ok(TypedMaterialization {
        schema: schema.name.clone(),
        tables: plans.into_iter().map(|plan| plan.concept_type).collect(),
        unrecognized_tables: unrecognized,
    })
}
