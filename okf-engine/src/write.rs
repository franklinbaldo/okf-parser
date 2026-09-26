//! Conflict-safe writes into a bundle, and the single-concept body edit.
//!
//! Every write follows one protocol: snapshot the visible filesystem, stage a
//! candidate copy of the bundle with the new documents, validate the
//! candidate, recheck that nothing changed since the snapshot, and only then
//! replace the touched files atomically. A write never leaves the bundle with
//! a normative error it did not already have, and never overwrites a file
//! someone else changed in the meantime: the recheck and the commit run
//! under an exclusive lock on the bundle's `.okf-write.lock`, so two
//! cooperating writers cannot both pass the recheck and overwrite each other.

use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsString;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::UNIX_EPOCH;
use std::{fmt, fs};

use ignore::gitignore::Gitignore;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

use crate::engine::{
    BundlePath, BundlePathError, Code, ExclusionError, LoadError, Severity, concept_identity,
    exclusions, ignored_directory, load_bundle, markdown, normalized_newlines, reserved,
    split_source,
};

const BOM: &[u8] = b"\xef\xbb\xbf";
const READ_CONCURRENCY: usize = 32;
/// The advisory lock file serializing commits; never part of the bundle.
const LOCK_FILE: &str = ".okf-write.lock";

/// A write that could not be attempted at all, as opposed to one that was
/// attempted and refused (validation or conflict), which is a result.
#[derive(Debug)]
pub enum WriteError {
    /// The bundle root named by the request cannot be resolved.
    RootUnreadable {
        path: PathBuf,
        source: io::Error,
    },
    /// The request's exclusion patterns are not valid gitignore rules.
    Exclusions(ExclusionError),
    /// No concept, or more than one, has the requested id.
    UnknownConcept(String),
    /// The staged candidate no longer has exactly one concept with the id.
    CandidateLost(String),
    /// A file changed while the snapshot was reading it; retrying may succeed.
    ChangedDuringRead {
        path: String,
    },
    /// A bundle file cannot be named as a bundle path (no UTF-8 name), so a
    /// write cannot account for it.
    Path(BundlePathError),
    /// Loading the live or the staged bundle failed.
    Load(LoadError),
    Walk(walkdir::Error),
    /// The filesystem failed underneath the write.
    Io(io::Error),
}

impl WriteError {
    /// Whether the request is at fault (retrying the same request cannot
    /// help) rather than the environment.
    pub fn is_request(&self) -> bool {
        matches!(
            self,
            Self::RootUnreadable { .. }
                | Self::Exclusions(_)
                | Self::UnknownConcept(_)
                | Self::CandidateLost(_)
                | Self::ChangedDuringRead { .. }
                | Self::Path(_)
        )
    }
}

impl fmt::Display for WriteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::RootUnreadable { path, source } => {
                write!(
                    f,
                    "bundle root is not readable: {}: {source}",
                    path.display()
                )
            }
            Self::Exclusions(error) => error.fmt(f),
            Self::UnknownConcept(id) => write!(f, "concept does not exist exactly once: {id}"),
            Self::CandidateLost(id) => write!(
                f,
                "candidate concept does not exist exactly once after staging: {id}"
            ),
            Self::ChangedDuringRead { path } => {
                write!(f, "file changed while it was being read: {path}")
            }
            Self::Path(error) => error.fmt(f),
            Self::Load(error) => error.fmt(f),
            Self::Walk(error) => error.fmt(f),
            Self::Io(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for WriteError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::RootUnreadable { source, .. } => Some(source),
            Self::Exclusions(error) => Some(error),
            Self::Load(error) => Some(error),
            Self::Walk(error) => Some(error),
            Self::Io(error) => Some(error),
            Self::Path(error) => Some(error),
            Self::UnknownConcept(_) | Self::CandidateLost(_) | Self::ChangedDuringRead { .. } => {
                None
            }
        }
    }
}

impl From<io::Error> for WriteError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<BundlePathError> for WriteError {
    fn from(error: BundlePathError) -> Self {
        Self::Path(error)
    }
}

/// A document's bytes split so it can be rebuilt with its physical style.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RawDocument {
    bom: bool,
    crlf: bool,
    frontmatter: String,
    body: String,
}

