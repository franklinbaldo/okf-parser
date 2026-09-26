//! Types and identifiers as DuckDB's catalog spells them.
//!
//! DuckDB stays the parser and normalizer: a declared column's type is read
//! back from `duckdb_columns()` and only classified here, never re-derived.

use serde::Serialize;

/// The coarse family of a catalog type, enough to choose a raw carrier and
/// an export shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TypeFamily {
    String,
    Boolean,
    Integer,
    Float,
    Decimal,
    Date,
    Timestamp,
    Timestamptz,
    Uuid,
    List,
    Unsupported,
}

/// One catalog type, keeping its exact SQL spelling.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LogicalType {
    pub sql: String,
    pub family: TypeFamily,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub precision: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scale: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub element: Option<Box<LogicalType>>,
}

fn scalar_family(key: &str) -> TypeFamily {
    match key {
        "VARCHAR" => TypeFamily::String,
        "BOOLEAN" => TypeFamily::Boolean,
        "TINYINT" | "SMALLINT" | "INTEGER" | "BIGINT" | "HUGEINT" | "UTINYINT" | "USMALLINT"
        | "UINTEGER" | "UBIGINT" | "UHUGEINT" => TypeFamily::Integer,
        "FLOAT" | "REAL" | "DOUBLE" => TypeFamily::Float,
        "DATE" => TypeFamily::Date,
        "TIMESTAMP" | "TIMESTAMP_S" | "TIMESTAMP_MS" | "TIMESTAMP_NS" => TypeFamily::Timestamp,
        "TIMESTAMPTZ" | "TIMESTAMP WITH TIME ZONE" => TypeFamily::Timestamptz,
        "UUID" => TypeFamily::Uuid,
        _ => TypeFamily::Unsupported,
    }
}

/// `DECIMAL(p,s)` spelled out in a type name.
fn decimal_parameters(key: &str) -> (Option<u32>, Option<u32>) {
    let parsed = key
        .strip_prefix("DECIMAL(")
        .and_then(|rest| rest.strip_suffix(')'))
        .and_then(|inner| inner.split_once(','))
        .and_then(|(precision, scale)| {
            Some((precision.trim().parse().ok()?, scale.trim().parse().ok()?))
        });
    parsed.map_or((None, None), |(p, s)| (Some(p), Some(s)))
}

impl LogicalType {
    /// Classify a normalized catalog type; the catalog's own precision and
    /// scale win over the ones spelled in the name.
    pub fn from_catalog(data_type: &str, precision: Option<u32>, scale: Option<u32>) -> Self {
        let sql = data_type.split_whitespace().collect::<Vec<_>>().join(" ");
        let key = sql.to_uppercase();
        if let Some(element) = sql.strip_suffix("[]") {
            return Self {
                element: Some(Box::new(Self::from_catalog(element, None, None))),
                ..Self::bare(sql.clone(), TypeFamily::List)
            };
        }
        if key.starts_with("DECIMAL(") {
            let (parsed_precision, parsed_scale) = decimal_parameters(&key);
            return Self {
                precision: precision.or(parsed_precision),
                scale: scale.or(parsed_scale),
                ..Self::bare(sql, TypeFamily::Decimal)
            };
        }
        let family = scalar_family(&key);
        Self::bare(sql, family)
    }

    fn bare(sql: String, family: TypeFamily) -> Self {
        Self {
            sql,
            family,
            precision: None,
            scale: None,
            element: None,
        }
    }

    /// The raw column that carries authored text losslessly beside this type.
    pub fn raw_sql_type(&self) -> &'static str {
        if self.family == TypeFamily::List {
            "VARCHAR[]"
        } else {
            "VARCHAR"
        }
    }
}

/// A schema inside one attached database. Names are always qualified with
/// the database too: a file `okf.duckdb` is the catalog `okf`, and a bare
/// `okf.concepts` would be ambiguous against the schema `okf`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Schema {
    pub catalog: String,
    pub name: String,
}

impl Schema {
    /// `name` in the connection's current database.
    pub fn current(connection: &duckdb::Connection, name: &str) -> duckdb::Result<Self> {
        Ok(Self {
            catalog: connection.query_row("SELECT current_database()", [], |row| row.get(0))?,
            name: name.to_owned(),
        })
    }

    /// A sibling schema in the same database.
    pub fn sibling(&self, name: &str) -> Self {
        Self {
            catalog: self.catalog.clone(),
            name: name.to_owned(),
        }
    }

    /// The schema as SQL: `"catalog"."schema"`.
    pub fn sql(&self) -> String {
        format!("{}.{}", quote_ident(&self.catalog), quote_ident(&self.name))
    }

    /// One of its tables as SQL.
    pub fn table(&self, table: &str) -> String {
        format!("{}.{}", self.sql(), quote_ident(table))
    }

    /// The names of its tables, sorted.
    pub fn tables(&self, connection: &duckdb::Connection) -> duckdb::Result<Vec<String>> {
        let mut statement = connection.prepare(
            "SELECT table_name FROM information_schema.tables \
             WHERE table_catalog = ? AND table_schema = ? ORDER BY table_name",
        )?;
        statement
            .query_map([&self.catalog, &self.name], |row| row.get(0))?
            .collect()
    }
}

/// Fold ASCII letters the way DuckDB resolves quoted identifiers.
pub fn identifier_key(identifier: &str) -> String {
    identifier.to_ascii_lowercase()
}

/// `identifier` as a quoted SQL identifier.
pub fn quote_ident(identifier: &str) -> String {
    format!("\"{}\"", identifier.replace('"', "\"\""))
}

/// `value` as a SQL string literal.
pub fn quote_literal(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decimals_keep_their_parameters_and_unknown_types_their_spelling() {
        let decimal = LogicalType::from_catalog("DECIMAL(18,3)", None, None);
        assert_eq!(
            (decimal.family, decimal.precision, decimal.scale),
            (TypeFamily::Decimal, Some(18), Some(3))
        );
        let catalog = LogicalType::from_catalog("DECIMAL(18,3)", Some(9), Some(2));
        assert_eq!((catalog.precision, catalog.scale), (Some(9), Some(2)));
        let odd = LogicalType::from_catalog("STRUCT(a  INTEGER)", None, None);
        assert_eq!(
            (odd.sql.as_str(), odd.family),
            ("STRUCT(a INTEGER)", TypeFamily::Unsupported)
        );
    }

    #[test]
    fn lists_recurse_and_carry_a_list_raw_column() {
        let list = LogicalType::from_catalog("DATE[][]", None, None);
        assert_eq!(list.family, TypeFamily::List);
        let inner = list.element.as_deref().unwrap();
        assert_eq!(inner.family, TypeFamily::List);
        assert_eq!(inner.element.as_deref().unwrap().family, TypeFamily::Date);
        assert_eq!(list.raw_sql_type(), "VARCHAR[]");
        assert_eq!(
            LogicalType::from_catalog("timestamp with time zone", None, None).family,
            TypeFamily::Timestamptz
        );
    }

    #[test]
    fn quoting_doubles_the_delimiter() {
        assert_eq!(quote_ident("a\"b"), "\"a\"\"b\"");
        assert_eq!(quote_literal("it's"), "'it''s'");
        assert_eq!(identifier_key("Ação_X"), "ação_x");
    }
}
