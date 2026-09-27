//! `okf-parser import`: every row of a DuckDB-readable source becomes one
//! concept document, `<type slug>/<id slug>.md`.
//!
//! A dry run reports what would be created and a `preview_token` bound to the
//! source rows and to the state of every destination; a write that passes
//! the token back fails closed if either changed. Existing destinations are
//! skipped, compared (`verify-identical`: the same parsed document is a
//! match, any other a conflict that blocks the whole import) or, with
//! `overwrite`, replaced.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use okf_engine::frontmatter::{RenderError, render_document};
use okf_engine::parsed_digest;
use okf_engine::specs::type_slug;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::source::{SourceError, SourceRows, read_source};

/// What to do with a destination that already exists.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ConflictPolicy {
    /// Leave it and report it in `skipped_existing`.
    #[default]
    Skip,
    /// Match it against the row: the same parsed document is fine, any
    /// other blocks the import.
    VerifyIdentical,
}

/// One import.
#[derive(Debug, Clone, Copy)]
pub struct ImportRequest<'a> {
    pub source: &'a str,
    pub root: &'a Path,
    pub concept_type: &'a str,
    pub id_column: Option<&'a str>,
    pub write: bool,
    pub overwrite: bool,
    pub on_conflict: ConflictPolicy,
    pub expected_preview_token: Option<&'a str>,
}

/// What an import found and did. Paths are bundle-relative.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct ImportReport {
    pub created: Vec<String>,
    pub would_create: Vec<String>,
    pub skipped_existing: Vec<String>,
    pub matched_existing: Vec<String>,
    pub conflicting_existing: Vec<String>,
    /// Id slugs more than one row derives: nothing is imported.
    pub duplicate_ids: Vec<String>,
    pub preview_token: String,
    pub written: bool,
}

impl ImportReport {
    /// Whether the import is blocked by duplicate ids or conflicts.
    pub fn blocked(&self) -> bool {
        !self.duplicate_ids.is_empty() || !self.conflicting_existing.is_empty()
    }
}

/// An import that could not be planned or written.
#[derive(Debug)]
pub enum ImportError {
    /// `overwrite` and `verify-identical` contradict each other.
    OverwriteAndVerify,
    Source(SourceError),
    /// The source has a `type` column, which would fight `--type`.
    ReservedTypeColumn,
    UnknownIdColumn {
        column: String,
        columns: Vec<String>,
    },
    MissingId {
        row: usize,
        column: String,
    },
    /// The destinations or the source changed since the preview.
    StalePreview,
    Render(RenderError),
    Write {
        path: PathBuf,
        error: io::Error,
    },
}

impl fmt::Display for ImportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OverwriteAndVerify => {
                f.write_str("overwrite and on_conflict='verify-identical' are mutually exclusive")
            }
            Self::Source(error) => error.fmt(f),
            Self::ReservedTypeColumn => {
                f.write_str("source column 'type' is reserved; use --type to set concept identity")
            }
            Self::UnknownIdColumn { column, columns } => write!(
                f,
                "id column {column:?} is not a column of the source: {}",
                columns.join(", ")
            ),
            Self::MissingId { row, column } => {
                write!(f, "row {row} has no value in id column {column:?}")
            }
            Self::StalePreview => {
                f.write_str("import preview is stale; rerun preview before committing")
            }
            Self::Render(error) => error.fmt(f),
            Self::Write { path, error } => write!(f, "could not write {}: {error}", path.display()),
        }
    }
}

impl std::error::Error for ImportError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Source(error) => Some(error),
            Self::Render(error) => Some(error),
            Self::Write { error, .. } => Some(error),
            _ => None,
        }
    }
}

impl ImportError {
    /// Whether the caller's request, not the environment, is at fault.
    pub fn is_request(&self) -> bool {
        !matches!(self, Self::Write { .. } | Self::Render(_))
    }
}

/// Plan, and with `write` perform, one import.
pub fn import_source(request: &ImportRequest<'_>) -> Result<ImportReport, ImportError> {
    if request.overwrite && request.on_conflict == ConflictPolicy::VerifyIdentical {
        return Err(ImportError::OverwriteAndVerify);
    }
    let source = read_source(request.source).map_err(ImportError::Source)?;
    let columns: Vec<String> = source.columns.iter().map(|c| c.name.clone()).collect();
    if columns.iter().any(|column| column == "type") {
        return Err(ImportError::ReservedTypeColumn);
    }
    let (plan, duplicate_ids) = plan(request, &columns, &source)?;
    let destinations: BTreeMap<&str, Value> = plan
        .iter()
        .map(|(relative, _)| {
            (
                relative.as_str(),
                destination_state(&request.root.join(relative)),
            )
        })
        .collect();
    let preview_token = preview_token(request, &columns, &source, &destinations);
    if request.write
        && request
            .expected_preview_token
            .is_some_and(|expected| expected != preview_token)
    {
        return Err(ImportError::StalePreview);
    }
    let mut report = ImportReport {
        preview_token,
        ..ImportReport::default()
    };
    if !duplicate_ids.is_empty() {
        report.duplicate_ids = duplicate_ids;
        return Ok(report);
    }

    let mut to_write: Vec<(String, String)> = Vec::new();
    for (relative, row) in &plan {
        let candidate = render(request.concept_type, &columns, row)?;
        let destination = request.root.join(relative);
        if destination.is_file() && !request.overwrite {
            let bucket = match request.on_conflict {
                ConflictPolicy::Skip => &mut report.skipped_existing,
                ConflictPolicy::VerifyIdentical if same_document(&destination, &candidate) => {
                    &mut report.matched_existing
                }
                ConflictPolicy::VerifyIdentical => &mut report.conflicting_existing,
            };
            bucket.push(relative.clone());
            continue;
        }
        to_write.push((relative.clone(), candidate));
    }
    report.skipped_existing.sort();
    report.matched_existing.sort();
    report.conflicting_existing.sort();
    let mut planned: Vec<String> = to_write
        .iter()
        .map(|(relative, _)| relative.clone())
        .collect();
    planned.sort();
    if !report.conflicting_existing.is_empty() || !request.write {
        report.would_create = planned;
        return Ok(report);
    }
    for (relative, text) in &to_write {
        write_staged(&request.root.join(relative), text)?;
    }
    report.created = planned;
    report.written = true;
    Ok(report)
}

