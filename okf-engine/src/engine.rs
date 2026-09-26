use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::path::{Component, Path, PathBuf};
use std::{fmt, fs, io};

use chrono::NaiveDate;
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use percent_encoding::percent_decode_str;
use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use walkdir::{DirEntry, WalkDir};

use crate::yaml::{
    Frontmatter, FrontmatterError, canonical_parsed, parse_frontmatter, parse_mapping, sorted_json,
};

pub(crate) const IGNORED: &[&str] = &[
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ty_cache",
    ".venv",
    "node_modules",
];

#[derive(Clone, Serialize, Deserialize)]
pub struct Facts {
    pub links: Vec<String>,
    pub headings: Vec<(u8, String)>,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct ConceptRecord {
    pub concept_id: String,
    pub logical_key: String,
    pub path: String,
    pub concept_type: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub source_digest: String,
    pub parsed_digest: String,
    pub frontmatter_json: String,
    pub body: String,
}
/// The two reserved documents OKF gives a fixed meaning.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ReservedFile {
    #[serde(rename = "index.md")]
    Index,
    #[serde(rename = "log.md")]
    Log,
}
impl ReservedFile {
    /// The reserved document `path` names, if any.
    pub fn of(path: &Path) -> Option<Self> {
        match path.file_name()?.to_str()? {
            "index.md" => Some(Self::Index),
            "log.md" => Some(Self::Log),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Index => "index.md",
            Self::Log => "log.md",
        }
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct ReservedRecord {
    pub path: String,
    pub filename: ReservedFile,
    pub body: String,
}
/// Where a link was found. Only Markdown bodies are scanned today.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LinkOrigin {
    Body,
}
impl LinkOrigin {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Body => "body",
        }
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct LinkRecord {
    pub source_id: String,
    pub raw_target: String,
    pub target_id: Option<String>,
    pub exists: bool,
    pub origin: LinkOrigin,
}
/// How much a diagnostic matters: errors are normative, warnings are advice.
/// Declared in the order diagnostics sort (`error` before `warning`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Error,
    Warning,
}
impl Severity {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warning => "warning",
        }
    }
}
/// The diagnostic codes this engine emits. Declared in code order, so sorting
/// by code matches sorting by the code's spelling.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Code {
    /// A concept could not be read or parsed.
    #[serde(rename = "OKF001")]
    Okf001,
    /// A concept has no non-empty string `type`.
    #[serde(rename = "OKF002")]
    Okf002,
    /// A reserved document could not be read.
    #[serde(rename = "OKF003")]
    Okf003,
    /// `index.md` frontmatter is invalid or misplaced.
    #[serde(rename = "OKF004")]
    Okf004,
    /// `index.md` has no level-one section.
    #[serde(rename = "OKF005")]
    Okf005,
    /// `log.md` has frontmatter.
    #[serde(rename = "OKF006")]
    Okf006,
    /// `log.md` has no level-one title.
    #[serde(rename = "OKF007")]
    Okf007,
    /// A `log.md` date heading is malformed.
    #[serde(rename = "OKF008")]
    Okf008,
    /// `log.md` dates are not newest first.
    #[serde(rename = "OKF009")]
    Okf009,
    /// A local Markdown link does not resolve.
    #[serde(rename = "OKF101")]
    Okf101,
    /// Frontmatter uses YAML tags JSON cannot carry.
    #[serde(rename = "OKF102")]
    Okf102,
}
impl Code {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Okf001 => "OKF001",
            Self::Okf002 => "OKF002",
            Self::Okf003 => "OKF003",
            Self::Okf004 => "OKF004",
            Self::Okf005 => "OKF005",
            Self::Okf006 => "OKF006",
            Self::Okf007 => "OKF007",
            Self::Okf008 => "OKF008",
            Self::Okf009 => "OKF009",
            Self::Okf101 => "OKF101",
            Self::Okf102 => "OKF102",
        }
    }
}
impl fmt::Display for Code {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Diagnostic {
    pub code: Code,
    pub severity: Severity,
    pub path: String,
    pub message: String,
}
#[derive(Serialize, Deserialize)]
pub struct BundleData {
    pub root: String,
    pub concepts: Vec<ConceptRecord>,
    pub reserved: Vec<ReservedRecord>,
    pub links: Vec<LinkRecord>,
    pub diagnostics: Vec<Diagnostic>,
    pub markdown_count: usize,
}
/// A bundle could not be loaded at all (per-document problems are diagnostics).
#[derive(Debug)]
pub enum LoadError {
    /// The root does not exist or cannot be resolved.
    Root(io::Error),
    NotADirectory,
    Concurrency(usize),
    Exclusions(ExclusionError),
    Walk(walkdir::Error),
    ThreadPool(rayon::ThreadPoolBuildError),
    /// A path the bundle must name cannot be a [`BundlePath`].
    Path(BundlePathError),
}
impl fmt::Display for LoadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Root(error) => error.fmt(f),
            Self::NotADirectory => f.write_str("bundle root is not a directory"),
            Self::Concurrency(_) => {
                f.write_str("read concurrency must be an integer from 1 through 256")
            }
            Self::Exclusions(error) => error.fmt(f),
            Self::Walk(error) => error.fmt(f),
            Self::ThreadPool(error) => error.fmt(f),
            Self::Path(error) => error.fmt(f),
        }
    }
}
impl std::error::Error for LoadError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Root(error) => Some(error),
            Self::Exclusions(error) => Some(error),
            Self::Walk(error) => Some(error),
            Self::ThreadPool(error) => Some(error),
            Self::Path(error) => Some(error),
            Self::NotADirectory | Self::Concurrency(_) => None,
        }
    }
}
impl From<ExclusionError> for LoadError {
    fn from(error: ExclusionError) -> Self {
        Self::Exclusions(error)
    }
}
impl From<BundlePathError> for LoadError {
    fn from(error: BundlePathError) -> Self {
        Self::Path(error)
    }
}

