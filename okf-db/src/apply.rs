//! `okf-parser apply`: frontmatter edits written as SQL (RFC 0005).
//!
//! Each concept type becomes a table (named for the exact `type`), each
//! scalar frontmatter field a `VARCHAR` column, in a fresh in-memory database
//! locked like `okf-parser sql`'s: no files, no network, no settings. The
//! script then runs whole, any statements in any order (`ALTER TABLE`,
//! `UPDATE`, `UPDATE ... FROM` across types, helper tables), and **the final
//! tables are the answer**, row by row, column by column:
//!
//! - a column with a value different from the authored one sets the field
//!   (whatever its SQL type, as text);
//! - a column that became NULL where the field had a value removes it;
//! - a column that no longer exists (dropped, or renamed, which moves its
//!   values to the new name) removes the field from every document that had it.
//!
//! How the script got there is never inspected. What it may not do: add or
//! remove rows, change the compiler-owned `__okf_*` columns, give a column the
//! name of a list or mapping field (those are never columns, so they cannot be
//! silently overwritten), or touch a field its `.schema.sql` declares: with a
//! spec template those are typed, read-only columns for filtering; without
//! one they are ordinary text fields.
//!
//! One consequence of reading the final state: `SET x = NULL` on a document
//! whose `x` is an explicit YAML `null` keeps it (NULL did not change);
//! dropping the column removes it.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fmt;
use std::path::Path;

use duckdb::Connection;
use duckdb::types::Value as DbValue;
use okf_engine::specs::{SpecTemplate, SpecTemplateError, split_lines};
use okf_engine::write::{ConceptChanges, PlannedConcept};
use serde_json::{Map, Value};

use crate::catalog::{identifier_key, quote_ident, quote_literal};
use crate::declared::{DeclaredSchema, DeclaredSchemaError, discover_declared_schemas};
use crate::typed::{ConceptRow, TypedTableError, TypedTablePlan, compile_plan};

const PROTECTED: [(&str, &str); 6] = [
    ("__okf_path", "VARCHAR"),
    ("__okf_concept_id", "VARCHAR"),
    ("__okf_logical_key", "VARCHAR"),
    ("__okf_body", "VARCHAR"),
    ("__okf_body_lines", "VARCHAR[]"),
    ("__okf_frontmatter", "VARCHAR"),
];
const OKF_PREFIX: &str = "__okf_";

/// Why an apply could not be planned. Nothing was written.
#[derive(Debug)]
pub enum PlanError {
    SpecTemplate(SpecTemplateError),
    Declared(DeclaredSchemaError),
    Typed {
        concept_type: String,
        error: TypedTableError,
    },
    /// An authored field uses the compiler's `__okf_` prefix.
    ReservedField {
        concept_type: String,
        field: String,
    },
    /// Two types, or two fields of one type, are one DuckDB identifier.
    Collision {
        concept_type: String,
        detail: String,
    },
    /// The script itself failed.
    Script(duckdb::Error),
    /// The script broke one of the rules the final state must satisfy.
    Contract(String),
    Db(duckdb::Error),
}

impl fmt::Display for PlanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::SpecTemplate(error) => error.fmt(f),
            Self::Declared(error) => error.fmt(f),
            Self::Typed {
                concept_type,
                error,
            } => write!(
                f,
                "type `{concept_type}` cannot become a typed table: {error}"
            ),
            Self::ReservedField {
                concept_type,
                field,
            } => write!(
                f,
                "type `{concept_type}` has a field `{field}` in the reserved __okf_ namespace"
            ),
            Self::Collision {
                concept_type,
                detail,
            } => write!(
                f,
                "type `{concept_type}` collides with another type or field under DuckDB's \
                 case-insensitive identifiers: {detail}"
            ),
            Self::Script(error) => write!(f, "script failed: {error}"),
            Self::Contract(message) => f.write_str(message),
            Self::Db(error) => write!(f, "DuckDB: {error}"),
        }
    }
}

