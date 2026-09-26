//! `okf-parser import`'s reading: any source DuckDB can scan, as text rows.
//!
//! `FROM '<source>'` lets DuckDB's replacement scan pick the reader by
//! extension (CSV, Parquet, JSON, NDJSON). Every value is cast to `VARCHAR`
//! by DuckDB itself, which is exactly the text a frontmatter field holds.

use std::fmt;

use duckdb::Connection;
use serde::Serialize;

use crate::catalog::quote_literal;

/// A source DuckDB could not read.
#[derive(Debug)]
pub struct SourceError {
    source: String,
    error: duckdb::Error,
}

impl fmt::Display for SourceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "could not read {:?}: {}", self.source, self.error)
    }
}

impl std::error::Error for SourceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.error)
    }
}

/// One source column and its DuckDB type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SourceColumn {
    pub name: String,
    #[serde(rename = "type")]
    pub sql_type: String,
}

/// Every row of a source, each value as DuckDB's own text for it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SourceRows {
    pub columns: Vec<SourceColumn>,
    pub rows: Vec<Vec<Option<String>>>,
}

/// Read every row of `source` (a path or anything DuckDB's `FROM` accepts).
pub fn read_source(source: &str) -> Result<SourceRows, SourceError> {
    let failed = |error| SourceError {
        source: source.to_owned(),
        error,
    };
    let connection = Connection::open_in_memory().map_err(failed)?;
    let from = format!("FROM {}", quote_literal(source));
    let columns: Vec<SourceColumn> = connection
        .prepare(&format!("DESCRIBE {from}"))
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    Ok(SourceColumn {
                        name: row.get(0)?,
                        sql_type: row.get(1)?,
                    })
                })?
                .collect()
        })
        .map_err(failed)?;
    let rows = connection
        .prepare(&format!("SELECT CAST(COLUMNS(*) AS VARCHAR) {from}"))
        .and_then(|mut statement| {
            statement
                .query_map([], |row| {
                    (0..columns.len())
                        .map(|index| row.get(index))
                        .collect::<Result<Vec<Option<String>>, _>>()
                })?
                .collect()
        })
        .map_err(failed)?;
    Ok(SourceRows { columns, rows })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_csv_reads_as_typed_columns_and_text_values() {
        let path = std::env::temp_dir().join(format!("okf-source-{}.csv", std::process::id()));
        std::fs::write(
            &path,
            "id,amount,ok,day\na,1.50,true,2026-01-15\nb,,false,\n",
        )
        .unwrap();
        let read = read_source(&path.display().to_string()).unwrap();
        std::fs::remove_file(&path).unwrap();
        let types: Vec<&str> = read.columns.iter().map(|c| c.sql_type.as_str()).collect();
        assert_eq!(types, ["VARCHAR", "DOUBLE", "BOOLEAN", "DATE"]);
        assert_eq!(
            read.rows,
            [
                vec![
                    Some("a".into()),
                    Some("1.5".into()),
                    Some("true".into()),
                    Some("2026-01-15".into())
                ],
                vec![Some("b".into()), None, Some("false".into()), None],
            ]
        );
    }

    #[test]
    fn an_unreadable_source_names_itself() {
        let error = read_source("/definitely/missing.csv")
            .unwrap_err()
            .to_string();
        assert!(
            error.starts_with("could not read \"/definitely/missing.csv\""),
            "{error}"
        );
    }
}
