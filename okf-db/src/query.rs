//! `okf-parser sql` and `Bundle.sql()`: one read-only query over a bundle.
//!
//! The bundle is materialized into a fresh in-memory database, the same
//! tables `okf-parser duckdb` writes (`okf.concepts`, `okf.links`,
//! `okf.reserved`, `okf.diagnostics`, and with a spec template each declared
//! type in `okf_types`), both on the search path. External access is then
//! switched off and locked: the query sees the bundle and nothing else, so it
//! cannot read or write files, reach the network or load extensions.

use std::collections::BTreeMap;
use std::fmt;
use std::path::Path;

use chrono::{DateTime, NaiveDate, NaiveTime};
use duckdb::Connection;
use duckdb::types::{TimeUnit, Value as DbValue};
use okf_engine::BundleData;
use okf_engine::specs::{SpecTemplate, SpecTemplateError};
use serde::Serialize;
use serde_json::{Map, Value};

use crate::catalog::Schema;
use crate::declared::{DeclaredSchema, DeclaredSchemaError, discover_declared_schemas};
use crate::export::fill_tables;
use crate::typed::{TypedTableError, materialize_typed_tables};

/// Why a query produced no rows.
#[derive(Debug)]
pub enum QueryError {
    SpecTemplate(SpecTemplateError),
    Declared(DeclaredSchemaError),
    Typed(TypedTableError),
    /// The query itself is not valid, or failed while running.
    Query(duckdb::Error),
    /// The statement is valid but is not one query returning rows.
    NotAQuery,
    /// DuckDB failed while materializing the bundle.
    Db(duckdb::Error),
}

impl fmt::Display for QueryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::SpecTemplate(error) => error.fmt(f),
            Self::Declared(error) => error.fmt(f),
            Self::Typed(error) => error.fmt(f),
            Self::Query(error) => write!(f, "query failed: {error}"),
            Self::NotAQuery => f.write_str(
                "sql runs one read-only query (SELECT, WITH, FROM ..., VALUES); \
                 this statement is not one",
            ),
            Self::Db(error) => write!(f, "DuckDB: {error}"),
        }
    }
}

impl std::error::Error for QueryError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::SpecTemplate(error) => Some(error),
            Self::Declared(error) => Some(error),
            Self::Typed(error) => Some(error),
            Self::Query(error) | Self::Db(error) => Some(error),
            Self::NotAQuery => None,
        }
    }
}

impl From<duckdb::Error> for QueryError {
    fn from(error: duckdb::Error) -> Self {
        Self::Db(error)
    }
}

/// One result column and its DuckDB type, as `DESCRIBE` spells it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Column {
    pub name: String,
    #[serde(rename = "type")]
    pub sql_type: String,
}

/// A query's answer. Values are JSON: numbers and strings as they are,
/// `HUGEINT` and `DECIMAL` as strings (JSON numbers would round them),
/// temporal values as ISO 8601 strings (`TIMESTAMP WITH TIME ZONE` with
/// `+00:00`), `BLOB` as hex, lists as arrays, structs as objects, maps as
/// `[key, value]` pairs. `columns` says how to read each one back.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct QueryResult {
    pub columns: Vec<Column>,
    pub rows: Vec<Vec<Value>>,
    /// Whether `limit` cut the rows short.
    pub truncated: bool,
}

/// What to materialize before running the query.
#[derive(Debug, Clone, Copy, Default)]
pub struct QueryOptions<'a> {
    /// Also materialize each declared type in `okf_types`.
    pub spec_template: Option<&'a str>,
    /// Return at most this many rows.
    pub limit: Option<usize>,
}

fn declarations(
    data: &BundleData,
    spec_template: Option<&str>,
) -> Result<BTreeMap<String, DeclaredSchema>, QueryError> {
    let Some(template) = spec_template else {
        return Ok(BTreeMap::new());
    };
    let template = SpecTemplate::new(template).map_err(QueryError::SpecTemplate)?;
    let mut types: Vec<&str> = data
        .concepts
        .iter()
        .map(|concept| concept.concept_type.as_str())
        .filter(|concept_type| !concept_type.is_empty())
        .collect();
    types.sort_unstable();
    types.dedup();
    discover_declared_schemas(Path::new(&data.root), types, template).map_err(QueryError::Declared)
}