impl RawDocument {
    pub fn parse(data: &[u8]) -> Option<Self> {
        let text = std::str::from_utf8(data).ok()?;
        let normalized = normalized_newlines(text);
        let (frontmatter, body) = split_source(&normalized)?;
        Some(Self::from_split(text, frontmatter, body))
    }

    /// Keep the physical style of `text`, whose normalized source split into
    /// `frontmatter` and `body`.
    fn from_split(text: &str, frontmatter: &str, body: &str) -> Self {
        Self {
            bom: text.starts_with('\u{feff}'),
            crlf: text.contains("\r\n"),
            frontmatter: frontmatter.to_owned(),
            body: body.to_owned(),
        }
    }

    /// The exact bytes a write commits, keeping the BOM and line endings.
    pub fn render(&self) -> Vec<u8> {
        let mut text = format!("---\n{}", self.frontmatter);
        if !text.ends_with('\n') {
            text.push('\n');
        }
        text.push_str("---\n");
        text.push_str(&self.body);
        if self.crlf {
            text = text.replace('\n', "\r\n");
        }
        let mut bytes = if self.bom { BOM.to_vec() } else { Vec::new() };
        bytes.extend_from_slice(text.as_bytes());
        bytes
    }

    pub fn with_body(&self, body: String) -> Self {
        Self {
            body,
            ..self.clone()
        }
    }
}

type Signature = (u64, u128);

fn signature(path: &Path) -> io::Result<Signature> {
    let metadata = fs::metadata(path)?;
    let modified = metadata
        .modified()?
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    Ok((metadata.len(), modified))
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub struct ConceptSnapshot {
    pub path: PathBuf,
    pub relative: String,
    pub concept_id: String,
    pub source_digest: String,
    pub parsed_digest: String,
    pub content_hash: String,
    pub raw: RawDocument,
}

pub struct BundleSnapshot {
    pub manifest: BTreeMap<String, Signature>,
    pub concepts: Vec<ConceptSnapshot>,
}

/// Every non-symlink file a write must account for, relative to `root`.
///
/// Excluded directories are pruned unless the exclusion rules contain a
/// negation, which could re-include something below them.
fn visible_files(root: &Path, rules: &Gitignore) -> Result<Vec<(PathBuf, String)>, WriteError> {
    let prunes = rules.num_whitelists() == 0;
    let mut files = Vec::new();
    let walker = WalkDir::new(root).follow_links(false).into_iter();
    for entry in walker.filter_entry(|entry| {
        if entry.depth() == 0 {
            return true;
        }
        if entry.path_is_symlink() {
            return false;
        }
        if !entry.file_type().is_dir() {
            return true;
        }
        if ignored_directory(entry.file_name()) {
            return false;
        }
        let relative = entry.path().strip_prefix(root).unwrap_or(entry.path());
        !prunes
            || !rules
                .matched_path_or_any_parents(relative, true)
                .is_ignore()
    }) {
        let entry = entry.map_err(WriteError::Walk)?;
        if entry.depth() == 1 && entry.file_name() == LOCK_FILE {
            continue;
        }
        if entry.file_type().is_file() {
            let relative = BundlePath::new(root, entry.path())?.into_string();
            files.push((entry.into_path(), relative));
        }
    }
    files.sort();
    Ok(files)
}

fn exclusion_rules(root: &Path, exclude: &[String]) -> Result<Gitignore, WriteError> {
    exclusions(root, exclude).map_err(WriteError::Exclusions)
}

/// One parse of the bytes: split once, identify from the split, keep the split.
fn snapshot_concept(relative: &str, path: &Path, data: &[u8]) -> Option<ConceptSnapshot> {
    let text = std::str::from_utf8(data).ok()?;
    let normalized = normalized_newlines(text);
    let (frontmatter, body) = split_source(&normalized)?;
    let identity = concept_identity(relative, text, frontmatter, body)?;
    Some(ConceptSnapshot {
        path: path.to_owned(),
        relative: relative.to_owned(),
        concept_id: identity.concept_id,
        source_digest: identity.source_digest,
        parsed_digest: identity.parsed_digest,
        content_hash: sha256_hex(data),
        raw: RawDocument::from_split(text, frontmatter, body),
    })
}

/// Capture every visible file's signature and each concept's exact bytes.
pub fn snapshot_bundle(root: &Path, exclude: &[String]) -> Result<BundleSnapshot, WriteError> {
    let rules = exclusion_rules(root, exclude)?;
    let mut manifest = BTreeMap::new();
    let mut concepts = Vec::new();
    for (path, relative) in visible_files(root, &rules)? {
        let candidate = markdown(&path)
            && !rules
                .matched_path_or_any_parents(Path::new(&relative), false)
                .is_ignore()
            && !reserved(&path);
        if !candidate {
            manifest.insert(relative, signature(&path)?);
            continue;
        }
        let before = signature(&path)?;
        let data = fs::read(&path)?;
        let after = signature(&path)?;
        if before != after {
            return Err(WriteError::ChangedDuringRead { path: relative });
        }
        manifest.insert(relative.clone(), after);
        concepts.extend(snapshot_concept(&relative, &path, &data));
    }
    Ok(BundleSnapshot { manifest, concepts })
}

fn snapshot_manifest(
    root: &Path,
    exclude: &[String],
) -> Result<BTreeMap<String, Signature>, WriteError> {
    let rules = exclusion_rules(root, exclude)?;
    visible_files(root, &rules)?
        .into_iter()
        .map(|(path, relative)| Ok((relative, signature(&path)?)))
        .collect()
}

/// A replacement document for one bundle-relative path.
pub struct Candidate {
    pub raw: RawDocument,
    pub live_path: PathBuf,
    /// SHA-256 of the live file's bytes when the plan was made.
    pub expected_hash: String,
}

/// A name no other write (in this or another process) can be using.
fn unique_suffix() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |duration| duration.as_nanos()),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

