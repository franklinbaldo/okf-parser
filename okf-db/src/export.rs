//! `okf-parser duckdb`: a bundle as ordinary tables in a DuckDB database.
//!
//! `concepts`, `links`, `reserved` and `diagnostics` land in one schema and,
//! with a spec template, every declared type in `{schema}_types`. Everything
//! is written in one transaction: an export either lands whole or not at all.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use duckdb::Connection;
use okf_engine::check::{READ_CONCURRENCY, order};
use okf_engine::specs::{SpecTemplate, SpecTemplateError};
use okf_engine::{BundleData, LoadError, Severity, load_bundle};
use serde::Serialize;

use crate::catalog::{Schema, quote_ident};
use crate::declared::{DeclaredSchemaError, discover_declared_schemas};
use crate::typed::{TypedTableError, materialize_typed_tables};

/// The base tables, in creation order, with their columns.
const TABLES: [(&str, &[(&str, &str)]); 4] = [
    (
        "concepts",
        &[
            ("concept_id", "VARCHAR"),
            ("logical_key", "VARCHAR"),
            ("path", "VARCHAR"),
            ("concept_type", "VARCHAR"),
            ("title", "VARCHAR"),
            ("description", "VARCHAR"),
            ("source_digest", "VARCHAR"),
            ("parsed_digest", "VARCHAR"),
            ("frontmatter_json", "VARCHAR"),
            ("body", "VARCHAR"),
        ],
    ),
    (
        "links",
        &[
            ("source_id", "VARCHAR"),
            ("raw_target", "VARCHAR"),
            ("target_id", "VARCHAR"),
            ("exists", "BOOLEAN"),
            ("origin", "VARCHAR"),
        ],
    ),
    (
        "reserved",
        &[
            ("path", "VARCHAR"),
            ("filename", "VARCHAR"),
            ("body", "VARCHAR"),
        ],
    ),
    (
        "diagnostics",
        &[
            ("code", "VARCHAR"),
            ("severity", "VARCHAR"),
            ("path", "VARCHAR"),
            ("message", "VARCHAR"),
        ],
    ),
];

/// Why an export did not happen. Nothing was written in any case.
#[derive(Debug)]
pub enum ExportError {
    /// The schema name is not a plain SQL identifier.
    InvalidSchema(String),
    /// The target schema already holds tables and `overwrite` is off.
    Collision {
        schema: String,
        tables: Vec<String>,
    },
    Load(LoadError),
    SpecTemplate(SpecTemplateError),
    Declared(DeclaredSchemaError),
    Typed(TypedTableError),
    /// DuckDB could not open the database or write to it.
    Db(duckdb::Error),
}

impl fmt::Display for ExportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSchema(schema) => write!(
                f,
                "invalid DuckDB schema name `{schema}`: use letters, digits and `_`, not \
                 starting with a digit"
            ),
            Self::Collision { schema, tables } => write!(
                f,
                "schema `{schema}` already contains {}; pass --overwrite to replace them, or \
                 choose another --schema or database",
                tables.join(", ")
            ),
            Self::Load(error) => error.fmt(f),
            Self::SpecTemplate(error) => error.fmt(f),
            Self::Declared(error) => error.fmt(f),
            Self::Typed(error) => error.fmt(f),
            Self::Db(error) => write!(f, "DuckDB: {error}"),
        }
    }
}

impl std::error::Error for ExportError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::SpecTemplate(error) => Some(error),
            Self::Declared(error) => Some(error),
            Self::Typed(error) => Some(error),
            Self::Db(error) => Some(error),
            Self::InvalidSchema(_) | Self::Collision { .. } => None,
        }
    }
}

impl From<duckdb::Error> for ExportError {
    fn from(error: duckdb::Error) -> Self {
        Self::Db(error)
    }
}

impl From<TypedTableError> for ExportError {
    fn from(error: TypedTableError) -> Self {
        match error {
            TypedTableError::Collision { schema, tables } => Self::Collision { schema, tables },
            TypedTableError::Db(error) => Self::Db(error),
            other => Self::Typed(other),
        }
    }
}

/// What to export and where.
#[derive(Debug, Clone)]
pub struct ExportOptions<'a> {
    /// A DuckDB database: a file path or `:memory:`.
    pub database: &'a str,
    pub schema: &'a str,
    pub overwrite: bool,
    pub exclude: &'a [String],
    pub spec_template: Option<&'a str>,
}

/// The declared-type half of an export.
#[derive(Debug, Clone, Serialize)]
pub struct TypedSummary {
    pub typed_schema: String,
    pub typed_table_count: usize,
    pub typed_tables: Vec<String>,
    pub unrecognized_type_tables: Vec<String>,
}

/// What an export wrote.
#[derive(Debug, Clone, Serialize)]
pub struct ExportReport {
    pub database: String,
    pub schema: String,
    pub root: String,
    pub conformant: bool,
    pub markdown_count: usize,
    pub concept_count: usize,
    pub link_count: usize,
    pub diagnostic_count: usize,
    #[serde(flatten, skip_serializing_if = "Option::is_none")]
    pub typed: Option<TypedSummary>,
}