/// `.okfignore` or an `--exclude` pattern is not a valid gitignore rule.
#[derive(Debug)]
pub struct ExclusionError(ignore::Error);
impl fmt::Display for ExclusionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for ExclusionError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.0)
    }
}

/// Why a native path cannot become a [`BundlePath`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BundlePathError {
    /// The path is not under the bundle root.
    OutsideRoot { root: PathBuf, path: PathBuf },
    /// The path has no UTF-8 spelling.
    NonUtf8(PathBuf),
}
impl fmt::Display for BundlePathError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OutsideRoot { root, path } => write!(
                f,
                "path is outside the bundle root {}: {}",
                root.display(),
                path.display()
            ),
            Self::NonUtf8(path) => write!(f, "path is not valid UTF-8: {}", path.display()),
        }
    }
}
impl std::error::Error for BundlePathError {}

/// A path inside a bundle as OKF names it: relative to the root, UTF-8,
/// `/`-separated. This is the one place a native path becomes a string, and
/// it refuses instead of replacing what it cannot spell: a lossy conversion
/// could give two distinct files the same concept id.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct BundlePath(String);
impl BundlePath {
    /// Name `path` relative to `root`; a path outside `root` is refused.
    pub fn new(root: &Path, path: &Path) -> Result<Self, BundlePathError> {
        let relative = path
            .strip_prefix(root)
            .map_err(|_| BundlePathError::OutsideRoot {
                root: root.to_owned(),
                path: path.to_owned(),
            })?;
        let text = relative
            .to_str()
            .ok_or_else(|| BundlePathError::NonUtf8(path.to_owned()))?;
        Ok(Self(if std::path::MAIN_SEPARATOR == '/' {
            text.to_owned()
        } else {
            text.replace(std::path::MAIN_SEPARATOR, "/")
        }))
    }
    pub fn as_str(&self) -> &str {
        &self.0
    }
    pub fn into_string(self) -> String {
        self.0
    }
}

/// Why a document's text could not be read.
#[derive(Debug)]
enum ReadError {
    Io(io::Error),
    NotUtf8,
}
impl fmt::Display for ReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => error.fmt(f),
            Self::NotUtf8 => f.write_str("document must be valid UTF-8"),
        }
    }
}

/// Why a document's frontmatter block could not be split or parsed.
#[derive(Debug)]
enum DocumentError {
    /// A concept must open with a frontmatter block.
    NoFrontmatter,
    /// A reserved document opens a frontmatter block it never closes.
    UnclosedFrontmatter,
    Frontmatter(FrontmatterError),
}
impl fmt::Display for DocumentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoFrontmatter => {
                f.write_str("concept must start with YAML frontmatter delimited by ---")
            }
            Self::UnclosedFrontmatter => f.write_str("invalid YAML frontmatter delimiters"),
            Self::Frontmatter(error) => error.fmt(f),
        }
    }
}
impl From<FrontmatterError> for DocumentError {
    fn from(error: FrontmatterError) -> Self {
        Self::Frontmatter(error)
    }
}