type Row<'a> = &'a [Option<String>];

/// Each row's bundle-relative destination, in source order.
type Plan<'a> = Vec<(String, Row<'a>)>;

/// Each row's destination, in source order, and the id slugs that repeat.
fn plan<'a>(
    request: &ImportRequest<'_>,
    columns: &[String],
    source: &'a SourceRows,
) -> Result<(Plan<'a>, Vec<String>), ImportError> {
    let id_index = match request.id_column {
        None => None,
        Some(column) => Some(columns.iter().position(|c| c == column).ok_or_else(|| {
            ImportError::UnknownIdColumn {
                column: column.to_owned(),
                columns: columns.to_vec(),
            }
        })?),
    };
    let type_dir = match type_slug(request.concept_type) {
        slug if slug.is_empty() => "concept".to_owned(),
        slug => slug,
    };
    let mut seen: BTreeMap<String, usize> = BTreeMap::new();
    let mut plan = Vec::with_capacity(source.rows.len());
    for (index, row) in source.rows.iter().enumerate() {
        let concept_id = match id_index {
            None => format!("{index:06}"),
            Some(position) => match row[position].as_deref().map(str::trim) {
                Some(id) if !id.is_empty() => row[position].clone().unwrap_or_default(),
                _ => {
                    return Err(ImportError::MissingId {
                        row: index,
                        column: columns[position].clone(),
                    });
                }
            },
        };
        let id_slug = match type_slug(&concept_id) {
            slug if slug.is_empty() => format!("row-{index:06}"),
            slug => slug,
        };
        *seen.entry(id_slug.clone()).or_default() += 1;
        plan.push((format!("{type_dir}/{id_slug}.md"), row.as_slice()));
    }
    let duplicates = seen
        .into_iter()
        .filter(|(_, count)| *count > 1)
        .map(|(slug, _)| slug)
        .collect();
    Ok((plan, duplicates))
}

/// The row's document: `type`, then every non-null column as text.
fn render(concept_type: &str, columns: &[String], row: Row<'_>) -> Result<String, ImportError> {
    let mut frontmatter = Map::new();
    frontmatter.insert("type".to_owned(), Value::String(concept_type.to_owned()));
    for (column, value) in columns.iter().zip(row) {
        if let Some(value) = value {
            frontmatter.insert(column.clone(), Value::String(value.clone()));
        }
    }
    render_document(&frontmatter, "").map_err(ImportError::Render)
}

/// Compare parsed documents, not incidental YAML spelling.
fn same_document(destination: &Path, candidate: &str) -> bool {
    let Ok(existing) = fs::read_to_string(destination) else {
        return false;
    };
    match (parsed_digest(&existing), parsed_digest(candidate)) {
        (Some(existing), Some(intended)) => existing == intended,
        _ => false,
    }
}

/// What must stay unchanged between a preview and its write.
fn destination_state(destination: &Path) -> Value {
    if let Ok(target) = fs::read_link(destination) {
        return json!({"kind": "symlink", "target": target.to_string_lossy()});
    }
    if destination.is_file() {
        return match fs::read(destination) {
            Ok(bytes) => json!({"kind": "file", "sha256": format!("{:x}", Sha256::digest(bytes))}),
            Err(_) => json!({"kind": "unreadable"}),
        };
    }
    if destination.exists() {
        return json!({"kind": "other"});
    }
    json!({"kind": "absent"})
}

fn preview_token(
    request: &ImportRequest<'_>,
    columns: &[String],
    source: &SourceRows,
    destinations: &BTreeMap<&str, Value>,
) -> String {
    let payload = json!({
        "version": 2,
        "source": request.source,
        "concept_type": request.concept_type,
        "id_column": request.id_column,
        "overwrite": request.overwrite,
        "on_conflict": request.on_conflict,
        "columns": columns,
        "rows": source.rows,
        "destinations": destinations,
    });
    format!("{:x}", Sha256::digest(payload.to_string().as_bytes()))
}

