//! A type's optional `.schema.sql` declaration (RFC 0006).
//!
//! The file sits beside the type's specification document, at a path derived
//! from the spec template. It is trusted DuckDB SQL, run whole on its own
//! in-memory connection: CTAS, joins, `read_csv`, macros, anything DuckDB
//! accepts. Only the post-condition is checked, against that connection's
//! catalog: exactly one non-temporary table named for the concept type.
//! Never run a `.schema.sql` from a bundle you would not trust to execute
//! code; it has the power of a migration script.

use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

use duckdb::Connection;
use okf_engine::specs::SpecTemplate;
use serde::Serialize;

use crate::catalog::{LogicalType, identifier_key};

/// A declaration that could not be read, run, or does not declare its type.
#[derive(Debug)]
pub enum DeclaredSchemaError {
    /// The script itself failed.
    Script(duckdb::Error),
    /// The script ran but left no table named for the type.
    Missing { concept_type: String },
    /// The script left more than one table named for the type.
    Ambiguous { concept_type: String },
    /// Two types derive the same `.schema.sql` path.
    PathCollision { path: String, types: Vec<String> },
    /// The file exists but could not be read as UTF-8.
    Read { path: String, error: std::io::Error },
    /// DuckDB failed while its catalog was being read.
    Catalog(duckdb::Error),
}

impl fmt::Display for DeclaredSchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Script(error) => write!(f, "declared schema script failed: {error}"),
            Self::Missing { concept_type } => write!(
                f,
                "declared schema script did not create a table named `{concept_type}`"
            ),
            Self::Ambiguous { concept_type } => write!(
                f,
                "declared schema script created more than one table named `{concept_type}` \
                 (DuckDB identifiers ignore ASCII case)"
            ),
            Self::PathCollision { path, types } => write!(
                f,
                "types {} all derive the declared schema {path}; rename a type or change \
                 the spec template",
                types
                    .iter()
                    .map(|name| format!("`{name}`"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            Self::Read { path, error } => {
                write!(f, "could not read declared schema {path}: {error}")
            }
            Self::Catalog(error) => write!(f, "could not read the declared catalog: {error}"),
        }
    }
}

impl std::error::Error for DeclaredSchemaError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Script(error) | Self::Catalog(error) => Some(error),
            Self::Read { error, .. } => Some(error),
            Self::Missing { .. } | Self::Ambiguous { .. } | Self::PathCollision { .. } => None,
        }
    }
}

/// One declared column, in catalog order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DeclaredColumn {
    pub name: String,
    pub logical_type: LogicalType,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub comment: Option<String>,
}

/// One type's declared table, read back from DuckDB's catalog.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DeclaredSchema {
    pub table_name: String,
    pub columns: Vec<DeclaredColumn>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub table_comment: Option<String>,
}

/// The `.schema.sql` path beside `concept_type`'s specification document:
/// the spec path with its extension swapped.
pub fn declared_schema_relative_path(
    template: SpecTemplate<'_>,
    concept_type: &str,
) -> Option<String> {
    let relative = template.relative_path(concept_type)?;
    let file_start = relative.rfind('/').map_or(0, |slash| slash + 1);
    let stem = match relative[file_start..].rfind('.') {
        Some(dot) => &relative[..file_start + dot],
        None => relative.as_str(),
    };
    Some(format!("{stem}.schema.sql"))
}