struct Loaded {
    path: PathBuf,
    relative: BundlePath,
    content: Result<String, ReadError>,
}
struct Parsed {
    record: ConceptRecord,
    facts: Facts,
    /// YAML tags the frontmatter used that JSON cannot carry (OKF102).
    lossy_tags: Vec<String>,
}

/// Whether `name` ends in `.md`, ignoring ASCII case.
fn md_suffix(name: &str) -> bool {
    name.len() >= 3 && name.as_bytes()[name.len() - 3..].eq_ignore_ascii_case(b".md")
}
pub(crate) fn markdown(path: &Path) -> bool {
    path.file_name()
        .and_then(|v| v.to_str())
        .is_some_and(md_suffix)
}
pub(crate) fn reserved(path: &Path) -> bool {
    ReservedFile::of(path).is_some()
}
/// Whether `name` is a directory every walk skips.
pub(crate) fn ignored_directory(name: &std::ffi::OsStr) -> bool {
    name.to_str().is_some_and(|name| IGNORED.contains(&name))
}
fn traversable(entry: &DirEntry) -> bool {
    entry.depth() == 0
        || (!entry.file_type().is_symlink()
            && (!entry.file_type().is_dir() || !ignored_directory(entry.file_name())))
}
pub(crate) fn exclusions(root: &Path, patterns: &[String]) -> Result<Gitignore, ExclusionError> {
    let mut builder = GitignoreBuilder::new(root);
    let file = root.join(".okfignore");
    if file.is_file()
        && let Some(error) = builder.add(file)
    {
        return Err(ExclusionError(error));
    }
    for pattern in patterns {
        builder.add_line(None, pattern).map_err(ExclusionError)?;
    }
    builder.build().map_err(ExclusionError)
}
pub fn discover(root: &Path, patterns: &[String]) -> Result<Vec<PathBuf>, LoadError> {
    let rules = exclusions(root, patterns)?;
    let mut paths = Vec::new();
    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(traversable)
    {
        let entry = entry.map_err(LoadError::Walk)?;
        if entry.file_type().is_file() && !entry.file_type().is_symlink() && markdown(entry.path())
        {
            let rel = entry.path().strip_prefix(root).unwrap();
            if !rules.matched_path_or_any_parents(rel, false).is_ignore() {
                paths.push(entry.into_path());
            }
        }
    }
    paths.sort();
    Ok(paths)
}
fn level(value: HeadingLevel) -> u8 {
    match value {
        HeadingLevel::H1 => 1,
        HeadingLevel::H2 => 2,
        HeadingLevel::H3 => 3,
        HeadingLevel::H4 => 4,
        HeadingLevel::H5 => 5,
        HeadingLevel::H6 => 6,
    }
}
pub fn markdown_facts(source: &str) -> Facts {
    let mut links = Vec::new();
    let mut headings = Vec::new();
    let mut heading = None;
    for (event, range) in Parser::new(source).into_offset_iter() {
        match &event {
            Event::Start(Tag::Link { dest_url, .. }) => links.push(dest_url.to_string()),
            Event::Start(Tag::Heading { level: value, .. }) => {
                heading = Some((level(*value), None, 0))
            }
            Event::End(TagEnd::Heading(_)) => {
                if let Some((value, start, end)) = heading.take() {
                    headings.push((
                        value,
                        start.map_or_else(String::new, |start| source[start..end].into()),
                    ));
                }
            }
            _ if heading.is_some() => {
                if let Some((_, start, end)) = heading.as_mut() {
                    *start = Some(start.map_or(range.start, |v: usize| v.min(range.start)));
                    *end = (*end).max(range.end);
                }
            }
            _ => {}
        }
    }
    Facts { links, headings }
}
fn delimiter(line: &str) -> bool {
    let v = line.strip_suffix('\n').unwrap_or(line);
    let v = v.strip_suffix('\r').unwrap_or(v);
    v.starts_with("---") && v[3..].trim_matches([' ', '\t']).is_empty()
}
pub(crate) fn split_source(text: &str) -> Option<(&str, &str)> {
    let value = text.strip_prefix('\u{feff}').unwrap_or(text);
    let opening = value.find('\n')?;
    if !delimiter(&value[..=opening]) {
        return None;
    }
    let mut cursor = opening + 1;
    while cursor <= value.len() {
        let newline = value[cursor..].find('\n').map(|v| cursor + v);
        let end = newline.map_or(value.len(), |v| v + 1);
        if delimiter(&value[cursor..end]) {
            let mut block = cursor;
            if block > opening + 1 && value.as_bytes()[block - 1] == b'\n' {
                block -= 1;
                if block > opening + 1 && value.as_bytes()[block - 1] == b'\r' {
                    block -= 1;
                }
            }
            return Some((&value[opening + 1..block], &value[end..]));
        }
        cursor = newline? + 1;
    }
    None
}
type OptionalFrontmatter<'a> = (Option<Map<String, Value>>, &'a str);
fn optional(text: &str) -> Result<OptionalFrontmatter<'_>, DocumentError> {
    let v = text.strip_prefix('\u{feff}').unwrap_or(text);
    if !v.starts_with("---") {
        return Ok((None, v));
    }
    let (s, b) = split_source(v).ok_or(DocumentError::UnclosedFrontmatter)?;
    Ok((Some(parse_mapping(s)?), b))
}
fn field(map: &Map<String, Value>, name: &str) -> Option<String> {
    map.get(name).and_then(Value::as_str).map(str::to_owned)
}
fn id(path: &str) -> String {
    path.rsplit_once('.').map_or(path, |(v, _)| v).to_owned()
}
fn hash(prefix: &str, value: &str) -> String {
    format!("{prefix}{:x}", Sha256::digest(value.as_bytes()))
}
pub(crate) fn normalized_newlines(text: &str) -> Cow<'_, str> {
    if text.as_bytes().contains(&b'\r') {
        Cow::Owned(text.replace("\r\n", "\n").replace('\r', "\n"))
    } else {
        Cow::Borrowed(text)
    }
}
/// A concept's identity and digests: what a writer needs to plan against it.
pub(crate) struct Identity {
    pub(crate) concept_id: String,
    pub(crate) source_digest: String,
    pub(crate) parsed_digest: String,
}

