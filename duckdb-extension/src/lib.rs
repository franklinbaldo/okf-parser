use std::{
    error::Error,
    path::Path,
    sync::atomic::{AtomicUsize, Ordering},
};

use duckdb::{
    Connection, Result,
    core::{DataChunkHandle, Inserter, LogicalTypeHandle, LogicalTypeId},
    duckdb_entrypoint_c_api,
    vtab::{BindInfo, InitInfo, TableFunctionInfo, VTab},
};
use okf_engine::ConceptRecord;

struct Concepts;

struct ConceptsBind {
    rows: Vec<ConceptRecord>,
}

struct ScanInit {
    cursor: AtomicUsize,
}

impl VTab for Concepts {
    type InitData = ScanInit;
    type BindData = ConceptsBind;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        for (name, ty) in [
            ("concept_id", LogicalTypeId::Varchar),
            ("logical_key", LogicalTypeId::Varchar),
            ("path", LogicalTypeId::Varchar),
            ("concept_type", LogicalTypeId::Varchar),
            ("title", LogicalTypeId::Varchar),
            ("description", LogicalTypeId::Varchar),
            ("source_digest", LogicalTypeId::Varchar),
            ("parsed_digest", LogicalTypeId::Varchar),
            ("frontmatter_json", LogicalTypeId::Varchar),
            ("body", LogicalTypeId::Varchar),
        ] {
            bind.add_result_column(name, ty.into());
        }

        let root = bind.get_parameter(0).to_string();
        let bundle = okf_engine::load_bundle(Path::new(&root), &[], 1)
            .map_err(|message| std::io::Error::other(format!("okf_concepts: {message}")))?;
        Ok(ConceptsBind {
            rows: bundle.concepts,
        })
    }

    fn init(_: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        Ok(ScanInit {
            cursor: AtomicUsize::new(0),
        })
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let rows = &func.get_bind_data().rows;
        let capacity = output.flat_vector(0).capacity();
        let start = func
            .get_init_data()
            .cursor
            .fetch_add(capacity, Ordering::Relaxed);
        if start >= rows.len() {
            output.set_len(0);
            return Ok(());
        }
        let count = capacity.min(rows.len() - start);

        for (out_row, row) in rows[start..start + count].iter().enumerate() {
            output
                .flat_vector(0)
                .insert(out_row, row.concept_id.as_str());
            output
                .flat_vector(1)
                .insert(out_row, row.logical_key.as_str());
            output.flat_vector(2).insert(out_row, row.path.as_str());
            output
                .flat_vector(3)
                .insert(out_row, row.concept_type.as_str());
            insert_optional(output, 4, out_row, row.title.as_deref());
            insert_optional(output, 5, out_row, row.description.as_deref());
            output
                .flat_vector(6)
                .insert(out_row, row.source_digest.as_str());
            output
                .flat_vector(7)
                .insert(out_row, row.parsed_digest.as_str());
            output
                .flat_vector(8)
                .insert(out_row, row.frontmatter_json.as_str());
            output.flat_vector(9).insert(out_row, row.body.as_str());
        }
        output.set_len(count);
        Ok(())
    }

    fn parameters() -> Option<Vec<LogicalTypeHandle>> {
        Some(vec![LogicalTypeId::Varchar.into()])
    }
}

fn insert_optional(output: &mut DataChunkHandle, column: usize, row: usize, value: Option<&str>) {
    let mut vector = output.flat_vector(column);
    match value {
        Some(value) => vector.insert(row, value),
        None => vector.set_null(row),
    }
}

#[duckdb_entrypoint_c_api(ext_name = "okf", min_duckdb_version = "v1.5.4")]
pub fn extension_entrypoint(con: Connection) -> Result<(), Box<dyn Error>> {
    con.register_table_function::<Concepts>("okf_concepts")?;
    Ok(())
}