/// The bundle as tables in a locked-down in-memory database.
fn materialize(data: &BundleData, spec_template: Option<&str>) -> Result<Connection, QueryError> {
    let declared = declarations(data, spec_template)?;
    let connection = Connection::open_in_memory()?;
    let base = Schema::current(&connection, "okf")?;
    let typed = base.sibling("okf_types");
    connection.execute_batch(&format!(
        "CREATE SCHEMA {}; CREATE SCHEMA {};",
        base.sql(),
        typed.sql()
    ))?;
    fill_tables(&connection, &base, data)?;
    if !declared.is_empty() {
        materialize_typed_tables(&connection, data, &typed, &declared, false)
            .map_err(QueryError::Typed)?;
    }
    connection.execute_batch(
        "SET search_path = 'okf,okf_types';
         SET enable_external_access = false;
         SET autoinstall_known_extensions = false;
         SET autoload_known_extensions = false;
         SET lock_configuration = true;",
    )?;
    Ok(connection)
}

fn statement(query: &str) -> &str {
    query.trim().trim_end_matches(';').trim_end()
}

/// The query's columns. `DESCRIBE` only accepts a query, which is what
/// keeps `sql` to one read-only statement.
fn describe(connection: &Connection, query: &str) -> Result<Vec<Column>, QueryError> {
    let mut statement = connection
        .prepare(&format!("DESCRIBE {query}"))
        .map_err(|_| QueryError::NotAQuery)?;
    let columns = statement
        .query_map([], |row| {
            Ok(Column {
                name: row.get(0)?,
                sql_type: row.get(1)?,
            })
        })
        .map_err(QueryError::Query)?
        .collect::<Result<_, _>>()
        .map_err(QueryError::Query)?;
    Ok(columns)
}

/// Run one read-only `query` over `data`.
pub fn query_bundle(
    data: &BundleData,
    query: &str,
    options: QueryOptions<'_>,
) -> Result<QueryResult, QueryError> {
    let connection = materialize(data, options.spec_template)?;
    let query = statement(query);
    // Prepared first, so a mistake is reported against the caller's own text.
    let mut prepared = connection.prepare(query).map_err(QueryError::Query)?;
    let columns = describe(&connection, query)?;
    let zoned: Vec<bool> = columns
        .iter()
        .map(|column| column.sql_type.contains("WITH TIME ZONE"))
        .collect();
    let mut rows = prepared.query([]).map_err(QueryError::Query)?;
    let mut out = Vec::new();
    let mut truncated = false;
    while let Some(row) = rows.next().map_err(QueryError::Query)? {
        if options.limit.is_some_and(|limit| out.len() >= limit) {
            truncated = true;
            break;
        }
        let mut cells = Vec::with_capacity(columns.len());
        for (index, zoned) in zoned.iter().enumerate() {
            let value: DbValue = row.get(index).map_err(QueryError::Query)?;
            cells.push(encode(value, *zoned));
        }
        out.push(cells);
    }
    Ok(QueryResult {
        columns,
        rows: out,
        truncated,
    })
}

fn micros(unit: TimeUnit, value: i64) -> i64 {
    match unit {
        TimeUnit::Second => value.saturating_mul(1_000_000),
        TimeUnit::Millisecond => value.saturating_mul(1_000),
        TimeUnit::Microsecond => value,
        TimeUnit::Nanosecond => value / 1_000,
    }
}

fn float(value: f64) -> Value {
    serde_json::Number::from_f64(value).map_or_else(
        || {
            Value::String(if value.is_nan() {
                "NaN".into()
            } else if value > 0.0 {
                "Infinity".into()
            } else {
                "-Infinity".into()
            })
        },
        Value::Number,
    )
}

fn timestamp(unit: TimeUnit, value: i64, zoned: bool) -> Value {
    let Some(moment) = DateTime::from_timestamp_micros(micros(unit, value)) else {
        // DuckDB's `infinity` and `-infinity` are the extreme values.
        return Value::String(if value > 0 { "infinity" } else { "-infinity" }.into());
    };
    let text = moment
        .naive_utc()
        .format("%Y-%m-%dT%H:%M:%S%.6f")
        .to_string();
    Value::String(if zoned { text + "+00:00" } else { text })
}