/// The digests of `text`, whose normalized source split into `mapping` and `body`.
fn digests(text: &str, mapping: &Map<String, Value>, body: &str) -> (String, String) {
    (
        hash("sha256:", text),
        hash(
            "okf-parsed-v1-jcs-sha256:",
            &canonical_parsed(mapping, body),
        ),
    )
}

/// Identify a concept from its already-split source, or `None` if it is not
/// one (invalid frontmatter or no `type`). Parses the frontmatter once and
/// allocates only the identity it returns.
pub(crate) fn concept_identity(
    path: &str,
    text: &str,
    frontmatter: &str,
    body: &str,
) -> Option<Identity> {
    let mapping = parse_frontmatter(frontmatter).ok()?.mapping;
    let kind = mapping.get("type").and_then(Value::as_str);
    if kind.is_none_or(|kind| kind.trim().is_empty()) {
        return None;
    }
    let (source_digest, parsed_digest) = digests(text, &mapping, body);
    Some(Identity {
        concept_id: id(path),
        source_digest,
        parsed_digest,
    })
}

fn parse_concept(path: String, text: &str) -> Result<Parsed, DocumentError> {
    let normalized = normalized_newlines(text);
    let (s, b) = split_source(normalized.as_ref()).ok_or(DocumentError::NoFrontmatter)?;
    let Frontmatter {
        mapping: map,
        lossy_tags,
    } = parse_frontmatter(s)?;
    let identity = id(&path);
    let kind = field(&map, "type")
        .map(|v| v.trim().into())
        .unwrap_or_default();
    let (source_digest, parsed_digest) = digests(text, &map, b);
    Ok(Parsed {
        lossy_tags,
        facts: markdown_facts(b),
        record: ConceptRecord {
            concept_id: identity.clone(),
            logical_key: identity,
            path,
            concept_type: kind,
            title: field(&map, "title"),
            description: field(&map, "description"),
            source_digest,
            parsed_digest,
            frontmatter_json: sorted_json(&map),
            body: b.into(),
        },
    })
}
fn diag(code: Code, severity: Severity, path: &str, message: impl Into<String>) -> Diagnostic {
    Diagnostic {
        code,
        severity,
        path: path.into(),
        message: message.into(),
    }
}
fn error(code: Code, path: &str, message: impl Into<String>) -> Diagnostic {
    diag(code, Severity::Error, path, message)
}
fn target(root: &Path, source: &Path, raw: &str) -> Option<PathBuf> {
    let value = raw.split('#').next()?.split('?').next()?;
    if value.is_empty()
        || value.starts_with("//")
        || value.split('/').next().is_some_and(|v| v.contains(':'))
    {
        return None;
    }
    let decoded = percent_decode_str(value).decode_utf8().ok()?;
    let candidate = if decoded.starts_with('/') {
        root.join(decoded.trim_start_matches('/'))
    } else {
        source.parent()?.join(decoded.as_ref())
    };
    let mut out = PathBuf::new();
    for part in candidate.components() {
        match part {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            v => out.push(v.as_os_str()),
        }
    }
    out.starts_with(root).then_some(out)
}
fn md_target(raw: &str) -> bool {
    md_suffix(raw.split(['?', '#']).next().unwrap_or_default())
}
fn has_title(f: &Facts) -> bool {
    f.headings
        .iter()
        .any(|(l, t)| *l == 1 && !t.trim().is_empty())
}
fn validate_reserved(
    root: &Path,
    path: &Path,
    relative: &str,
    kind: ReservedFile,
    text: String,
) -> (ReservedRecord, Vec<Diagnostic>) {
    let record = |body: String| ReservedRecord {
        path: relative.to_owned(),
        filename: kind,
        body,
    };
    let (front, body) = match optional(&text) {
        Ok(v) => v,
        Err(e) => {
            let code = match kind {
                ReservedFile::Index => Code::Okf004,
                ReservedFile::Log => Code::Okf006,
            };
            let diagnostic = error(code, relative, e.to_string());
            return (record(text), vec![diagnostic]);
        }
    };
    let facts = markdown_facts(body);
    let mut ds = Vec::new();
    match kind {
        ReservedFile::Index => {
            if let Some(f) = &front {
                if path.parent() != Some(root) {
                    ds.push(error(
                        Code::Okf004,
                        relative,
                        "only the bundle-root index.md may contain frontmatter",
                    ));
                } else if f.keys().any(|k| k != "okf_version") {
                    ds.push(error(
                        Code::Okf004,
                        relative,
                        "root index.md frontmatter may contain only okf_version",
                    ));
                }
            }
            if !has_title(&facts) {
                ds.push(error(
                    Code::Okf005,
                    relative,
                    "index.md must contain at least one level-one section",
                ));
            }
        }
        ReservedFile::Log => {
            if front.is_some() {
                ds.push(error(
                    Code::Okf006,
                    relative,
                    "log.md must not contain frontmatter",
                ));
            }
            if !has_title(&facts) {
                ds.push(error(
                    Code::Okf007,
                    relative,
                    "log.md must contain a level-one title",
                ));
            }
            let mut dates = Vec::new();
            for (_, h) in facts.headings.iter().filter(|(l, _)| *l == 2) {
                if h.len() != 10
                    || h.as_bytes().get(4) != Some(&b'-')
                    || h.as_bytes().get(7) != Some(&b'-')
                {
                    ds.push(error(
                        Code::Okf008,
                        relative,
                        format!("log date heading must use YYYY-MM-DD: {h}"),
                    ));
                } else if let Ok(d) = NaiveDate::parse_from_str(h, "%Y-%m-%d") {
                    dates.push(d);
                } else {
                    ds.push(error(
                        Code::Okf008,
                        relative,
                        format!("log date heading is not a real date: {h}"),
                    ));
                }
            }
            if dates.windows(2).any(|v| v[0] < v[1]) {
                ds.push(error(
                    Code::Okf009,
                    relative,
                    "log date groups must be ordered newest first",
                ));
            }
        }
    }
    let body = body.to_owned();
    (record(body), ds)
}
/// A concept's links, awaiting resolution once every concept is known.
struct Pending {
    path: PathBuf,
    relative: String,
    concept_id: String,
    links: Vec<String>,
}