impl std::error::Error for PlanError {}

impl From<duckdb::Error> for PlanError {
    fn from(error: duckdb::Error) -> Self {
        Self::Db(error)
    }
}

/// `UPDATE "type" SET "field" = 'to' WHERE "field" = 'from'`, quoted.
pub fn sugar_sql(concept_type: &str, field: &str, from: &str, to: &str) -> String {
    let field = quote_ident(field);
    format!(
        "UPDATE {} SET {field} = {} WHERE {field} = {}",
        quote_ident(concept_type),
        quote_literal(to),
        quote_literal(from)
    )
}

/// One type's table: what was materialized and what the script may change.
struct TypeTable<'a> {
    name: &'a str,
    concepts: Vec<&'a PlannedConcept>,
    /// Writable scalar or flat-list fields, in column order.
    fields: Vec<String>,
    /// Writable fields whose relational type is a list.
    lists: BTreeSet<String>,
    /// Compiler-owned columns (plus declared raw carriers).
    protected: Vec<String>,
    /// Declared, typed, read-only fields.
    declared: BTreeSet<String>,
    /// Fields that are a mapping, nested list, or scalar/list mixture: never writable.
    structured: BTreeSet<String>,
}

impl TypeTable<'_> {
    /// The columns whose values the script may not change: the compiler's,
    /// then the declared fields.
    fn owned(&self) -> Vec<String> {
        self.protected
            .iter()
            .chain(&self.declared)
            .cloned()
            .collect()
    }

    fn refuse_owned(&self, column: &str, what: &str) -> PlanError {
        let name = self.name;
        PlanError::Contract(if self.declared.contains(column) {
            format!(
                "the script {what} declared field `{name}`.`{column}`; declared fields are \
                 read-only with --spec-template (run apply without it to edit them as text)"
            )
        } else {
            format!(
                "the script {what} protected column `{name}`.`{column}`; apply owns the \
                 `__okf_*` columns"
            )
        })
    }
}

fn is_reserved(name: &str) -> bool {
    name.get(..OKF_PREFIX.len())
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case(OKF_PREFIX))
}

fn text(value: Option<&Value>) -> DbValue {
    match value {
        Some(Value::String(text)) => DbValue::Text(text.clone()),
        _ => DbValue::Null,
    }
}

fn json_list(items: impl IntoIterator<Item = Option<String>>) -> DbValue {
    let items: Vec<Value> = items
        .into_iter()
        .map(|item| item.map_or(Value::Null, Value::String))
        .collect();
    DbValue::Text(Value::Array(items).to_string())
}

fn protected_values(concept: &PlannedConcept) -> Vec<DbValue> {
    vec![
        DbValue::Text(concept.path.clone()),
        DbValue::Text(concept.concept_id.clone()),
        DbValue::Text(concept.concept_id.clone()),
        DbValue::Text(concept.body.clone()),
        json_list(split_lines(&concept.body).map(|line| Some(line.to_owned()))),
        DbValue::Text(concept.frontmatter_text.clone()),
    ]
}

fn placeholder(sql_type: &str) -> &'static str {
    if sql_type == "VARCHAR[]" {
        "CAST(CAST(? AS JSON) AS VARCHAR[])"
    } else {
        "?"
    }
}

fn insert(
    connection: &Connection,
    table: &str,
    columns: &[(String, String)],
    rows: impl IntoIterator<Item = Vec<DbValue>>,
) -> Result<(), PlanError> {
    let names: Vec<String> = columns.iter().map(|(name, _)| quote_ident(name)).collect();
    let marks: Vec<&str> = columns.iter().map(|(_, kind)| placeholder(kind)).collect();
    let mut statement = connection.prepare(&format!(
        "INSERT INTO {} ({}) VALUES ({})",
        quote_ident(table),
        names.join(", "),
        marks.join(", ")
    ))?;
    for row in rows {
        statement.execute(duckdb::params_from_iter(row))?;
    }
    Ok(())
}