fn is_identifier(name: &str) -> bool {
    let mut characters = name.chars();
    characters
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
        && characters.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn existing_tables(connection: &Connection, schema: &Schema) -> Result<Vec<String>, duckdb::Error> {
    let present = schema.tables(connection)?;
    Ok(TABLES
        .iter()
        .map(|(name, _)| *name)
        .filter(|name| present.iter().any(|table| table == name))
        .map(str::to_owned)
        .collect())
}

fn replace_table(
    connection: &Connection,
    schema: &Schema,
    table: &str,
    columns: &[(&str, &str)],
) -> Result<(), duckdb::Error> {
    let qualified = schema.table(table);
    let definition: Vec<String> = columns
        .iter()
        .map(|(name, kind)| format!("{} {kind}", quote_ident(name)))
        .collect();
    connection.execute_batch(&format!(
        "DROP TABLE IF EXISTS {qualified}; CREATE TABLE {qualified} ({})",
        definition.join(", ")
    ))
}

fn fill_tables(
    connection: &Connection,
    schema: &Schema,
    data: &BundleData,
) -> Result<(), duckdb::Error> {
    for (table, columns) in TABLES {
        replace_table(connection, schema, table, columns)?;
    }
    let mut concepts =
        connection.appender_to_catalog_and_db("concepts", &schema.catalog, &schema.name)?;
    for row in &data.concepts {
        concepts.append_row(duckdb::params![
            row.concept_id,
            row.logical_key,
            row.path,
            row.concept_type,
            row.title,
            row.description,
            row.source_digest,
            row.parsed_digest,
            row.frontmatter_json,
            row.body,
        ])?;
    }
    concepts.flush()?;
    let mut links =
        connection.appender_to_catalog_and_db("links", &schema.catalog, &schema.name)?;
    for row in &data.links {
        links.append_row(duckdb::params![
            row.source_id,
            row.raw_target,
            row.target_id,
            row.exists,
            row.origin.as_str(),
        ])?;
    }
    links.flush()?;
    let mut reserved =
        connection.appender_to_catalog_and_db("reserved", &schema.catalog, &schema.name)?;
    for row in &data.reserved {
        reserved.append_row(duckdb::params![row.path, row.filename.as_str(), row.body])?;
    }
    reserved.flush()?;
    let mut diagnostics = data.diagnostics.clone();
    order(&mut diagnostics);
    let mut table =
        connection.appender_to_catalog_and_db("diagnostics", &schema.catalog, &schema.name)?;
    for row in &diagnostics {
        table.append_row(duckdb::params![
            row.code.as_str(),
            row.severity.as_str(),
            row.path,
            row.message,
        ])?;
    }
    table.flush()
}

fn resolved(database: &str) -> String {
    if database == ":memory:" || database.is_empty() {
        return database.to_owned();
    }
    let path = PathBuf::from(database);
    std::path::absolute(&path)
        .unwrap_or(path)
        .display()
        .to_string()
}

/// Export the bundle at `root` into `options.database`.
pub fn export_bundle(
    root: &Path,
    options: &ExportOptions<'_>,
) -> Result<ExportReport, ExportError> {
    if !is_identifier(options.schema) {
        return Err(ExportError::InvalidSchema(options.schema.to_owned()));
    }
    let template = options
        .spec_template
        .map(SpecTemplate::new)
        .transpose()
        .map_err(ExportError::SpecTemplate)?;
    let data = load_bundle(root, options.exclude, READ_CONCURRENCY).map_err(ExportError::Load)?;
    let declarations = match template {
        Some(template) => {
            let mut types: Vec<&str> = data
                .concepts
                .iter()
                .map(|concept| concept.concept_type.as_str())
                .filter(|concept_type| !concept_type.is_empty())
                .collect();
            types.sort_unstable();
            types.dedup();
            Some(
                discover_declared_schemas(Path::new(&data.root), types, template)
                    .map_err(ExportError::Declared)?,
            )
        }
        None => None,
    };
    let connection = Connection::open(options.database)?;
    let schema = Schema::current(&connection, options.schema)?;
    let collisions = existing_tables(&connection, &schema)?;
    if !collisions.is_empty() && !options.overwrite {
        return Err(ExportError::Collision {
            schema: options.schema.to_owned(),
            tables: collisions,
        });
    }
    connection.execute_batch("BEGIN TRANSACTION")?;
    let written = write(&connection, &data, &schema, options, declarations.as_ref());
    match written {
        Ok(typed) => {
            connection.execute_batch("COMMIT")?;
            Ok(ExportReport {
                database: resolved(options.database),
                schema: options.schema.to_owned(),
                conformant: !data
                    .diagnostics
                    .iter()
                    .any(|item| item.severity == Severity::Error),
                markdown_count: data.markdown_count,
                concept_count: data.concepts.len(),
                link_count: data.links.len(),
                diagnostic_count: data.diagnostics.len(),
                root: data.root,
                typed,
            })
        }
        Err(error) => {
            // The export's own error is the one worth reporting.
            let _ = connection.execute_batch("ROLLBACK");
            Err(error)
        }
    }
}

fn write(
    connection: &Connection,
    data: &BundleData,
    schema: &Schema,
    options: &ExportOptions<'_>,
    declarations: Option<&BTreeMap<String, crate::declared::DeclaredSchema>>,
) -> Result<Option<TypedSummary>, ExportError> {
    connection.execute_batch(&format!("CREATE SCHEMA IF NOT EXISTS {}", schema.sql()))?;
    fill_tables(connection, schema, data)?;
    let Some(declarations) = declarations else {
        return Ok(None);
    };
    let typed_schema = schema.sibling(&format!("{}_types", schema.name));
    let typed = materialize_typed_tables(
        connection,
        data,
        &typed_schema,
        declarations,
        options.overwrite,
    )?;
    Ok(Some(TypedSummary {
        typed_schema: typed.schema,
        typed_table_count: typed.tables.len(),
        typed_tables: typed.tables,
        unrecognized_type_tables: typed.unrecognized_tables,
    }))
}