pub fn load_bundle(
    root: &Path,
    patterns: &[String],
    concurrency: usize,
) -> Result<BundleData, LoadError> {
    let root = root.canonicalize().map_err(LoadError::Root)?;
    if !root.is_dir() {
        return Err(LoadError::NotADirectory);
    }
    if !(1..=256).contains(&concurrency) {
        return Err(LoadError::Concurrency(concurrency));
    }
    let root_text = root
        .to_str()
        .ok_or_else(|| BundlePathError::NonUtf8(root.clone()))?
        .to_owned();
    let paths = discover(&root, patterns)?;
    let relatives = paths
        .iter()
        .map(|path| BundlePath::new(&root, path))
        .collect::<Result<Vec<_>, _>>()?;
    let known: HashSet<&Path> = paths.iter().map(PathBuf::as_path).collect();
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(concurrency)
        .build()
        .map_err(LoadError::ThreadPool)?;
    let loaded = pool.install(|| {
        paths
            .par_iter()
            .zip(relatives)
            .map(|(path, relative)| Loaded {
                relative,
                path: path.clone(),
                content: fs::read(path)
                    .map_err(ReadError::Io)
                    .and_then(|v| String::from_utf8(v).map_err(|_| ReadError::NotUtf8)),
            })
            .collect::<Vec<_>>()
    });
    let mut concepts = Vec::new();
    let mut reserved_rows = Vec::new();
    let mut diagnostics = Vec::new();
    let mut pending = Vec::new();
    for Loaded {
        path,
        relative,
        content,
    } in loaded
    {
        let relative = relative.into_string();
        let kind = ReservedFile::of(&path);
        let text = match content {
            Ok(v) => v,
            Err(e) => {
                let code = if kind.is_some() {
                    Code::Okf003
                } else {
                    Code::Okf001
                };
                diagnostics.push(error(code, &relative, e.to_string()));
                continue;
            }
        };
        if let Some(kind) = kind {
            let (r, d) = validate_reserved(&root, &path, &relative, kind, text);
            reserved_rows.push(r);
            diagnostics.extend(d);
            continue;
        }
        match parse_concept(relative.clone(), &text) {
            Ok(p) => {
                if p.record.concept_type.is_empty() {
                    diagnostics.push(error(
                        Code::Okf002,
                        &relative,
                        "frontmatter must contain a non-empty string type",
                    ));
                }
                if !p.lossy_tags.is_empty() {
                    diagnostics.push(diag(
                        Code::Okf102,
                        Severity::Warning,
                        &relative,
                        format!(
                            "frontmatter uses YAML tags with no JSON representation ({}); values kept as written",
                            p.lossy_tags.join(", ")
                        ),
                    ));
                }
                pending.push(Pending {
                    path,
                    concept_id: p.record.concept_id.clone(),
                    relative,
                    links: p.facts.links,
                });
                concepts.push(p.record);
            }
            Err(e) => diagnostics.push(error(Code::Okf001, &relative, e.to_string())),
        }
    }
    let ids: HashMap<PathBuf, &str> = concepts
        .iter()
        .map(|r| (root.join(&r.path), r.concept_id.as_str()))
        .collect();
    let mut links = Vec::new();
    for source in pending {
        for raw in source.links {
            if !md_target(&raw) {
                continue;
            }
            let Some(dest) = target(&root, &source.path, &raw) else {
                continue;
            };
            let exists = known.contains(dest.as_path());
            if !exists {
                diagnostics.push(diag(
                    Code::Okf101,
                    Severity::Warning,
                    &source.relative,
                    format!("local Markdown link does not resolve: {raw}"),
                ));
            }
            links.push(LinkRecord {
                source_id: source.concept_id.clone(),
                target_id: if exists && !reserved(&dest) {
                    ids.get(&dest).map(|id| (*id).to_owned())
                } else {
                    None
                },
                raw_target: raw,
                exists,
                origin: LinkOrigin::Body,
            });
        }
    }
    diagnostics.sort_by(|a, b| {
        (&a.path, a.severity, a.code, &a.message).cmp(&(&b.path, b.severity, b.code, &b.message))
    });
    Ok(BundleData {
        root: root_text,
        concepts,
        reserved: reserved_rows,
        links,
        diagnostics,
        markdown_count: paths.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TempBundle(PathBuf);
    impl TempBundle {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "okf-engine-{name}-{}-{:?}",
                std::process::id(),
                std::thread::current().id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for TempBundle {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn closed_sets_serialize_as_their_wire_spelling() {
        let diagnostic = Diagnostic {
            code: Code::Okf101,
            severity: Severity::Warning,
            path: "a.md".into(),
            message: "m".into(),
        };
        assert_eq!(
            serde_json::to_value(&diagnostic).unwrap(),
            serde_json::json!({"code": "OKF101", "severity": "warning", "path": "a.md", "message": "m"})
        );
        assert_eq!(serde_json::to_value(Severity::Error).unwrap(), "error");
        assert_eq!(serde_json::to_value(ReservedFile::Log).unwrap(), "log.md");
        assert_eq!(serde_json::to_value(LinkOrigin::Body).unwrap(), "body");
        for code in [Code::Okf001, Code::Okf009, Code::Okf102] {
            assert_eq!(serde_json::to_value(code).unwrap(), code.as_str());
        }
    }

    #[test]
    fn enum_order_matches_the_spelling_order_diagnostics_sort_by() {
        assert!(Severity::Error < Severity::Warning);
        let codes = [
            Code::Okf001,
            Code::Okf002,
            Code::Okf003,
            Code::Okf004,
            Code::Okf005,
            Code::Okf006,
            Code::Okf007,
            Code::Okf008,
            Code::Okf009,
            Code::Okf101,
            Code::Okf102,
        ];
        for pair in codes.windows(2) {
            assert!(pair[0] < pair[1]);
            assert!(pair[0].as_str() < pair[1].as_str());
        }
    }

    #[test]
    fn diagnostics_carry_typed_codes_and_severities() {
        let bundle = TempBundle::new("typed");
        fs::write(bundle.0.join("a.md"), "---\ntitle: A\n---\n[b](b.md)\n").unwrap();
        let data = load_bundle(&bundle.0, &[], 1).unwrap();
        let found: Vec<_> = data
            .diagnostics
            .iter()
            .map(|d| (d.code, d.severity))
            .collect();
        assert_eq!(
            found,
            [
                (Code::Okf002, Severity::Error),
                (Code::Okf101, Severity::Warning)
            ]
        );
    }

    #[test]
    fn load_errors_are_typed_and_keep_their_wording() {
        let bundle = TempBundle::new("errors");
        assert!(matches!(
            load_bundle(&bundle.0, &[], 0),
            Err(LoadError::Concurrency(0))
        ));
        let error = load_bundle(&bundle.0, &["{a".into()], 1).err().unwrap();
        assert!(matches!(error, LoadError::Exclusions(_)), "{error:?}");
        assert_eq!(
            LoadError::NotADirectory.to_string(),
            "bundle root is not a directory"
        );
    }

    #[cfg(unix)]
    #[test]
    fn non_utf8_paths_are_refused_not_replaced() {
        use std::os::unix::ffi::OsStrExt;
        let bundle = TempBundle::new("non-utf8");
        // Two directories whose names differ only in invalid bytes would
        // collapse to the same U+FFFD spelling under a lossy conversion.
        for name in [&b"n\xff"[..], &b"n\xfe"[..]] {
            let dir = bundle.0.join(std::ffi::OsStr::from_bytes(name));
            fs::create_dir(&dir).unwrap();
            fs::write(dir.join("a.md"), "---\ntype: Note\n---\n").unwrap();
        }
        let error = load_bundle(&bundle.0, &[], 1).err().unwrap();
        assert!(
            matches!(error, LoadError::Path(BundlePathError::NonUtf8(_))),
            "{error:?}"
        );
    }

    #[test]
    fn bundle_paths_are_slash_separated_and_relative() {
        let root = Path::new("/b");
        let path = root.join("x").join("y.md");
        assert_eq!(BundlePath::new(root, &path).unwrap().as_str(), "x/y.md");
    }

    #[test]
    fn markdown_suffix_is_byte_safe_on_unicode_names() {
        // The suffix is compared on bytes, so a multibyte tail cannot land
        // a slice inside a code point.
        for (name, expected) in [
            ("éé", false),
            ("é", false),
            ("日本", false),
            ("ü.MD", true),
            ("知識.md", true),
            ("a.mdé", false),
            ("md", false),
            ("", false),
        ] {
            assert_eq!(md_suffix(name), expected, "{name:?}");
        }
        assert!(md_target("notas/é.md#seção"));
    }

    #[test]
    fn a_path_outside_the_root_is_refused() {
        let root = Path::new("/b");
        for outside in [Path::new("/elsewhere/y.md"), Path::new("relative/y.md")] {
            assert_eq!(
                BundlePath::new(root, outside),
                Err(BundlePathError::OutsideRoot {
                    root: root.to_owned(),
                    path: outside.to_owned(),
                })
            );
        }
    }
}
