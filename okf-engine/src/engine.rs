use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

use chrono::NaiveDate;
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use percent_encoding::percent_decode_str;
use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use walkdir::{DirEntry, WalkDir};

use crate::yaml::{canonical_parsed, parse_mapping, sorted_json};

const IGNORED: &[&str] = &[
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
#[derive(Clone, Serialize, Deserialize)]
pub struct ReservedRecord {
    pub path: String,
    pub filename: String,
    pub body: String,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct LinkRecord {
    pub source_id: String,
    pub raw_target: String,
    pub target_id: Option<String>,
    pub exists: bool,
    pub origin: String,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Diagnostic {
    pub code: String,
    pub severity: String,
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
struct Loaded {
    path: PathBuf,
    relative: String,
    content: Result<String, String>,
}
struct Parsed {
    record: ConceptRecord,
    facts: Facts,
}

fn relative(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap()
        .to_string_lossy()
        .replace('\\', "/")
}
fn markdown(path: &Path) -> bool {
    path.file_name()
        .and_then(|v| v.to_str())
        .is_some_and(|v| v.to_ascii_lowercase().ends_with(".md"))
}
fn reserved(path: &Path) -> bool {
    matches!(
        path.file_name().and_then(|v| v.to_str()),
        Some("index.md" | "log.md")
    )
}
fn traversable(entry: &DirEntry) -> bool {
    entry.depth() == 0
        || (!entry.file_type().is_symlink()
            && (!entry.file_type().is_dir()
                || !IGNORED.contains(&entry.file_name().to_string_lossy().as_ref())))
}
fn exclusions(root: &Path, patterns: &[String]) -> Result<Gitignore, String> {
    let mut builder = GitignoreBuilder::new(root);
    let file = root.join(".okfignore");
    if file.is_file()
        && let Some(error) = builder.add(file)
    {
        return Err(error.to_string());
    }
    for pattern in patterns {
        builder.add_line(None, pattern).map_err(|e| e.to_string())?;
    }
    builder.build().map_err(|e| e.to_string())
}
pub fn discover(root: &Path, patterns: &[String]) -> Result<Vec<PathBuf>, String> {
    let rules = exclusions(root, patterns)?;
    let mut paths = Vec::new();
    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(traversable)
    {
        let entry = entry.map_err(|e| e.to_string())?;
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
fn split_source(text: &str) -> Option<(&str, &str)> {
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
fn optional(text: &str) -> Result<OptionalFrontmatter<'_>, String> {
    let v = text.strip_prefix('\u{feff}').unwrap_or(text);
    if !v.starts_with("---") {
        return Ok((None, v));
    }
    let (s, b) = split_source(v).ok_or("invalid YAML frontmatter delimiters")?;
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
fn normalized_newlines(text: &str) -> Cow<'_, str> {
    if text.as_bytes().contains(&b'\r') {
        Cow::Owned(text.replace("\r\n", "\n").replace('\r', "\n"))
    } else {
        Cow::Borrowed(text)
    }
}
fn parse_concept(path: String, text: String) -> Result<Parsed, String> {
    let normalized = normalized_newlines(&text);
    let (s, b) = split_source(normalized.as_ref())
        .ok_or("concept must start with YAML frontmatter delimited by ---")?;
    let map = parse_mapping(s)?;
    let identity = id(&path);
    let kind = field(&map, "type")
        .map(|v| v.trim().into())
        .unwrap_or_default();
    Ok(Parsed {
        facts: markdown_facts(b),
        record: ConceptRecord {
            concept_id: identity.clone(),
            logical_key: identity,
            path,
            concept_type: kind,
            title: field(&map, "title"),
            description: field(&map, "description"),
            source_digest: hash("sha256:", &text),
            parsed_digest: hash("okf-parsed-v1-jcs-sha256:", &canonical_parsed(&map, b)),
            frontmatter_json: sorted_json(&map),
            body: b.into(),
        },
    })
}
fn diag(code: &str, severity: &str, path: &str, message: impl Into<String>) -> Diagnostic {
    Diagnostic {
        code: code.into(),
        severity: severity.into(),
        path: path.into(),
        message: message.into(),
    }
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
    raw.split(['?', '#'])
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase()
        .ends_with(".md")
}
fn has_title(f: &Facts) -> bool {
    f.headings
        .iter()
        .any(|(l, t)| *l == 1 && !t.trim().is_empty())
}
fn validate_reserved(root: &Path, doc: &Loaded) -> (ReservedRecord, Vec<Diagnostic>) {
    let text = doc.content.as_ref().unwrap();
    let filename = doc.path.file_name().unwrap().to_string_lossy().to_string();
    let mut ds = Vec::new();
    let (front, body) = match optional(text) {
        Ok(v) => v,
        Err(e) => {
            let c = if filename == "index.md" {
                "OKF004"
            } else {
                "OKF006"
            };
            return (
                ReservedRecord {
                    path: doc.relative.clone(),
                    filename,
                    body: text.clone(),
                },
                vec![diag(c, "error", &doc.relative, e)],
            );
        }
    };
    let facts = markdown_facts(body);
    if filename == "index.md" {
        if let Some(f) = &front {
            if doc.path.parent() != Some(root) {
                ds.push(diag(
                    "OKF004",
                    "error",
                    &doc.relative,
                    "only the bundle-root index.md may contain frontmatter",
                ));
            } else if f.keys().any(|k| k != "okf_version") {
                ds.push(diag(
                    "OKF004",
                    "error",
                    &doc.relative,
                    "root index.md frontmatter may contain only okf_version",
                ));
            }
        }
        if !has_title(&facts) {
            ds.push(diag(
                "OKF005",
                "error",
                &doc.relative,
                "index.md must contain at least one level-one section",
            ));
        }
    } else {
        if front.is_some() {
            ds.push(diag(
                "OKF006",
                "error",
                &doc.relative,
                "log.md must not contain frontmatter",
            ));
        }
        if !has_title(&facts) {
            ds.push(diag(
                "OKF007",
                "error",
                &doc.relative,
                "log.md must contain a level-one title",
            ));
        }
        let mut dates = Vec::new();
        for (_, h) in facts.headings.iter().filter(|(l, _)| *l == 2) {
            if h.len() != 10
                || h.as_bytes().get(4) != Some(&b'-')
                || h.as_bytes().get(7) != Some(&b'-')
            {
                ds.push(diag(
                    "OKF008",
                    "error",
                    &doc.relative,
                    format!("log date heading must use YYYY-MM-DD: {h}"),
                ));
            } else if let Ok(d) = NaiveDate::parse_from_str(h, "%Y-%m-%d") {
                dates.push(d);
            } else {
                ds.push(diag(
                    "OKF008",
                    "error",
                    &doc.relative,
                    format!("log date heading is not a real date: {h}"),
                ));
            }
        }
        if dates.windows(2).any(|v| v[0] < v[1]) {
            ds.push(diag(
                "OKF009",
                "error",
                &doc.relative,
                "log date groups must be ordered newest first",
            ));
        }
    }
    (
        ReservedRecord {
            path: doc.relative.clone(),
            filename,
            body: body.into(),
        },
        ds,
    )
}
pub fn load_bundle(
    root: &Path,
    patterns: &[String],
    concurrency: usize,
) -> Result<BundleData, String> {
    let root = root.canonicalize().map_err(|e| e.to_string())?;
    if !root.is_dir() {
        return Err("bundle root is not a directory".into());
    }
    if !(1..=256).contains(&concurrency) {
        return Err("read concurrency must be an integer from 1 through 256".into());
    }
    let paths = discover(&root, patterns)?;
    let known: HashSet<_> = paths.iter().cloned().collect();
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(concurrency)
        .build()
        .map_err(|e| e.to_string())?;
    let loaded = pool.install(|| {
        paths
            .par_iter()
            .map(|path| Loaded {
                relative: relative(&root, path),
                path: path.clone(),
                content: fs::read(path).map_err(|e| e.to_string()).and_then(|v| {
                    String::from_utf8(v).map_err(|_| "document must be valid UTF-8".into())
                }),
            })
            .collect::<Vec<_>>()
    });
    let mut concepts = Vec::new();
    let mut reserved_rows = Vec::new();
    let mut diagnostics = Vec::new();
    let mut facts = Vec::new();
    for doc in loaded {
        let Loaded {
            path,
            relative,
            content,
        } = doc;
        let text = match content {
            Ok(v) => v,
            Err(e) => {
                diagnostics.push(diag(
                    if reserved(&path) { "OKF003" } else { "OKF001" },
                    "error",
                    &relative,
                    e,
                ));
                continue;
            }
        };
        if reserved(&path) {
            let doc = Loaded {
                path,
                relative,
                content: Ok(text),
            };
            let (r, d) = validate_reserved(&root, &doc);
            reserved_rows.push(r);
            diagnostics.extend(d);
        } else {
            match parse_concept(relative.clone(), text) {
                Ok(p) => {
                    if p.record.concept_type.is_empty() {
                        diagnostics.push(diag(
                            "OKF002",
                            "error",
                            &relative,
                            "frontmatter must contain a non-empty string type",
                        ));
                    }
                    facts.push((path, p.facts));
                    concepts.push(p.record);
                }
                Err(e) => diagnostics.push(diag("OKF001", "error", &relative, e)),
            }
        }
    }
    let ids: HashMap<_, _> = concepts
        .iter()
        .map(|r| (root.join(&r.path), r.concept_id.clone()))
        .collect();
    let mut links = Vec::new();
    for (source, f) in &facts {
        let source_id = id(&relative(&root, source));
        for raw in &f.links {
            if !md_target(raw) {
                continue;
            }
            let Some(dest) = target(&root, source, raw) else {
                continue;
            };
            let exists = known.contains(&dest);
            links.push(LinkRecord {
                source_id: source_id.clone(),
                raw_target: raw.clone(),
                target_id: if exists && !reserved(&dest) {
                    ids.get(&dest).cloned()
                } else {
                    None
                },
                exists,
                origin: "body".into(),
            });
            if !exists {
                diagnostics.push(diag(
                    "OKF101",
                    "warning",
                    &relative(&root, source),
                    format!("local Markdown link does not resolve: {raw}"),
                ));
            }
        }
    }
    diagnostics.sort_by(|a, b| {
        (&a.path, &a.severity, &a.code, &a.message).cmp(&(
            &b.path,
            &b.severity,
            &b.code,
            &b.message,
        ))
    });
    Ok(BundleData {
        root: root.to_string_lossy().into(),
        concepts,
        reserved: reserved_rows,
        links,
        diagnostics,
        markdown_count: paths.len(),
    })
}