/// Write `bytes` to a new sibling of `path` that only this write owns.
///
/// `create_new` fails instead of reusing a file, so two writers staging the
/// same document can never overwrite each other's staged bytes.
fn stage_sibling(path: &Path, bytes: &[u8]) -> io::Result<PathBuf> {
    let mut name = OsString::from(".");
    name.push(path.file_name().unwrap_or_default());
    name.push(format!(".okf-write.{}.tmp", unique_suffix()));
    let staged = path.with_file_name(name);
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&staged)?;
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        drop(file);
        let _ = fs::remove_file(&staged);
        return Err(error);
    }
    Ok(staged)
}

/// Create `path` with `bytes` unless it already exists; never replaces a file.
///
/// Returns whether this call created it. The content is staged in a sibling
/// first and then hard-linked into place, which fails instead of clobbering
/// if another writer created `path` in the meantime, so the file appears
/// complete or not at all. A filesystem without hard links falls back to an
/// exclusive `create_new`.
pub(crate) fn create_exclusive(path: &Path, bytes: &[u8]) -> io::Result<bool> {
    let staged = stage_sibling(path, bytes)?;
    let linked = fs::hard_link(&staged, path);
    let _ = fs::remove_file(&staged);
    match linked {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => Ok(false),
        Err(_) => create_new(path, bytes),
    }
}

pub(crate) fn create_new(path: &Path, bytes: &[u8]) -> io::Result<bool> {
    let mut file = match fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => return Ok(false),
        Err(error) => return Err(error),
    };
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        drop(file);
        let _ = fs::remove_file(path);
        return Err(error);
    }
    Ok(true)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let staged = stage_sibling(path, bytes)?;
    fs::rename(&staged, path).inspect_err(|_| {
        let _ = fs::remove_file(&staged);
    })
}

