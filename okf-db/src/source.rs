//! `okf-parser import`'s reading: any source DuckDB can scan, preserving shape.
//!
//! `FROM '<source>'` lets DuckDB's replacement scan pick the reader by
//! extension (CSV, Parquet, JSON, NDJSON). DuckDB serializes each typed value
//! through `to_json`, so lists and structs keep their shape while scalar
//! leaves can still be normalized to OKF strings by the importer.

use std::fmt;

use duckdb::Connection;
use serde::Serialize;
use serde_json::Value;

use crate::catalog::quote_literal;

/// A source DuckDB could not read.
#[derive(Debug)]
pub struct SourceError {
    source: String,
    error: SourceErrorKind,
}

#[derive(Debug)]
enum SourceErrorKind {
    DuckDb(duckdb::Error),
    Json(serde_json::Error),
}

impl fmt::Display for SourceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "could not read {:?}: {}", self.source, match &self.error {
            SourceErrorKind::DuckDb(error) => error.to_string(),
            SourceErrorKind::Json(error) => format!("DuckDB returned invalid JSON: {error}"),
        })
    }
}

impl std::error::Error for SourceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match &self.error {
            SourceErrorKind::DuckDb(error) => error,
            SourceErrorKind::Json(error) => error,
        })
    }
}

/// One source column and its DuckDB type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SourceColumn {
    pub name: String,
    #[serde(rename = "type")]
    pub sql_type: String,
}

/// Every row of a source, preserving lists/mappings and typed scalar leaves.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SourceRows {
    pub columns: Vec<SourceColumn>,
    pub rows: Vec<Vec<Option<Value>>>,
}

/// Read every row of `source` (a path or anything DuckDB's `FROM` accepts).
pub fn read_source(source: &str) -> Result<SourceRows, SourceError> {
    let failed = |error| SourceError {
        source: source.to_owned(),
        error: SourceErrorKind::DuckDb(error),
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
    let raw_rows: Vec<Vec<Option<String>>> = connection
        .prepare(&format!("SELECT to_json(COLUMNS(*)) {from}"))
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
    let rows = raw_rows
        .into_iter()
        .map(|row| {
            row.into_iter()
                .map(|value| {
                    value
                        .map(|json| {
                            serde_json::from_str(&json).map_err(|error| SourceError {
                                source: source.to_owned(),
                                error: SourceErrorKind::Json(error),
                            })
                        })
                        .transpose()
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .collect::<Result<Vec<_>, _>>()?;
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
                    Some(serde_json::json!("a")),
                    Some(serde_json::json!(1.5)),
                    Some(serde_json::json!(true)),
                    Some(serde_json::json!("2026-01-15"))
                ],
                vec![
                    Some(serde_json::json!("b")),
                    None,
                    Some(serde_json::json!(false)),
                    None
                ],
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