/// The authored list values of a declared list field, or NULL.
fn flat_list(value: &Value) -> bool {
    matches!(value, Value::Array(items) if items.iter().all(|item| matches!(item, Value::String(_) | Value::Null)))
}

fn raw_list(value: Option<&Value>) -> DbValue {
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
    strings.map_or(DbValue::Null, json_list)
}

fn declared_value(frontmatter: &Map<String, Value>, field: &str, list: bool) -> DbValue {
    let wanted = identifier_key(field);
    let value = frontmatter
        .iter()
        .find(|(key, _)| identifier_key(key) == wanted)
        .map(|(_, value)| value);
    if list { raw_list(value) } else { text(value) }
}

fn create(connection: &Connection, name: &str, columns: &[String]) -> Result<(), PlanError> {
    connection
        .execute_batch(&format!(
            "CREATE TABLE {} ({})",
            quote_ident(name),
            columns.join(", ")
        ))
        .map_err(|error| PlanError::Collision {
            concept_type: name.to_owned(),
            detail: error.to_string(),
        })
}

fn materialize_plain(connection: &Connection, table: &TypeTable<'_>) -> Result<(), PlanError> {
    let mut columns: Vec<(String, String)> = PROTECTED
        .iter()
        .map(|(name, kind)| ((*name).to_owned(), (*kind).to_owned()))
        .collect();
    columns.extend(table.fields.iter().map(|field| {
        let kind = if table.lists.contains(field) {
            "VARCHAR[]"
        } else {
            "VARCHAR"
        };
        (field.clone(), kind.to_owned())
    }));
    let definition: Vec<String> = columns
        .iter()
        .map(|(name, kind)| format!("{} {kind}", quote_ident(name)))
        .collect();
    create(connection, table.name, &definition)?;
    insert(
        connection,
        table.name,
        &columns,
        table.concepts.iter().map(|concept| {
            let mut row = protected_values(concept);
            row.extend(table.fields.iter().map(|field| {
                if table.lists.contains(field) {
                    raw_list(concept.frontmatter.get(field))
                } else {
                    text(concept.frontmatter.get(field))
                }
            }));
            row
        }),
    )
}

fn materialize_typed(
    connection: &Connection,
    table: &TypeTable<'_>,
    plan: &TypedTablePlan,
    declaration: &DeclaredSchema,
) -> Result<(), PlanError> {
    let mut definition: Vec<String> = PROTECTED
        .iter()
        .map(|(name, kind)| format!("{} {kind}", quote_ident(name)))
        .collect();
    let mut columns: Vec<(String, String)> = PROTECTED
        .iter()
        .map(|(name, kind)| ((*name).to_owned(), (*kind).to_owned()))
        .collect();
    let declared_columns: HashMap<String, _> = declaration
        .columns
        .iter()
        .map(|column| (identifier_key(&column.name), column))
        .collect();
    let writable_list_keys: HashSet<String> = plan
        .fields
        .iter()
        .filter_map(|field| {
            field.declared.as_ref().and_then(|declared| {
                (declared.logical_type.raw_sql_type() == "VARCHAR[]")
                    .then(|| identifier_key(&field.name))
            })
        })
        .collect();
    for field in &plan.fields {
        match &field.declared {
            None => {
                definition.push(format!("{} VARCHAR", quote_ident(&field.name)));
                columns.push((field.name.clone(), "VARCHAR".to_owned()));
            }
            Some(declared) => {
                let raw_type = declared.logical_type.raw_sql_type();
                if raw_type == "VARCHAR[]" {
                    let nullable = declared_columns
                        .get(&identifier_key(&field.name))
                        .is_none_or(|column| column.nullable);
                    definition.push(format!(
                        "{} {}{}",
                        quote_ident(&field.name),
                        declared.logical_type.sql,
                        if nullable { "" } else { " NOT NULL" }
                    ));
                    columns.push((field.name.clone(), raw_type.to_owned()));
                } else {
                    definition.push(format!("{} {raw_type}", quote_ident(&declared.raw_name)));
                    definition.push(format!(
                        "{} {} GENERATED ALWAYS AS (TRY_CAST({} AS {})) VIRTUAL",
                        quote_ident(&field.name),
                        declared.logical_type.sql,
                        quote_ident(&declared.raw_name),
                        declared.logical_type.sql
                    ));
                    columns.push((declared.raw_name.clone(), raw_type.to_owned()));
                }
            }
        }
    }
    definition.extend(
        declaration
            .checks
            .iter()
            .filter(|check| {
                !check.columns.is_empty()
                    && check
                        .columns
                        .iter()
                        .all(|column| writable_list_keys.contains(&identifier_key(column)))
            })
            .map(|check| format!("CHECK ({})", check.expression)),
    );
    create(connection, table.name, &definition)?;
    insert(
        connection,
        table.name,
        &columns,
        table.concepts.iter().map(|concept| {
            let mut row = protected_values(concept);
            row.extend(plan.fields.iter().map(|field| match &field.declared {
                None => {
                    if table.lists.contains(&field.name) {
                        raw_list(concept.frontmatter.get(&field.name))
                    } else {
                        text(concept.frontmatter.get(&field.name))
                    }
                }
                Some(declared) if declared.logical_type.raw_sql_type() == "VARCHAR[]" => {
                    raw_list(concept.frontmatter.get(&field.name))
                }
                Some(declared) => declared_value(&concept.frontmatter, &field.name, false),
            }));
            row
        }),
    )
}