/// Replace every file in `replacements`, or leave all of them as they were.
///
/// Everything is staged before anything is replaced, so a failure while
/// staging commits nothing. If a replacement fails part-way, the files
/// already replaced are restored from their original bytes.
fn commit_all(
    replacements: &[(PathBuf, Vec<u8>)],
    rename: impl Fn(&Path, &Path) -> io::Result<()>,
) -> io::Result<()> {
    let originals = replacements
        .iter()
        .map(|(path, _)| fs::read(path))
        .collect::<io::Result<Vec<_>>>()?;
    let mut staged = Vec::with_capacity(replacements.len());
    for (path, bytes) in replacements {
        match stage_sibling(path, bytes) {
            Ok(file) => staged.push(file),
            Err(error) => {
                for file in &staged {
                    let _ = fs::remove_file(file);
                }
                return Err(error);
            }
        }
    }
    for (index, ((path, _), file)) in replacements.iter().zip(&staged).enumerate() {
        if let Err(error) = rename(file, path) {
            for file in &staged[index..] {
                let _ = fs::remove_file(file);
            }
            let unrestored: Vec<String> = replacements[..index]
                .iter()
                .zip(&originals)
                .filter(|((path, _), original)| atomic_write(path, original).is_err())
                .map(|((path, _), _)| path.display().to_string())
                .collect();
            let detail = if unrestored.is_empty() {
                format!("rolled back {index} replaced file(s)")
            } else {
                format!("could not restore: {}", unrestored.join(", "))
            };
            return Err(io::Error::new(error.kind(), format!("{error}; {detail}")));
        }
    }
    Ok(())
}

/// A private directory removed when dropped, for staging candidate bundles.
struct StagingDir(PathBuf);

