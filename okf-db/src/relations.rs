//! RFC 0021 bundle relation SQL over already-materialized typed tables.
//!
//! This is deliberately opt-in trusted SQL. It runs before the query
//! connection is locked down, in the same DuckDB connection that holds
//! `okf_types`, and may publish views/tables only under the reserved
//! `okf_relations` namespace for consumers.

use std::fmt;
use std::path::{Path, PathBuf};

use duckdb::Connection;
use serde::Serialize;

/// The optional bundle-root program.
pub const RELATIONS_FILENAME: &str = "okf.relations.sql";
/// The namespace relation programs publish into.
pub const RELATIONS_SCHEMA: &str = "okf_relations";

#[derive(Debug)]
pub enum BundleRelationsError {
    Read { path: PathBuf, error: std::io::Error },
    Script(duckdb::Error),
    Catalog(duckdb::Error),
    MissingSchema,
}

impl fmt::Display for BundleRelationsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read { path, error } => {
                write!(f, "could not read bundle relation SQL {}: {error}", path.display())
            }
            Self::Script(error) => write!(f, "bundle relation SQL failed: {error}"),
            Self::Catalog(error) => write!(f, "could not inspect bundle relations: {error}"),
            Self::MissingSchema => {
                write!(f, "bundle relation SQL removed reserved schema {RELATIONS_SCHEMA:?}")
            }
        }
    }
}

impl std::error::Error for BundleRelationsError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Read { error, .. } => Some(error),
            Self::Script(error) | Self::Catalog(error) => Some(error),
            Self::MissingSchema => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BundleRelationCatalog {
    pub schema: String,
    pub relations: Vec<String>,
}

/// Execute the optional relation program and inventory what it publishes.
///
/// The caller must materialize `okf_types` first. The schema is created even
/// when the file is absent, so relation-enabled queries have a stable search
/// path and simply see an empty catalog.
pub fn execute_bundle_relations(
    connection: &Connection,
    root: &Path,
) -> Result<BundleRelationCatalog, BundleRelationsError> {
    connection
        .execute_batch(&format!("CREATE SCHEMA IF NOT EXISTS {RELATIONS_SCHEMA}"))
        .map_err(BundleRelationsError::Catalog)?;

    let path = root.join(RELATIONS_FILENAME);
    if path.is_file() {
        let sql = std::fs::read_to_string(&path).map_err(|error| BundleRelationsError::Read {
            path: path.clone(),
            error,
        })?;
        connection
            .execute_batch(&sql)
            .map_err(BundleRelationsError::Script)?;
    }

    let schema_count: i64 = connection
        .query_row(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = ?",
            [RELATIONS_SCHEMA],
            |row| row.get(0),
        )
        .map_err(BundleRelationsError::Catalog)?;
    if schema_count != 1 {
        return Err(BundleRelationsError::MissingSchema);
    }

    let relations = connection
        .prepare(
            "SELECT table_name FROM information_schema.tables \
             WHERE table_schema = ? ORDER BY table_name",
        )
        .and_then(|mut statement| {
            statement
                .query_map([RELATIONS_SCHEMA], |row| row.get::<_, String>(0))?
                .collect::<Result<Vec<_>, _>>()
        })
        .map_err(BundleRelationsError::Catalog)?;

    Ok(BundleRelationCatalog {
        schema: RELATIONS_SCHEMA.to_owned(),
        relations,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "okf-relations-{name}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn relation_sql_sees_typed_tables_and_is_inventoried() {
        let root = root("join");
        let connection = Connection::open_in_memory().unwrap();
        connection
            .execute_batch(
                "CREATE SCHEMA okf_types; \
                 CREATE TABLE okf_types.Source(id VARCHAR, target_key VARCHAR); \
                 CREATE TABLE okf_types.Target(id VARCHAR, key VARCHAR); \
                 INSERT INTO okf_types.Source VALUES ('s', 'alpha'); \
                 INSERT INTO okf_types.Target VALUES ('t', 'alpha');",
            )
            .unwrap();
        std::fs::write(
            root.join(RELATIONS_FILENAME),
            "CREATE VIEW okf_relations.edges AS \
             SELECT s.id AS source_id, t.id AS target_id \
             FROM okf_types.Source s JOIN okf_types.Target t ON t.key = s.target_key;",
        )
        .unwrap();

        let catalog = execute_bundle_relations(&connection, &root).unwrap();
        assert_eq!(catalog.relations, ["edges"]);
        let pair: (String, String) = connection
            .query_row("SELECT source_id, target_id FROM okf_relations.edges", [], |row| {
                Ok((row.get(0)?, row.get(1)?))
            })
            .unwrap();
        assert_eq!(pair, ("s".into(), "t".into()));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn an_absent_program_is_a_valid_empty_catalog() {
        let root = root("absent");
        let connection = Connection::open_in_memory().unwrap();
        let catalog = execute_bundle_relations(&connection, &root).unwrap();
        assert!(catalog.relations.is_empty());
        std::fs::remove_dir_all(root).unwrap();
    }
}