/// Stage beside the destination and rename, so a crash never leaves a
/// truncated concept behind.
fn write_staged(destination: &Path, text: &str) -> Result<(), ImportError> {
    let failed = |error| ImportError::Write {
        path: destination.to_path_buf(),
        error,
    };
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent).map_err(failed)?;
    }
    let name = destination
        .file_name()
        .map_or_else(String::new, |name| name.to_string_lossy().into_owned());
    let staged = destination.with_file_name(format!(".{name}.okf-write.tmp"));
    fs::write(&staged, text).map_err(failed)?;
    fs::rename(&staged, destination).map_err(|error| {
        let _ = fs::remove_file(&staged);
        failed(error)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        dir: PathBuf,
    }

    impl Fixture {
        fn new(name: &str, csv: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("okf-import-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(dir.join("bundle")).unwrap();
            fs::write(dir.join("source.csv"), csv).unwrap();
            Self { dir }
        }

        fn source(&self) -> String {
            self.dir.join("source.csv").display().to_string()
        }

        fn root(&self) -> PathBuf {
            self.dir.join("bundle")
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.dir);
        }
    }

    fn request<'a>(source: &'a str, root: &'a Path) -> ImportRequest<'a> {
        ImportRequest {
            source,
            root,
            concept_type: "Customer Account",
            id_column: Some("id"),
            write: false,
            overwrite: false,
            on_conflict: ConflictPolicy::Skip,
            expected_preview_token: None,
        }
    }

    #[test]
    fn a_preview_names_the_documents_and_a_write_creates_them() {
        let fixture = Fixture::new("write", "id,name,score\nA-1,Ana,1.5\nb 2,Bia,\n");
        let (source, root) = (fixture.source(), fixture.root());
        let preview = import_source(&request(&source, &root)).unwrap();
        assert_eq!(
            preview.would_create,
            ["customer-account/a-1.md", "customer-account/b-2.md"]
        );
        let written = import_source(&ImportRequest {
            write: true,
            expected_preview_token: Some(&preview.preview_token),
            ..request(&source, &root)
        })
        .unwrap();
        assert_eq!(written.created, preview.would_create);
        let text = fs::read_to_string(root.join("customer-account/b-2.md")).unwrap();
        assert_eq!(
            text,
            "---\ntype: Customer Account\nid: b 2\nname: Bia\n---\n"
        );
    }

    #[test]
    fn a_stale_preview_and_duplicate_ids_block_the_write() {
        let fixture = Fixture::new("stale", "id\nx\n");
        let (source, root) = (fixture.source(), fixture.root());
        let preview = import_source(&request(&source, &root)).unwrap();
        fs::create_dir_all(root.join("customer-account")).unwrap();
        fs::write(
            root.join("customer-account/x.md"),
            "---\ntype: Other\n---\n",
        )
        .unwrap();
        let stale = import_source(&ImportRequest {
            write: true,
            expected_preview_token: Some(&preview.preview_token),
            ..request(&source, &root)
        });
        assert!(matches!(stale, Err(ImportError::StalePreview)));

        let fixture = Fixture::new("duplicates", "id\nA\na\n");
        let (source, root) = (fixture.source(), fixture.root());
        let report = import_source(&ImportRequest {
            write: true,
            ..request(&source, &root)
        })
        .unwrap();
        assert_eq!(report.duplicate_ids, ["a"]);
        assert!(report.blocked() && !report.written);
    }

    #[test]
    fn verify_identical_matches_by_parsed_document() {
        let fixture = Fixture::new("verify", "id,name\nx,Ana\ny,Bia\n");
        let (source, root) = (fixture.source(), fixture.root());
        fs::create_dir_all(root.join("customer-account")).unwrap();
        fs::write(
            root.join("customer-account/x.md"),
            "---\nname: 'Ana'\nid: x\ntype: Customer Account\n---\n",
        )
        .unwrap();
        fs::write(
            root.join("customer-account/y.md"),
            "---\ntype: Customer Account\n---\n",
        )
        .unwrap();
        let report = import_source(&ImportRequest {
            on_conflict: ConflictPolicy::VerifyIdentical,
            write: true,
            ..request(&source, &root)
        })
        .unwrap();
        assert_eq!(report.matched_existing, ["customer-account/x.md"]);
        assert_eq!(report.conflicting_existing, ["customer-account/y.md"]);
        assert!(report.blocked() && !report.written);
    }

    #[test]
    fn requests_that_cannot_be_planned_say_why() {
        let fixture = Fixture::new("errors", "type,id\nT,x\n");
        let (source, root) = (fixture.source(), fixture.root());
        let reserved = import_source(&request(&source, &root)).unwrap_err();
        assert!(matches!(reserved, ImportError::ReservedTypeColumn));
        let fixture = Fixture::new("errors2", "name\nx\n");
        let (source, root) = (fixture.source(), fixture.root());
        let unknown = import_source(&request(&source, &root)).unwrap_err();
        assert_eq!(
            unknown.to_string(),
            "id column \"id\" is not a column of the source: name"
        );
    }
}