/// The columns of `table` as DuckDB stores them, or `None` if it is gone.
fn columns(connection: &Connection, table: &str) -> Result<Option<Vec<String>>, PlanError> {
    let names: Vec<String> = connection
        .prepare(
            "SELECT column_name FROM duckdb_columns() \
             WHERE schema_name = 'main' AND table_name = ? ORDER BY column_index",
        )?
        .query_map([table], |row| row.get(0))?
        .collect::<Result<_, _>>()?;
    Ok((!names.is_empty()).then_some(names))
}

/// Every row of `table` as text, keyed by concept id.
fn rows(
    connection: &Connection,
    table: &str,
    names: &[String],
) -> Result<BTreeMap<String, Vec<Option<String>>>, PlanError> {
    let select: Vec<String> = names
        .iter()
        .map(|name| format!("CAST({} AS VARCHAR)", quote_ident(name)))
        .collect();
    let mut statement = connection.prepare(&format!(
        "SELECT CAST(\"__okf_concept_id\" AS VARCHAR), {} FROM {}",
        select.join(", "),
        quote_ident(table)
    ))?;
    let mut out = BTreeMap::new();
    let mut result = statement.query([])?;
    while let Some(row) = result.next()? {
        let id: Option<String> = row.get(0)?;
        let values = (0..names.len())
            .map(|index| row.get(index + 1))
            .collect::<Result<Vec<Option<String>>, _>>()?;
        if out.insert(id.unwrap_or_default(), values).is_some() {
            return Err(PlanError::Contract(format!(
                "the script duplicated rows of `{table}`; apply edits fields, not rows"
            )));
        }
    }
    Ok(out)
}

fn normalize_list_json(text: &str, table: &str, column: &str) -> Result<Value, PlanError> {
    let parsed: Value = serde_json::from_str(text).map_err(|_| {
        PlanError::Contract(format!(
            "`{table}`.`{column}` did not serialize as a JSON list"
        ))
    })?;
    let Value::Array(items) = parsed else {
        return Err(PlanError::Contract(format!(
            "`{table}`.`{column}` is not a flat list"
        )));
    };
    let mut normalized = Vec::with_capacity(items.len());
    for item in items {
        normalized.push(match item {
            Value::String(text) => Value::String(text),
            Value::Null => Value::Null,
            Value::Bool(value) => Value::String(value.to_string()),
            Value::Number(value) => Value::String(value.to_string()),
            _ => {
                return Err(PlanError::Contract(format!(
                    "`{table}`.`{column}` became a nested or structured list; apply only writes flat scalar lists"
                )));
            }
        });
    }
    Ok(Value::Array(normalized))
}

