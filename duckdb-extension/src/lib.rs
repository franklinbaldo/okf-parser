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
use okf_engine::{BundleData, ConceptRecord, Diagnostic, LinkRecord, ReservedRecord};

struct ScanInit {
    cursor: AtomicUsize,
}

fn init_scan(_: &InitInfo) -> Result<ScanInit, Box<dyn Error>> {
    Ok(ScanInit {
        cursor: AtomicUsize::new(0),
    })
}

fn root(bind: &BindInfo) -> String {
    bind.get_parameter(0).to_string()
}

fn load(root: &str, function: &str) -> Result<BundleData, Box<dyn Error>> {
    okf_engine::load_bundle(Path::new(root), &[], 1)
        .map_err(|message| std::io::Error::other(format!("{function}: {message}")).into())
}

fn chunk(rows: usize, func: &ScanInit, output: &mut DataChunkHandle) -> Option<(usize, usize)> {
    let capacity = output.flat_vector(0).capacity();
    let start = func.cursor.fetch_add(capacity, Ordering::Relaxed);
    if start >= rows {
        output.set_len(0);
        None
    } else {
        Some((start, capacity.min(rows - start)))
    }
}

fn varchar(bind: &BindInfo, name: &str) {
    bind.add_result_column(name, LogicalTypeId::Varchar.into());
}

fn insert_optional(output: &mut DataChunkHandle, column: usize, row: usize, value: Option<&str>) {
    let mut vector = output.flat_vector(column);
    match value {
        Some(value) => vector.insert(row, value),
        None => vector.set_null(row),
    }
}

struct Concepts;
struct ConceptsBind {
    rows: Vec<ConceptRecord>,
}

impl VTab for Concepts {
    type BindData = ConceptsBind;
    type InitData = ScanInit;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        for name in [
            "concept_id",
            "logical_key",
            "path",
            "concept_type",
            "title",
            "description",
            "source_digest",
            "parsed_digest",
            "frontmatter_json",
            "body",
        ] {
            varchar(bind, name);
        }
        Ok(ConceptsBind {
            rows: load(&root(bind), "okf_concepts")?.concepts,
        })
    }

    fn init(info: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        init_scan(info)
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let rows = &func.get_bind_data().rows;
        let Some((start, count)) = chunk(rows.len(), func.get_init_data(), output) else {
            return Ok(());
        };
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

struct Links;
struct LinksBind {
    rows: Vec<LinkRecord>,
}

impl VTab for Links {
    type BindData = LinksBind;
    type InitData = ScanInit;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        varchar(bind, "source_id");
        varchar(bind, "raw_target");
        varchar(bind, "target_id");
        bind.add_result_column("exists", LogicalTypeId::Boolean.into());
        varchar(bind, "origin");
        Ok(LinksBind {
            rows: load(&root(bind), "okf_links")?.links,
        })
    }

    fn init(info: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        init_scan(info)
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let rows = &func.get_bind_data().rows;
        let Some((start, count)) = chunk(rows.len(), func.get_init_data(), output) else {
            return Ok(());
        };
        for (out_row, row) in rows[start..start + count].iter().enumerate() {
            output
                .flat_vector(0)
                .insert(out_row, row.source_id.as_str());
            output
                .flat_vector(1)
                .insert(out_row, row.raw_target.as_str());
            insert_optional(output, 2, out_row, row.target_id.as_deref());
            output.flat_vector(4).insert(out_row, row.origin.as_str());
        }
        {
            let mut vector = output.flat_vector(3);
            let values = unsafe { vector.as_mut_slice_with_len::<bool>(count) };
            for (out_row, row) in rows[start..start + count].iter().enumerate() {
                values[out_row] = row.exists;
            }
        }
        output.set_len(count);
        Ok(())
    }

    fn parameters() -> Option<Vec<LogicalTypeHandle>> {
        Some(vec![LogicalTypeId::Varchar.into()])
    }
}

struct Reserved;
struct ReservedBind {
    rows: Vec<ReservedRecord>,
}

impl VTab for Reserved {
    type BindData = ReservedBind;
    type InitData = ScanInit;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        for name in ["path", "filename", "body"] {
            varchar(bind, name);
        }
        Ok(ReservedBind {
            rows: load(&root(bind), "okf_reserved")?.reserved,
        })
    }

    fn init(info: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        init_scan(info)
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let rows = &func.get_bind_data().rows;
        let Some((start, count)) = chunk(rows.len(), func.get_init_data(), output) else {
            return Ok(());
        };
        for (out_row, row) in rows[start..start + count].iter().enumerate() {
            output.flat_vector(0).insert(out_row, row.path.as_str());
            output.flat_vector(1).insert(out_row, row.filename.as_str());
            output.flat_vector(2).insert(out_row, row.body.as_str());
        }
        output.set_len(count);
        Ok(())
    }

    fn parameters() -> Option<Vec<LogicalTypeHandle>> {
        Some(vec![LogicalTypeId::Varchar.into()])
    }
}

struct Diagnostics;
struct DiagnosticsBind {
    rows: Vec<Diagnostic>,
}

impl VTab for Diagnostics {
    type BindData = DiagnosticsBind;
    type InitData = ScanInit;

    fn bind(bind: &BindInfo) -> Result<Self::BindData, Box<dyn Error>> {
        for name in ["code", "severity", "path", "message"] {
            varchar(bind, name);
        }
        Ok(DiagnosticsBind {
            rows: load(&root(bind), "okf_diagnostics")?.diagnostics,
        })
    }

    fn init(info: &InitInfo) -> Result<Self::InitData, Box<dyn Error>> {
        init_scan(info)
    }

    fn func(
        func: &TableFunctionInfo<Self>,
        output: &mut DataChunkHandle,
    ) -> Result<(), Box<dyn Error>> {
        let rows = &func.get_bind_data().rows;
        let Some((start, count)) = chunk(rows.len(), func.get_init_data(), output) else {
            return Ok(());
        };
        for (out_row, row) in rows[start..start + count].iter().enumerate() {
            output.flat_vector(0).insert(out_row, row.code.as_str());
            output.flat_vector(1).insert(out_row, row.severity.as_str());
            output.flat_vector(2).insert(out_row, row.path.as_str());
            output.flat_vector(3).insert(out_row, row.message.as_str());
        }
        output.set_len(count);
        Ok(())
    }

    fn parameters() -> Option<Vec<LogicalTypeHandle>> {
        Some(vec![LogicalTypeId::Varchar.into()])
    }
}

#[duckdb_entrypoint_c_api(ext_name = "okf", min_duckdb_version = "v1.5.4")]
pub fn extension_entrypoint(con: Connection) -> Result<(), Box<dyn Error>> {
    con.register_table_function::<Concepts>("okf_concepts")?;
    con.register_table_function::<Links>("okf_links")?;
    con.register_table_function::<Reserved>("okf_reserved")?;
    con.register_table_function::<Diagnostics>("okf_diagnostics")?;
    Ok(())
}