impl StagingDir {
    fn new(prefix: &str) -> io::Result<Self> {
        let path = std::env::temp_dir().join(format!("{prefix}{}", unique_suffix()));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
}

impl Drop for StagingDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Mirror the visible bundle into `staging`, substituting candidate documents.
fn build_candidate_tree(
    root: &Path,
    staging: &Path,
    candidates: &BTreeMap<String, Candidate>,
    exclude: &[String],
) -> Result<(), WriteError> {
    let rules = exclusion_rules(root, exclude)?;
    for (source, relative) in visible_files(root, &rules)? {
        let destination = staging.join(&relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        match candidates.get(&relative) {
            Some(candidate) => atomic_write(&destination, &candidate.raw.render())?,
            None => {
                if fs::hard_link(&source, &destination).is_err() {
                    fs::copy(&source, &destination)?;
                }
            }
        }
    }
    Ok(())
}

pub type DiagnosticKey = (Code, String, String);

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ValidationItem {
    pub code: Code,
    pub path: String,
    pub message: String,
}

/// The normative (error) diagnostics of a bundle, as comparable keys.
pub fn error_keys(root: &Path, exclude: &[String]) -> Result<BTreeSet<DiagnosticKey>, WriteError> {
    let data = load_bundle(root, exclude, READ_CONCURRENCY).map_err(WriteError::Load)?;
    Ok(data
        .diagnostics
        .into_iter()
        .filter(|item| item.severity == Severity::Error)
        .map(|item| (item.code, item.path, item.message))
        .collect())
}

fn new_errors(
    candidate: &BTreeSet<DiagnosticKey>,
    baseline: &BTreeSet<DiagnosticKey>,
) -> Vec<ValidationItem> {
    candidate
        .difference(baseline)
        .map(|(code, path, message)| ValidationItem {
            code: *code,
            path: path.clone(),
            message: message.clone(),
        })
        .collect()
}

/// How a write the engine could attempt ended; failures to attempt it are
/// `Err(WriteError)`.
#[derive(Debug, PartialEq, Eq)]
pub enum WriteOutcome {
    Written,
    /// Refused: the candidate adds these normative diagnostics.
    Invalid(Vec<ValidationItem>),
    /// Refused: these paths changed since the snapshot.
    Conflict(Vec<String>),
}

/// Take the bundle's exclusive write lock; it is released when the file drops.
///
/// The lock file is created once and never removed, so every writer locks the
/// same inode.
fn lock_bundle(root: &Path) -> io::Result<fs::File> {
    let lock = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(root.join(LOCK_FILE))?;
    lock.lock()?;
    Ok(lock)
}

/// Stage the candidates, validate, recheck freshness, then replace the files.
pub fn stage_validate_write(
    root: &Path,
    exclude: &[String],
    candidates: &BTreeMap<String, Candidate>,
    baseline_errors: &BTreeSet<DiagnosticKey>,
    baseline_manifest: &BTreeMap<String, Signature>,
) -> Result<WriteOutcome, WriteError> {
    let staging = StagingDir::new("okf-write-")?;
    let candidate_root = staging.0.join("bundle");
    fs::create_dir(&candidate_root)?;
    build_candidate_tree(root, &candidate_root, candidates, exclude)?;
    let invalid = new_errors(&error_keys(&candidate_root, exclude)?, baseline_errors);
    if !invalid.is_empty() {
        return Ok(WriteOutcome::Invalid(invalid));
    }

    let _lock = lock_bundle(root)?;
    let current = snapshot_manifest(root, exclude)?;
    let baseline_paths: BTreeSet<&String> = baseline_manifest.keys().collect();
    let current_paths: BTreeSet<&String> = current.keys().collect();
    let mut conflicts: BTreeSet<String> = baseline_paths
        .symmetric_difference(&current_paths)
        .map(|path| (*path).clone())
        .collect();
    for path in baseline_paths.intersection(&current_paths) {
        if !candidates.contains_key(*path) && baseline_manifest[*path] != current[*path] {
            conflicts.insert((*path).clone());
        }
    }
    for (relative, candidate) in candidates {
        let live = fs::read(&candidate.live_path).map(|bytes| sha256_hex(&bytes));
        if live.ok().as_deref() != Some(candidate.expected_hash.as_str()) {
            conflicts.insert(relative.clone());
        }
    }
    if !conflicts.is_empty() {
        return Ok(WriteOutcome::Conflict(conflicts.into_iter().collect()));
    }

    let replacements: Vec<(PathBuf, Vec<u8>)> = candidates
        .values()
        .map(|candidate| (candidate.live_path.clone(), candidate.raw.render()))
        .collect();
    commit_all(&replacements, |from, to| fs::rename(from, to))?;
    Ok(WriteOutcome::Written)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditRequest {
    pub path: PathBuf,
    pub concept_id: String,
    pub body: String,
    pub expected_source_digest: String,
    #[serde(default)]
    pub exclude: Vec<String>,
    #[serde(default)]
    pub write: bool,
}

/// A concept's source and parsed digests.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Digests {
    pub source: String,
    pub parsed: String,
}

/// How an edit ended. Only `Previewed` and `Written` mean the new body is (or
/// would be) the concept's; `Unchanged` means there was nothing to write.
#[derive(Debug, PartialEq, Eq)]
pub enum EditOutcome {
    /// The body is already the requested one.
    Unchanged,
    /// The concept changed since the caller read `expected_source_digest`.
    Stale,
    /// Staged and validated, not committed (a preview).
    Previewed,
    Written,
    /// Refused: the candidate adds these normative diagnostics.
    Invalid(Vec<ValidationItem>),
    /// Refused: these paths changed while the edit was being validated.
    Conflict(Vec<String>),
}

/// The edited concept, its candidate's digests, and how the edit ended.
#[derive(Debug, PartialEq, Eq)]
pub struct EditReport {
    pub concept_id: String,
    pub path: String,
    pub source_digest: String,
    /// The digests the concept has after this edit (or would have, if refused).
    /// For `Unchanged` and `Stale` they are the live concept's.
    pub candidate: Digests,
    pub outcome: EditOutcome,
}

/// Replace one concept's Markdown body, previewing or committing it.
pub fn edit_concept(request: &EditRequest) -> Result<EditReport, WriteError> {
    let root = request
        .path
        .canonicalize()
        .map_err(|source| WriteError::RootUnreadable {
            path: request.path.clone(),
            source,
        })?;
    let snapshot = snapshot_bundle(&root, &request.exclude)?;
    let mut matches = snapshot
        .concepts
        .iter()
        .filter(|concept| concept.concept_id == request.concept_id);
    let (Some(concept), None) = (matches.next(), matches.next()) else {
        return Err(WriteError::UnknownConcept(request.concept_id.clone()));
    };

    let report = |candidate: Digests, outcome| EditReport {
        concept_id: concept.concept_id.clone(),
        path: concept.relative.clone(),
        source_digest: concept.source_digest.clone(),
        candidate,
        outcome,
    };
    let live = Digests {
        source: concept.source_digest.clone(),
        parsed: concept.parsed_digest.clone(),
    };
    if concept.source_digest != request.expected_source_digest {
        return Ok(report(live, EditOutcome::Stale));
    }
    let body = normalized_newlines(&request.body).into_owned();
    if body == concept.raw.body {
        return Ok(report(live, EditOutcome::Unchanged));
    }

    let baseline_errors = error_keys(&root, &request.exclude)?;
    let candidates = BTreeMap::from([(
        concept.relative.clone(),
        Candidate {
            raw: concept.raw.with_body(body),
            live_path: concept.path.clone(),
            expected_hash: concept.content_hash.clone(),
        },
    )]);

    // Preview on the same staged tree a commit would validate; loading it
    // yields the exact digests a successful commit produces.
    let staging = StagingDir::new("okf-edit-")?;
    let candidate_root = staging.0.join("bundle");
    fs::create_dir(&candidate_root)?;
    build_candidate_tree(&root, &candidate_root, &candidates, &request.exclude)?;
    let staged = load_bundle(&candidate_root, &request.exclude, READ_CONCURRENCY)
        .map_err(WriteError::Load)?;
    drop(staging);
    let mut staged_matches = staged
        .concepts
        .iter()
        .filter(|record| record.concept_id == request.concept_id);
    let (Some(record), None) = (staged_matches.next(), staged_matches.next()) else {
        return Err(WriteError::CandidateLost(request.concept_id.clone()));
    };
    let candidate = Digests {
        source: record.source_digest.clone(),
        parsed: record.parsed_digest.clone(),
    };
    let candidate_errors: BTreeSet<DiagnosticKey> = staged
        .diagnostics
        .iter()
        .filter(|item| item.severity == Severity::Error)
        .map(|item| (item.code, item.path.clone(), item.message.clone()))
        .collect();

    let invalid = new_errors(&candidate_errors, &baseline_errors);
    if !invalid.is_empty() {
        return Ok(report(candidate, EditOutcome::Invalid(invalid)));
    }
    if !request.write {
        return Ok(report(candidate, EditOutcome::Previewed));
    }
    let outcome = match stage_validate_write(
        &root,
        &request.exclude,
        &candidates,
        &baseline_errors,
        &snapshot.manifest,
    )? {
        WriteOutcome::Written => EditOutcome::Written,
        WriteOutcome::Invalid(items) => EditOutcome::Invalid(items),
        WriteOutcome::Conflict(paths) => EditOutcome::Conflict(paths),
    };
    Ok(report(candidate, outcome))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle(files: &[(&str, &str)]) -> StagingDir {
        let dir = StagingDir::new("okf-write-test-").unwrap();
        for (path, text) in files {
            let target = dir.0.join(path);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::write(target, text).unwrap();
        }
        dir
    }

    fn digest(root: &Path, concept_id: &str) -> String {
        snapshot_bundle(root, &[])
            .unwrap()
            .concepts
            .into_iter()
            .find(|concept| concept.concept_id == concept_id)
            .unwrap()
            .source_digest
    }

    fn request(root: &Path, body: &str, expected: String, write: bool) -> EditRequest {
        EditRequest {
            path: root.to_owned(),
            concept_id: "a".into(),
            body: body.into(),
            expected_source_digest: expected,
            exclude: Vec::new(),
            write,
        }
    }

    #[test]
    fn raw_documents_round_trip_bom_and_crlf() {
        let source = b"\xef\xbb\xbf---\r\ntype: Note\r\n---\r\n# A\r\n";
        let raw = RawDocument::parse(source).unwrap();
        assert_eq!(raw.render(), source);
        assert_eq!(raw.body, "# A\n");
    }

    #[test]
    fn preview_reports_candidate_digests_without_writing() {
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n# A\n")]);
        let expected = digest(&dir.0, "a");
        let result = edit_concept(&request(&dir.0, "# B\n", expected.clone(), false)).unwrap();
        assert_eq!(result.outcome, EditOutcome::Previewed);
        assert_ne!(result.candidate.source, expected);
        assert_eq!(
            fs::read_to_string(dir.0.join("a.md")).unwrap(),
            "---\ntype: Note\n---\n# A\n"
        );
    }

    #[test]
    fn write_replaces_only_the_body() {
        let dir = bundle(&[("a.md", "---\ntype:   Note  # kept\n---\n# A\n")]);
        let expected = digest(&dir.0, "a");
        let result = edit_concept(&request(&dir.0, "# B\n", expected, true)).unwrap();
        assert_eq!(result.outcome, EditOutcome::Written, "{result:?}");
        assert_eq!(
            fs::read_to_string(dir.0.join("a.md")).unwrap(),
            "---\ntype:   Note  # kept\n---\n# B\n"
        );
    }

    #[test]
    fn stale_digest_fails_closed() {
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n# A\n")]);
        let result = edit_concept(&request(&dir.0, "# B\n", "stale".into(), true)).unwrap();
        assert_eq!(result.outcome, EditOutcome::Stale);
        assert_eq!(result.path, "a.md");
        assert_eq!(
            fs::read_to_string(dir.0.join("a.md")).unwrap(),
            "---\ntype: Note\n---\n# A\n"
        );
    }

    #[test]
    fn a_new_normative_error_blocks_the_write() {
        let original = "---\ntype: Note\n---\n# A\n";
        let dir = bundle(&[("a.md", original)]);
        let snapshot = snapshot_bundle(&dir.0, &[]).unwrap();
        let concept = &snapshot.concepts[0];
        let untyped = RawDocument::parse(b"---\ntitle: A\n---\n# A\n").unwrap();
        let candidates = BTreeMap::from([(
            concept.relative.clone(),
            Candidate {
                raw: untyped,
                live_path: concept.path.clone(),
                expected_hash: concept.content_hash.clone(),
            },
        )]);
        let outcome = stage_validate_write(
            &dir.0,
            &[],
            &candidates,
            &BTreeSet::new(),
            &snapshot.manifest,
        )
        .unwrap();
        let WriteOutcome::Invalid(items) = outcome else {
            panic!("expected a validation failure, got {outcome:?}");
        };
        assert_eq!(items[0].code, Code::Okf002);
        assert_eq!(fs::read_to_string(dir.0.join("a.md")).unwrap(), original);
    }

    #[test]
    fn an_external_change_after_the_snapshot_is_a_conflict() {
        let dir = bundle(&[
            ("a.md", "---\ntype: Note\n---\n# A\n"),
            ("b.md", "---\ntype: Note\n---\n# B\n"),
        ]);
        let snapshot = snapshot_bundle(&dir.0, &[]).unwrap();
        let concept = snapshot
            .concepts
            .iter()
            .find(|c| c.concept_id == "a")
            .unwrap();
        let candidates = BTreeMap::from([(
            concept.relative.clone(),
            Candidate {
                raw: concept.raw.with_body("# changed\n".into()),
                live_path: concept.path.clone(),
                expected_hash: concept.content_hash.clone(),
            },
        )]);
        fs::write(dir.0.join("c.md"), "---\ntype: Note\n---\n").unwrap();
        let outcome = stage_validate_write(
            &dir.0,
            &[],
            &candidates,
            &BTreeSet::new(),
            &snapshot.manifest,
        )
        .unwrap();
        assert_eq!(outcome, WriteOutcome::Conflict(vec!["c.md".into()]));
    }

    #[test]
    fn a_writer_committing_under_the_lock_turns_the_second_into_a_conflict() {
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n# A\n")]);
        let snapshot = snapshot_bundle(&dir.0, &[]).unwrap();
        let concept = &snapshot.concepts[0];
        let candidates = BTreeMap::from([(
            concept.relative.clone(),
            Candidate {
                raw: concept.raw.with_body("# from B\n".into()),
                live_path: concept.path.clone(),
                expected_hash: concept.content_hash.clone(),
            },
        )]);
        // Writer A holds the lock between its recheck and its commit.
        let held = lock_bundle(&dir.0).unwrap();
        let root = dir.0.clone();
        let manifest = snapshot.manifest.clone();
        let (done, finished) = std::sync::mpsc::channel();
        let writer_b = std::thread::spawn(move || {
            let outcome =
                stage_validate_write(&root, &[], &candidates, &BTreeSet::new(), &manifest);
            done.send(()).unwrap();
            outcome
        });
        assert!(
            finished
                .recv_timeout(std::time::Duration::from_millis(300))
                .is_err(),
            "writer B must wait for the lock instead of committing"
        );
        let from_a = "---\ntype: Note\n---\n# from A\n";
        fs::write(dir.0.join("a.md"), from_a).unwrap();
        drop(held);
        let outcome = writer_b.join().unwrap().unwrap();
        assert_eq!(outcome, WriteOutcome::Conflict(vec!["a.md".into()]));
        assert_eq!(fs::read_to_string(dir.0.join("a.md")).unwrap(), from_a);
    }

    #[test]
    fn the_lock_file_is_not_part_of_the_bundle() {
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n# A\n")]);
        let before = snapshot_bundle(&dir.0, &[]).unwrap().manifest;
        drop(lock_bundle(&dir.0).unwrap());
        assert!(dir.0.join(LOCK_FILE).exists());
        assert_eq!(snapshot_bundle(&dir.0, &[]).unwrap().manifest, before);
    }

    #[test]
    fn staged_siblings_are_unique_per_write() {
        let dir = bundle(&[("a.md", "x")]);
        let target = dir.0.join("a.md");
        let first = stage_sibling(&target, b"one").unwrap();
        let second = stage_sibling(&target, b"two").unwrap();
        assert_ne!(first, second);
        assert_eq!(fs::read(&first).unwrap(), b"one");
        assert_eq!(fs::read(&second).unwrap(), b"two");
    }

    #[test]
    fn a_failed_replacement_rolls_back_the_files_already_replaced() {
        let dir = bundle(&[("a.md", "old a"), ("b.md", "old b")]);
        let replacements = vec![
            (dir.0.join("a.md"), b"new a".to_vec()),
            (dir.0.join("b.md"), b"new b".to_vec()),
        ];
        let calls = std::cell::Cell::new(0);
        let error = commit_all(&replacements, |from, to| {
            calls.set(calls.get() + 1);
            if calls.get() == 2 {
                return Err(io::Error::other("injected"));
            }
            fs::rename(from, to)
        })
        .unwrap_err();

        assert!(error.to_string().contains("rolled back 1"), "{error}");
        assert_eq!(fs::read_to_string(dir.0.join("a.md")).unwrap(), "old a");
        assert_eq!(fs::read_to_string(dir.0.join("b.md")).unwrap(), "old b");
        let leftovers: Vec<_> = fs::read_dir(&dir.0)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|name| name.ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "{leftovers:?}");
    }

    #[test]
    fn commit_all_replaces_every_file() {
        let dir = bundle(&[("a.md", "old a"), ("b.md", "old b")]);
        let replacements = vec![
            (dir.0.join("a.md"), b"new a".to_vec()),
            (dir.0.join("b.md"), b"new b".to_vec()),
        ];
        commit_all(&replacements, |from, to| fs::rename(from, to)).unwrap();
        assert_eq!(fs::read_to_string(dir.0.join("a.md")).unwrap(), "new a");
        assert_eq!(fs::read_to_string(dir.0.join("b.md")).unwrap(), "new b");
    }

    #[test]
    fn unknown_concept_is_a_request_error() {
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n")]);
        let mut edit = request(&dir.0, "x", "d".into(), false);
        edit.concept_id = "missing".into();
        let error = edit_concept(&edit).unwrap_err();
        assert!(
            matches!(&error, WriteError::UnknownConcept(id) if id == "missing"),
            "{error:?}"
        );
        assert!(error.is_request());
    }

    #[cfg(unix)]
    #[test]
    fn a_non_utf8_file_name_is_refused_not_replaced() {
        use std::os::unix::ffi::OsStrExt;
        let dir = bundle(&[("a.md", "---\ntype: Note\n---\n# A\n")]);
        let name = std::ffi::OsStr::from_bytes(b"image-\xff.png");
        fs::write(dir.0.join(name), b"x").unwrap();
        let error = snapshot_bundle(&dir.0, &[]).err().unwrap();
        assert!(
            matches!(
                &error,
                WriteError::Path(BundlePathError::NonUtf8(path)) if path.file_name() == Some(name)
            ),
            "{error:?}"
        );
    }
}