/// Every writable row, preserving list values as arrays rather than flattening them to text.
fn writable_rows(
    connection: &Connection,
    table: &str,
    names: &[String],
) -> Result<BTreeMap<String, Vec<Option<Value>>>, PlanError> {
    let types: HashMap<String, String> = connection
        .prepare(
            "SELECT column_name, data_type FROM duckdb_columns() \
             WHERE schema_name = 'main' AND table_name = ?",
        )?
        .query_map([table], |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<Result<_, _>>()?;
    let list_columns: Vec<bool> = names
        .iter()
        .map(|name| types.get(name).is_some_and(|kind| kind.ends_with("[]")))
        .collect();
    let select: Vec<String> = names
        .iter()
        .zip(&list_columns)
        .map(|(name, list)| {
            if *list {
                format!("CAST(to_json({}) AS VARCHAR)", quote_ident(name))
            } else {
                format!("CAST({} AS VARCHAR)", quote_ident(name))
            }
        })
        .collect();
    let mut statement = connection.prepare(&format!(
        "SELECT CAST(\"__okf_concept_id\" AS VARCHAR), {} FROM {}",
        select.join(", "),
        quote_ident(table)
    ))?;
    let mut out = BTreeMap::new();
    let mut result = statement.query([])?;
    while let Some(row) = result.next()? {
        let id: Option<String> = row.get(0)?;
        let mut values = Vec::with_capacity(names.len());
        for (index, (name, list)) in names.iter().zip(&list_columns).enumerate() {
            let raw: Option<String> = row.get(index + 1)?;
            values.push(match (raw, list) {
                (None, _) => None,
                (Some(value), false) => Some(Value::String(value)),
                (Some(value), true) => Some(normalize_list_json(&value, table, name)?),
            });
        }
        if out.insert(id.unwrap_or_default(), values).is_some() {
            return Err(PlanError::Contract(format!(
                "the script duplicated rows of `{table}`; apply edits fields, not rows"
            )));
        }
    }
    Ok(out)
}

/// Plan an apply: run `sql` over `concepts` and read the changes back.
pub fn plan_apply(
    root: &Path,
    concepts: &[PlannedConcept],
    sql: &str,
    spec_template: Option<&str>,
) -> Result<Vec<ConceptChanges>, PlanError> {
    let mut by_type: BTreeMap<&str, Vec<&PlannedConcept>> = BTreeMap::new();
    for concept in concepts {
        by_type
            .entry(concept.concept_type.as_str())
            .or_default()
            .push(concept);
    }
    let declarations = match spec_template {
        Some(template) => {
            let template = SpecTemplate::new(template).map_err(PlanError::SpecTemplate)?;
            discover_declared_schemas(root, by_type.keys().copied(), template)
                .map_err(PlanError::Declared)?
        }
        None => BTreeMap::new(),
    };
    let connection = Connection::open_in_memory()?;
    // Typed columns cast in UTC, whatever the session would say.
    let _ = connection.execute_batch("SET TimeZone = 'UTC'");
    let mut tables = Vec::with_capacity(by_type.len());
    for (name, members) in by_type {
        let mut scalar = BTreeSet::new();
        let mut lists = BTreeSet::new();
        let mut nulls = BTreeSet::new();
        let mut structured = BTreeSet::new();
        for concept in &members {
            for (key, value) in &concept.frontmatter {
                if is_reserved(key) {
                    return Err(PlanError::ReservedField {
                        concept_type: name.to_owned(),
                        field: key.clone(),
                    });
                }
                match value {
                    Value::String(_) => {
                        scalar.insert(key.clone());
                    }
                    Value::Null => {
                        nulls.insert(key.clone());
                    }
                    value if flat_list(value) => {
                        lists.insert(key.clone());
                    }
                    _ => {
                        structured.insert(key.clone());
                    }
                }
            }
        }
        for field in scalar.intersection(&lists) {
            structured.insert(field.clone());
        }
        let mut writable = scalar.clone();
        writable.extend(lists.iter().cloned());
        writable.extend(nulls);
        let list_fields: BTreeSet<String> = lists.difference(&structured).cloned().collect();
        let mut table = TypeTable {
            name,
            fields: writable.difference(&structured).cloned().collect(),
            lists: list_fields,
            concepts: members,
            protected: PROTECTED.iter().map(|(n, _)| (*n).to_owned()).collect(),
            declared: BTreeSet::new(),
            structured,
        };
        match declarations.get(name) {
            None => materialize_plain(&connection, &table)?,
            Some(declaration) => {
                let rows: Vec<ConceptRow<'_>> = table
                    .concepts
                    .iter()
                    .map(|concept| ConceptRow {
                        path: &concept.path,
                        concept_id: &concept.concept_id,
                        logical_key: &concept.concept_id,
                        body: &concept.body,
                        frontmatter: concept.frontmatter.clone(),
                    })
                    .collect();
                let plan =
                    compile_plan(name, &rows, declaration).map_err(|error| PlanError::Typed {
                        concept_type: name.to_owned(),
                        error,
                    })?;
                materialize_typed(&connection, &table, &plan, declaration)?;
                table.fields = plan
                    .fields
                    .iter()
                    .filter(|field| {
                        field.declared.as_ref().is_none_or(|declared| {
                            declared.logical_type.raw_sql_type() == "VARCHAR[]"
                        })
                    })
                    .map(|field| field.name.clone())
                    .collect();
                for field in &plan.fields {
                    if let Some(declared) = &field.declared {
                        if declared.logical_type.raw_sql_type() == "VARCHAR[]" {
                            table.lists.insert(field.name.clone());
                        } else {
                            table.declared.insert(field.name.clone());
                            table.protected.push(declared.raw_name.clone());
                        }
                    }
                }
                let declared_keys: HashSet<String> = plan
                    .fields
                    .iter()
                    .filter(|field| field.declared.is_some())
                    .map(|field| identifier_key(&field.name))
                    .collect();
                table
                    .structured
                    .retain(|name| !declared_keys.contains(&identifier_key(name)));
            }
        }
        tables.push(table);
    }
    let before: Vec<BTreeMap<String, Vec<Option<String>>>> = tables
        .iter()
        .map(|table| rows(&connection, table.name, &table.owned()))
        .collect::<Result<_, _>>()?;
    connection.execute_batch(
        "SET enable_external_access = false;
         SET autoinstall_known_extensions = false;
         SET autoload_known_extensions = false;
         SET lock_configuration = true;",
    )?;
    connection.execute_batch(sql).map_err(PlanError::Script)?;
    let mut changes = Vec::new();
    for (table, owned_before) in tables.iter().zip(before) {
        changes.extend(compile(&connection, table, &owned_before)?);
    }
    changes.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(changes)
}

/// Read one type's final table back as field changes.
fn compile(
    connection: &Connection,
    table: &TypeTable<'_>,
    owned_before: &BTreeMap<String, Vec<Option<String>>>,
) -> Result<Vec<ConceptChanges>, PlanError> {
    let name = table.name;
    let Some(final_columns) = columns(connection, name)? else {
        return Err(PlanError::Contract(format!(
            "the script dropped or renamed the table of type `{name}`"
        )));
    };
    let present: HashSet<&str> = final_columns.iter().map(String::as_str).collect();
    let owned = table.owned();
    if let Some(column) = owned
        .iter()
        .find(|column| !present.contains(column.as_str()))
    {
        return Err(table.refuse_owned(column, "removed"));
    }
    let structured: HashMap<String, &String> = table
        .structured
        .iter()
        .map(|field| (identifier_key(field), field))
        .collect();
    let owned_names: HashSet<&str> = owned.iter().map(String::as_str).collect();
    let mut writable = Vec::new();
    for column in &final_columns {
        if owned_names.contains(column.as_str()) {
            continue;
        }
        if is_reserved(column) {
            return Err(PlanError::Contract(format!(
                "`{name}`.`{column}` is in the reserved __okf_ namespace"
            )));
        }
        if let Some(field) = structured.get(&identifier_key(column)) {
            return Err(PlanError::Contract(format!(
                "`{name}`.`{column}` would overwrite `{field}`, a structured (list or \
                 mapping) field, which apply cannot write"
            )));
        }
        writable.push(column.clone());
    }
    let owned_after = rows(connection, name, &owned)?;
    if owned_after.len() != owned_before.len() || owned_after.keys().ne(owned_before.keys()) {
        return Err(PlanError::Contract(format!(
            "the script added or removed rows of `{name}`; apply edits fields, not rows"
        )));
    }
    for (id, values) in &owned_after {
        let original = &owned_before[id];
        if let Some(index) = (0..owned.len()).find(|&index| values[index] != original[index]) {
            return Err(table.refuse_owned(&owned[index], &format!("changed row `{id}` in")));
        }
    }
    let after = writable_rows(connection, name, &writable)?;
    let removed: Vec<&String> = table
        .fields
        .iter()
        .filter(|field| !present.contains(field.as_str()))
        .collect();
    let mut changes = Vec::new();
    for concept in &table.concepts {
        let mut fields: Vec<(String, Option<Value>)> = Vec::new();
        for field in &removed {
            if concept.frontmatter.contains_key(field.as_str()) {
                fields.push(((*field).clone(), None));
            }
        }
        let values = &after[&concept.concept_id];
        for (column, value) in writable.iter().zip(values) {
            let authored = concept.frontmatter.get(column);
            match (value, authored) {
                (Some(value), Some(old)) if value == old => {}
                (Some(value), _) => fields.push((column.clone(), Some(value.clone()))),
                (None, Some(Value::Null)) | (None, None) => {}
                (None, Some(_)) => fields.push((column.clone(), None)),
            }
        }
        if !fields.is_empty() {
            fields.sort();
            changes.push(ConceptChanges {
                path: concept.path.clone(),
                fields,
            });
        }
    }
    Ok(changes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn concept(path: &str, concept_type: &str, frontmatter: Value) -> PlannedConcept {
        let Value::Object(frontmatter) = frontmatter else {
            unreachable!()
        };
        PlannedConcept {
            path: path.to_owned(),
            concept_id: path.trim_end_matches(".md").to_owned(),
            concept_type: concept_type.to_owned(),
            frontmatter,
            frontmatter_text: String::new(),
            body: "text\n".to_owned(),
        }
    }

    fn notes() -> Vec<PlannedConcept> {
        vec![
            concept(
                "a.md",
                "Note",
                json!({"type": "Note", "status": "draft", "owner": "ana"}),
            ),
            concept(
                "b.md",
                "Note",
                json!({"type": "Note", "status": "final", "tags": ["x"], "meta": {"a": "b"}}),
            ),
            concept(
                "p.md",
                "Person",
                json!({"type": "Person", "name": "ana", "team": "core"}),
            ),
        ]
    }

    fn plan(sql: &str) -> Result<Vec<ConceptChanges>, PlanError> {
        plan_apply(Path::new("/nowhere"), &notes(), sql, None)
    }

    fn change(path: &str, fields: &[(&str, Option<&str>)]) -> ConceptChanges {
        ConceptChanges {
            path: path.to_owned(),
            fields: fields
                .iter()
                .map(|(name, value)| {
                    (
                        (*name).to_owned(),
                        value.map(|value| Value::String(value.to_owned())),
                    )
                })
                .collect(),
        }
    }

    fn list_change(path: &str, field: &str, items: &[&str]) -> ConceptChanges {
        ConceptChanges {
            path: path.to_owned(),
            fields: vec![(
                field.to_owned(),
                Some(Value::Array(
                    items
                        .iter()
                        .map(|item| Value::String((*item).to_owned()))
                        .collect(),
                )),
            )],
        }
    }

    #[test]
    fn an_update_sets_the_rows_it_changes() {
        assert_eq!(
            plan("UPDATE Note SET status = 'final' WHERE status = 'draft'").unwrap(),
            [change("a.md", &[("status", Some("final"))])]
        );
    }

    #[test]
    fn null_removes_and_a_dropped_column_removes_everywhere() {
        assert_eq!(
            plan("UPDATE Note SET owner = NULL").unwrap(),
            [change("a.md", &[("owner", None)])]
        );
        assert_eq!(
            plan("ALTER TABLE Note DROP COLUMN status").unwrap(),
            [
                change("a.md", &[("status", None)]),
                change("b.md", &[("status", None)])
            ]
        );
    }

    #[test]
    fn a_rename_moves_values_and_new_columns_add_fields() {
        assert_eq!(
            plan(
                "ALTER TABLE Note RENAME status TO state; \
                 ALTER TABLE Note ADD COLUMN reviewed INTEGER; \
                 UPDATE Note SET reviewed = 1 WHERE state = 'final'"
            )
            .unwrap(),
            [
                change("a.md", &[("state", Some("draft")), ("status", None)]),
                change(
                    "b.md",
                    &[
                        ("reviewed", Some("1")),
                        ("state", Some("final")),
                        ("status", None)
                    ]
                ),
            ]
        );
    }

    #[test]
    fn several_types_can_change_together() {
        assert_eq!(
            plan(
                "UPDATE Note SET owner = p.team FROM Person p WHERE Note.owner = p.name; \
                 UPDATE Person SET team = 'platform'"
            )
            .unwrap(),
            [
                change("a.md", &[("owner", Some("core"))]),
                change("p.md", &[("team", Some("platform"))]),
            ]
        );
    }

    #[test]
    fn rows_owned_columns_and_list_fields_are_off_limits() {
        for (sql, expected) in [
            (
                "DELETE FROM Note WHERE status = 'draft'",
                "added or removed rows",
            ),
            (
                "INSERT INTO Person (__okf_concept_id) VALUES ('x')",
                "added or removed rows",
            ),
            (
                "UPDATE Note SET __okf_body = 'x'",
                "protected column `Note`.`__okf_body`",
            ),
            (
                "ALTER TABLE Note ADD COLUMN meta VARCHAR",
                "structured (list or mapping)",
            ),
            (
                "ALTER TABLE Note ADD COLUMN __okf_mine VARCHAR",
                "reserved __okf_",
            ),
            ("DROP TABLE Person", "dropped or renamed the table"),
            ("SELECT * FROM read_csv('/etc/hosts')", "script failed"),
        ] {
            let message = plan(sql).unwrap_err().to_string();
            assert!(message.contains(expected), "{sql}: {message}");
        }
    }

    #[test]
    fn flat_lists_are_writable_and_list_functions_round_trip() {
        assert_eq!(
            plan("UPDATE Note SET tags = list_append(tags, 'y') WHERE tags IS NOT NULL").unwrap(),
            [list_change("b.md", "tags", &["x", "y"])]
        );
    }

    #[test]
    fn nothing_changed_is_an_empty_plan() {
        assert!(plan("UPDATE Note SET status = status").unwrap().is_empty());
        assert!(plan("CREATE TABLE scratch AS SELECT 1").unwrap().is_empty());
    }

    #[test]
    fn the_sugar_quotes_what_it_interpolates() {
        assert_eq!(
            sugar_sql("Nota Técnica", "st\"atus", "it's", "b"),
            "UPDATE \"Nota Técnica\" SET \"st\"\"atus\" = 'b' WHERE \"st\"\"atus\" = 'it''s'"
        );
    }
}