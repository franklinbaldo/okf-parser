use crate::engine::BundleData;
use duckdb::{Connection, params};
use std::path::Path;

fn ident(value: &str) -> Result<String, String> {
    if value.is_empty()
        || !value
            .chars()
            .enumerate()
            .all(|(i, c)| c == '_' || c.is_ascii_alphanumeric() && (i > 0 || !c.is_ascii_digit()))
    {
        return Err(format!("invalid DuckDB schema name: {value:?}"));
    }
    Ok(format!("\"{value}\""))
}
pub fn materialize(
    path: &Path,
    schema: &str,
    bundle: &BundleData,
    overwrite: bool,
) -> Result<(), String> {
    let schema_id = ident(schema)?;
    let mut connection = Connection::open(path).map_err(|e| e.to_string())?;
    connection
        .execute_batch(&format!("CREATE SCHEMA IF NOT EXISTS {schema_id}"))
        .map_err(|e| e.to_string())?;
    let existing:i64=connection.query_row("SELECT count(*) FROM information_schema.tables WHERE table_schema=? AND table_name IN ('concepts','reserved','links','diagnostics')",[schema],|row|row.get(0)).map_err(|e|e.to_string())?;
    if existing > 0 && !overwrite {
        return Err(format!(
            "schema {schema:?} already contains OKF tables; pass --overwrite"
        ));
    }
    let tx = connection.transaction().map_err(|e| e.to_string())?;
    tx.execute_batch(&format!("DROP TABLE IF EXISTS {schema_id}.concepts;DROP TABLE IF EXISTS {schema_id}.reserved;DROP TABLE IF EXISTS {schema_id}.links;DROP TABLE IF EXISTS {schema_id}.diagnostics;CREATE TABLE {schema_id}.concepts(concept_id VARCHAR,logical_key VARCHAR,path VARCHAR,concept_type VARCHAR,title VARCHAR,description VARCHAR,source_digest VARCHAR,parsed_digest VARCHAR,frontmatter_json VARCHAR,body VARCHAR);CREATE TABLE {schema_id}.reserved(path VARCHAR,filename VARCHAR,body VARCHAR);CREATE TABLE {schema_id}.links(source_id VARCHAR,raw_target VARCHAR,target_id VARCHAR,exists BOOLEAN,origin VARCHAR);CREATE TABLE {schema_id}.diagnostics(code VARCHAR,severity VARCHAR,path VARCHAR,message VARCHAR);")).map_err(|e|e.to_string())?;
    {
        let mut app = tx
            .appender_to_db("concepts", schema)
            .map_err(|e| e.to_string())?;
        for r in &bundle.concepts {
            app.append_row(params![
                &r.concept_id,
                &r.logical_key,
                &r.path,
                &r.concept_type,
                &r.title,
                &r.description,
                &r.source_digest,
                &r.parsed_digest,
                &r.frontmatter_json,
                &r.body
            ])
            .map_err(|e| e.to_string())?;
        }
        app.flush().map_err(|e| e.to_string())?;
    }
    {
        let mut app = tx
            .appender_to_db("reserved", schema)
            .map_err(|e| e.to_string())?;
        for r in &bundle.reserved {
            app.append_row(params![&r.path, &r.filename, &r.body])
                .map_err(|e| e.to_string())?;
        }
        app.flush().map_err(|e| e.to_string())?;
    }
    {
        let mut app = tx
            .appender_to_db("links", schema)
            .map_err(|e| e.to_string())?;
        for r in &bundle.links {
            app.append_row(params![
                &r.source_id,
                &r.raw_target,
                &r.target_id,
                r.exists,
                &r.origin
            ])
            .map_err(|e| e.to_string())?;
        }
        app.flush().map_err(|e| e.to_string())?;
    }
    {
        let mut app = tx
            .appender_to_db("diagnostics", schema)
            .map_err(|e| e.to_string())?;
        for r in &bundle.diagnostics {
            app.append_row(params![&r.code, &r.severity, &r.path, &r.message])
                .map_err(|e| e.to_string())?;
        }
        app.flush().map_err(|e| e.to_string())?;
    }
    tx.commit().map_err(|e| e.to_string())
}