/// Run `sql_text` whole on a dedicated connection and read back the one
/// table it declares for `concept_type`. Table identity follows DuckDB's
/// ASCII case-insensitive identifiers; the authored spelling is kept.
pub fn parse_declared_schema(
    sql_text: &str,
    concept_type: &str,
) -> Result<DeclaredSchema, DeclaredSchemaError> {
    let connection = Connection::open_in_memory().map_err(DeclaredSchemaError::Catalog)?;
    connection
        .execute_batch(sql_text)
        .map_err(DeclaredSchemaError::Script)?;
    let expected = identifier_key(concept_type);
    let mut tables = connection
        .prepare(
            "SELECT database_oid, schema_oid, table_oid, table_name, comment \
             FROM duckdb_tables() WHERE NOT temporary",
        )
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, String>(3)?,
                        row.get::<_, Option<String>>(4)?,
                    ))
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(DeclaredSchemaError::Catalog)?;
    tables.retain(|table| identifier_key(&table.3) == expected);
    let (database, schema, table, table_name, table_comment) = match tables.len() {
        0 => {
            return Err(DeclaredSchemaError::Missing {
                concept_type: concept_type.to_owned(),
            });
        }
        1 => tables.remove(0),
        _ => {
            return Err(DeclaredSchemaError::Ambiguous {
                concept_type: concept_type.to_owned(),
            });
        }
    };
    let columns = connection
        .prepare(
            "SELECT column_name, data_type, comment, numeric_precision, numeric_scale \
             FROM duckdb_columns() \
             WHERE database_oid = ? AND schema_oid = ? AND table_oid = ? \
             ORDER BY column_index",
        )
        .and_then(|mut statement| {
            statement
                .query_map([database, schema, table], |row| {
                    let precision: Option<i64> = row.get(3)?;
                    let scale: Option<i64> = row.get(4)?;
                    Ok(DeclaredColumn {
                        name: row.get(0)?,
                        logical_type: LogicalType::from_catalog(
                            &row.get::<_, String>(1)?,
                            precision.and_then(|value| u32::try_from(value).ok()),
                            scale.and_then(|value| u32::try_from(value).ok()),
                        ),
                        comment: row.get(2)?,
                    })
                })?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(DeclaredSchemaError::Catalog)?;
    Ok(DeclaredSchema {
        table_name,
        columns,
        table_comment,
    })
}

/// Run every declaration `template` derives for `concept_types` that exists
/// under `root`, keyed by concept type. Two types sharing one path is an
/// error, not a guess.
pub fn discover_declared_schemas<'a>(
    root: &Path,
    concept_types: impl IntoIterator<Item = &'a str>,
    template: SpecTemplate<'_>,
) -> Result<BTreeMap<String, DeclaredSchema>, DeclaredSchemaError> {
    let mut owners: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for concept_type in concept_types {
        if let Some(relative) = declared_schema_relative_path(template, concept_type) {
            let types = owners.entry(relative).or_default();
            if !types.iter().any(|known| known == concept_type) {
                types.push(concept_type.to_owned());
            }
        }
    }
    let mut declarations = BTreeMap::new();
    for (relative, mut types) in owners {
        let path = root.join(&relative);
        if !path.is_file() {
            continue;
        }
        if types.len() > 1 {
            types.sort();
            return Err(DeclaredSchemaError::PathCollision {
                path: relative,
                types,
            });
        }
        let concept_type = types.remove(0);
        let sql_text =
            std::fs::read_to_string(&path).map_err(|error| DeclaredSchemaError::Read {
                path: relative,
                error,
            })?;
        let declaration = parse_declared_schema(&sql_text, &concept_type)?;
        declarations.insert(concept_type, declaration);
    }
    Ok(declarations)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn template() -> SpecTemplate<'static> {
        SpecTemplate::new("specs/{slug}.md").unwrap()
    }

    #[test]
    fn the_declaration_sits_beside_the_spec() {
        assert_eq!(
            declared_schema_relative_path(template(), "Nota Técnica").as_deref(),
            Some("specs/nota-tecnica.schema.sql")
        );
        let bare = SpecTemplate::new("types.d/{slug}").unwrap();
        assert_eq!(
            declared_schema_relative_path(bare, "Note").as_deref(),
            Some("types.d/note.schema.sql")
        );
        assert_eq!(declared_schema_relative_path(template(), "知識"), None);
    }

    #[test]
    fn columns_comments_and_types_come_from_the_catalog() {
        let schema = parse_declared_schema(
            "CREATE TABLE staging (x INTEGER);\n\
             CREATE TABLE \"Note\" (status VARCHAR, due DATE, amount DECIMAL(9,2), tags VARCHAR[]);\n\
             COMMENT ON TABLE \"Note\" IS 'notes';\n\
             COMMENT ON COLUMN \"Note\".due IS 'when';",
            "note",
        )
        .unwrap();
        assert_eq!(schema.table_name, "Note");
        assert_eq!(schema.table_comment.as_deref(), Some("notes"));
        let names: Vec<&str> = schema.columns.iter().map(|c| c.name.as_str()).collect();
        assert_eq!(names, ["status", "due", "amount", "tags"]);
        assert_eq!(schema.columns[1].comment.as_deref(), Some("when"));
        assert_eq!(schema.columns[2].logical_type.precision, Some(9));
        assert_eq!(schema.columns[3].logical_type.raw_sql_type(), "VARCHAR[]");
    }

    #[test]
    fn ctas_and_temporary_tables_follow_the_catalog() {
        let schema = parse_declared_schema(
            "CREATE TEMP TABLE note (ignored INTEGER);\n\
             CREATE TABLE note AS SELECT 'a'::VARCHAR AS title, 1::BIGINT AS n;",
            "Note",
        )
        .unwrap();
        let names: Vec<&str> = schema.columns.iter().map(|c| c.name.as_str()).collect();
        assert_eq!(names, ["title", "n"]);
    }

    #[test]
    fn a_failing_or_empty_script_is_refused() {
        assert!(matches!(
            parse_declared_schema("CREATE TABLE (", "Note"),
            Err(DeclaredSchemaError::Script(_))
        ));
        let missing = parse_declared_schema("CREATE TABLE other (x INTEGER);", "Note");
        assert_eq!(
            missing.unwrap_err().to_string(),
            "declared schema script did not create a table named `Note`"
        );
    }
}