fn date(days: i32) -> Value {
    NaiveDate::from_ymd_opt(1970, 1, 1)
        .and_then(|epoch| epoch.checked_add_signed(chrono::Duration::days(days.into())))
        .map_or_else(
            || Value::String(if days > 0 { "infinity" } else { "-infinity" }.into()),
            |date| Value::String(date.format("%Y-%m-%d").to_string()),
        )
}

fn time(unit: TimeUnit, value: i64) -> Value {
    let total = micros(unit, value);
    let seconds = u32::try_from(total.div_euclid(1_000_000)).unwrap_or(0);
    let nanos = u32::try_from(total.rem_euclid(1_000_000) * 1_000).unwrap_or(0);
    NaiveTime::from_num_seconds_from_midnight_opt(seconds, nanos).map_or(Value::Null, |time| {
        Value::String(time.format("%H:%M:%S%.6f").to_string())
    })
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// One DuckDB value as JSON (see [`QueryResult`]).
fn encode(value: DbValue, zoned: bool) -> Value {
    match value {
        DbValue::Null => Value::Null,
        DbValue::Boolean(value) => Value::Bool(value),
        DbValue::TinyInt(value) => value.into(),
        DbValue::SmallInt(value) => value.into(),
        DbValue::Int(value) => value.into(),
        DbValue::BigInt(value) => value.into(),
        DbValue::UTinyInt(value) => value.into(),
        DbValue::USmallInt(value) => value.into(),
        DbValue::UInt(value) => value.into(),
        DbValue::UBigInt(value) => value.into(),
        DbValue::HugeInt(value) => Value::String(value.to_string()),
        DbValue::Float(value) => float(value.into()),
        DbValue::Double(value) => float(value),
        DbValue::Decimal(value) => Value::String(value.to_string()),
        DbValue::Timestamp(unit, value) => timestamp(unit, value, zoned),
        DbValue::Text(value) | DbValue::Enum(value) => Value::String(value),
        DbValue::Blob(value) => Value::String(hex(&value)),
        DbValue::Date32(days) => date(days),
        DbValue::Time64(unit, value) => time(unit, value),
        DbValue::Interval {
            months,
            days,
            nanos,
        } => serde_json::json!({"months": months, "days": days, "micros": nanos / 1_000}),
        DbValue::List(items) | DbValue::Array(items) => {
            Value::Array(items.into_iter().map(|item| encode(item, zoned)).collect())
        }
        DbValue::Struct(fields) => Value::Object(
            fields
                .iter()
                .map(|(name, item)| (name.clone(), encode(item.clone(), zoned)))
                .collect::<Map<_, _>>(),
        ),
        DbValue::Map(entries) => Value::Array(
            entries
                .iter()
                .map(|(key, item)| {
                    Value::Array(vec![
                        encode(key.clone(), zoned),
                        encode(item.clone(), zoned),
                    ])
                })
                .collect(),
        ),
        DbValue::Union(inner) => encode(*inner, zoned),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use okf_engine::ConceptRecord;

    fn bundle() -> BundleData {
        let concept = |path: &str, title: &str| ConceptRecord {
            concept_id: path.trim_end_matches(".md").to_owned(),
            logical_key: path.to_owned(),
            path: path.to_owned(),
            concept_type: "Note".to_owned(),
            title: Some(title.to_owned()),
            description: None,
            source_digest: String::new(),
            parsed_digest: String::new(),
            frontmatter_json: "{}".to_owned(),
            body: String::new(),
        };
        BundleData {
            root: std::env::temp_dir().display().to_string(),
            concepts: vec![concept("a.md", "A"), concept("b.md", "B")],
            reserved: Vec::new(),
            links: Vec::new(),
            diagnostics: Vec::new(),
            markdown_count: 2,
        }
    }

    fn run(sql: &str) -> Result<QueryResult, QueryError> {
        query_bundle(&bundle(), sql, QueryOptions::default())
    }

    #[test]
    fn the_bundle_tables_are_on_the_search_path() {
        let result = run("SELECT concept_id, title FROM concepts ORDER BY concept_id;").unwrap();
        assert_eq!(
            result.columns,
            [
                Column {
                    name: "concept_id".into(),
                    sql_type: "VARCHAR".into()
                },
                Column {
                    name: "title".into(),
                    sql_type: "VARCHAR".into()
                }
            ]
        );
        assert_eq!(result.rows, [["a", "A"], ["b", "B"]]);
        assert!(!result.truncated);
    }

    #[test]
    fn values_keep_their_precision_and_meaning() {
        let result = run(
            "SELECT 12.50::DECIMAL(9,2) AS d, 170141183460469231731687303715884105727::HUGEINT AS h, \
             DATE '2026-01-15' AS day, TIMESTAMP '2026-01-15 09:30:00.5' AS at, \
             TIMESTAMPTZ '2026-01-15 09:30:00+00' AS zoned, TIME '09:30:00' AS t, \
             'nan'::DOUBLE AS n, '\\x01\\xff'::BLOB AS b, [1, NULL] AS l, \
             {'k': 'v'} AS s, MAP {'a': 1} AS m",
        )
        .unwrap();
        assert_eq!(
            result.rows[0],
            [
                Value::from("12.50"),
                Value::from("170141183460469231731687303715884105727"),
                Value::from("2026-01-15"),
                Value::from("2026-01-15T09:30:00.500000"),
                Value::from("2026-01-15T09:30:00.000000+00:00"),
                Value::from("09:30:00.000000"),
                Value::from("NaN"),
                Value::from("01ff"),
                serde_json::json!([1, null]),
                serde_json::json!({"k": "v"}),
                serde_json::json!([["a", 1]]),
            ]
        );
        assert_eq!(result.columns[4].sql_type, "TIMESTAMP WITH TIME ZONE");
    }

    #[test]
    fn declared_defaults_fill_only_missing_values() {
        let root = std::env::temp_dir().join(format!("okf-defaults-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("specs")).unwrap();
        std::fs::write(
            root.join("specs/note.schema.sql"),
            "CREATE TABLE Note (status VARCHAR DEFAULT 'draft', n INTEGER DEFAULT 7);",
        )
        .unwrap();

        let mut data = bundle();
        data.root = root.display().to_string();
        data.concepts[0].frontmatter_json =
            r#"{"type":"Note","status":"final","n":"bad"}"#.to_owned();
        data.concepts[1].frontmatter_json = r#"{"type":"Note"}"#.to_owned();

        let result = query_bundle(
            &data,
            "SELECT __okf_concept_id, status, n FROM okf_types.Note ORDER BY 1",
            QueryOptions {
                spec_template: Some("specs/{slug}.md"),
                ..QueryOptions::default()
            },
        )
        .unwrap();
        assert_eq!(
            result.rows,
            [
                [Value::from("a"), Value::from("final"), Value::Null],
                [Value::from("b"), Value::from("draft"), Value::from(7)],
            ]
        );
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn a_limit_truncates_and_says_so() {
        let options = QueryOptions {
            limit: Some(1),
            ..QueryOptions::default()
        };
        let result = query_bundle(&bundle(), "FROM concepts", options).unwrap();
        assert_eq!(result.rows.len(), 1);
        assert!(result.truncated);
    }

    #[test]
    fn the_query_sees_the_bundle_and_nothing_else() {
        for sql in [
            "SELECT * FROM read_csv('/etc/hosts')",
            "COPY (SELECT 1) TO '/tmp/okf-escape.csv'",
            "SET enable_external_access = true",
            "INSTALL httpfs",
        ] {
            assert!(
                matches!(run(sql), Err(QueryError::Query(_) | QueryError::NotAQuery)),
                "{sql} should be refused"
            );
        }
        assert!(!Path::new("/tmp/okf-escape.csv").exists());
    }

    #[test]
    fn only_one_query_runs() {
        assert!(matches!(
            run("SELECT 1; SELECT 2"),
            Err(QueryError::Query(_))
        ));
        assert!(matches!(
            run("CREATE TABLE t (x INT)"),
            Err(QueryError::NotAQuery)
        ));
        let typo = run("SELEC 1").unwrap_err().to_string();
        assert!(
            typo.contains("SELEC 1") && !typo.contains("DESCRIBE"),
            "{typo}"
        );
    }
}
