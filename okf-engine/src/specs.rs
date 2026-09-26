//! Type specifications: the document every producer-defined type in use may be
//! required to have, and the fields that document may require.
//!
//! OKF v0.2 only requires `type` to be non-empty, so a producer can invent a
//! type and keep a green check while its frontmatter changes underneath its
//! consumers. These rules close that gap without inventing taxonomy: the
//! document path is *derived* from the type name through a template (a
//! `spec:` field would be a second fact free to disagree), and a document's
//! `## Required fields` table names the frontmatter each concept must author.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;
use std::{fmt, fs, io};

use serde::Serialize;
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;

use crate::{Code, ConceptRecord, Diagnostic, Severity};

/// The placeholder a specification template must contain.
pub const SLUG_PLACEHOLDER: &str = "{slug}";

/// A specification template that cannot address any type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpecTemplateError {
    pub template: String,
}

impl fmt::Display for SpecTemplateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "specification template must contain {SLUG_PLACEHOLDER}: {:?}",
            self.template
        )
    }
}

impl std::error::Error for SpecTemplateError {}

/// A template that contains `{slug}`, so it can address every type.
#[derive(Debug, Clone, Copy)]
pub struct SpecTemplate<'a>(&'a str);

impl<'a> SpecTemplate<'a> {
    pub fn new(template: &'a str) -> Result<Self, SpecTemplateError> {
        if template.contains(SLUG_PLACEHOLDER) {
            Ok(Self(template))
        } else {
            Err(SpecTemplateError {
                template: template.to_owned(),
            })
        }
    }

    pub fn as_str(self) -> &'a str {
        self.0
    }

    /// The bundle-relative document path expected for `concept_type`, or
    /// `None` when the type has no slug (a non-Latin name can produce none).
    pub fn relative_path(self, concept_type: &str) -> Option<String> {
        let slug = type_slug(concept_type);
        (!slug.is_empty()).then(|| self.0.replace(SLUG_PLACEHOLDER, &slug))
    }
}

/// Python's `str.isspace`, which the slug rules were written against: Unicode
/// whitespace plus the information separators U+001C..U+001F.
fn is_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

/// The filesystem-safe slug of one producer-defined type.
///
/// Compatibility decomposition drops accents and cedillas, whitespace and `/`
/// become hyphens, every other character outside `[a-z0-9-]` is removed, and
/// hyphen runs collapse.
pub fn type_slug(concept_type: &str) -> String {
    let stripped: String = concept_type
        .nfkd()
        .filter(|character| !is_combining_mark(*character))
        .collect();
    let lowered = stripped.trim_matches(is_space).to_lowercase();
    let mut slug = String::with_capacity(lowered.len());
    let mut separator = false;
    for character in lowered.chars() {
        if is_space(character) || character == '/' {
            separator = true;
            continue;
        }
        if separator {
            slug.push('-');
            separator = false;
        }
        if character.is_ascii_lowercase() || character.is_ascii_digit() || character == '-' {
            slug.push(character);
        }
    }
    if separator {
        slug.push('-');
    }
    let mut collapsed = String::with_capacity(slug.len());
    for character in slug.chars() {
        if !(character == '-' && collapsed.ends_with('-')) {
            collapsed.push(character);
        }
    }
    collapsed.trim_matches('-').to_owned()
}

fn severity(normative: bool) -> Severity {
    if normative {
        Severity::Error
    } else {
        Severity::Warning
    }
}

/// `OKF010`: every type in use whose derived specification document is absent.
///
/// Advisory unless `normative`: a bundle mid-adoption legitimately has legacy
/// types without a document, which is not an OKF v0.2 defect.
pub fn missing_type_specs<'a>(
    root: &Path,
    concept_types: impl IntoIterator<Item = &'a str>,
    template: SpecTemplate<'_>,
    normative: bool,
) -> Vec<Diagnostic> {
    let types: BTreeSet<&str> = concept_types
        .into_iter()
        .filter(|kind| !kind.is_empty())
        .collect();
    let mut diagnostics = Vec::new();
    for concept_type in types {
        let (path, message) = match template.relative_path(concept_type) {
            None => (
                template.as_str().to_owned(),
                format!(r#"type "{concept_type}" has no slug usable as a document path"#),
            ),
            Some(relative) if root.join(&relative).is_file() => continue,
            Some(relative) => (
                relative,
                format!(r#"type "{concept_type}" has no specification document"#),
            ),
        };
        diagnostics.push(Diagnostic {
            code: Code::Okf010,
            severity: severity(normative),
            path,
            message,
        });
    }
    diagnostics
}

/// Python's `str.splitlines`: every Unicode line boundary, `\r\n` as one.
fn split_lines(text: &str) -> impl Iterator<Item = &str> {
    let mut rest = text;
    std::iter::from_fn(move || {
        if rest.is_empty() {
            return None;
        }
        let boundary = rest.char_indices().find(|(_, c)| {
            matches!(
                c,
                '\n' | '\r'
                    | '\u{0b}'
                    | '\u{0c}'
                    | '\u{1c}'
                    | '\u{1d}'
                    | '\u{1e}'
                    | '\u{85}'
                    | '\u{2028}'
                    | '\u{2029}'
            )
        });
        Some(match boundary {
            None => std::mem::take(&mut rest),
            Some((index, character)) => {
                let line = &rest[..index];
                let mut next = index + character.len_utf8();
                if character == '\r' && rest[next..].starts_with('\n') {
                    next += 1;
                }
                rest = &rest[next..];
                line
            }
        })
    })
}

/// The first column of a document's `## Required fields` pipe table.
///
/// Specifications stay ordinary Markdown; this recognizes only the small
/// explicit contract OKF producer specs already use.
pub fn required_fields(spec: &str) -> Vec<String> {
    let mut in_required = false;
    let mut fields: Vec<String> = Vec::new();
    for line in split_lines(spec) {
        let stripped = line.trim_matches(is_space);
        if let Some(heading) = stripped.strip_prefix("## ") {
            let heading = heading.trim_matches(is_space).to_lowercase();
            if in_required && heading != "required fields" {
                break;
            }
            in_required = heading == "required fields";
            continue;
        }
        if !in_required || !stripped.starts_with('|') {
            continue;
        }
        let first = stripped
            .trim_matches('|')
            .split('|')
            .next()
            .unwrap_or_default()
            .trim_matches(is_space);
        if first.is_empty() || first.to_lowercase() == "field" {
            continue;
        }
        if first.chars().all(|c| c == '-' || c == ':') {
            continue;
        }
        let field = first.trim_matches('`').trim_matches(is_space);
        if !field.is_empty() && !fields.iter().any(|known| known == field) {
            fields.push(field.to_owned());
        }
    }
    fields
}

fn read_required_fields(root: &Path, template: SpecTemplate<'_>, kind: &str) -> Vec<String> {
    let Some(relative) = template.relative_path(kind) else {
        return Vec::new();
    };
    let path = root.join(relative);
    if !path.is_file() {
        return Vec::new();
    }
    fs::read_to_string(path)
        .map(|text| required_fields(&text))
        .unwrap_or_default()
}

/// `OKF011`: authored frontmatter missing a field its type's spec requires.
pub fn required_type_spec_fields(
    root: &Path,
    concepts: &[ConceptRecord],
    template: SpecTemplate<'_>,
    normative: bool,
) -> Vec<Diagnostic> {
    let mut required_by_type: HashMap<&str, Vec<String>> = HashMap::new();
    let mut diagnostics = Vec::new();
    for concept in concepts {
        let kind = concept.concept_type.as_str();
        if kind.is_empty() {
            continue;
        }
        let required = required_by_type
            .entry(kind)
            .or_insert_with(|| read_required_fields(root, template, kind));
        if required.is_empty() {
            continue;
        }
        let Ok(Value::Object(frontmatter)) = serde_json::from_str(&concept.frontmatter_json) else {
            continue;
        };
        for field in required.iter() {
            let missing = match frontmatter.get(field) {
                None | Some(Value::Null) => true,
                Some(Value::String(text)) => text.trim_matches(is_space).is_empty(),
                Some(_) => false,
            };
            if missing {
                diagnostics.push(Diagnostic {
                    code: Code::Okf011,
                    severity: severity(normative),
                    path: concept.path.clone(),
                    message: format!(
                        r#"type "{kind}" requires non-empty frontmatter field "{field}""#
                    ),
                });
            }
        }
    }
    diagnostics
}

/// Two or more types that derive the same document path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Collision {
    pub path: String,
    pub types: Vec<String>,
}

/// What scaffolding the missing specification documents did, or would do.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Scaffold {
    pub created: Vec<String>,
    pub would_create: Vec<String>,
    pub collisions: Vec<Collision>,
    pub written: bool,
}

fn stub(concept_type: &str) -> String {
    format!(
        "---\ntype: Spec\n---\n\n# {concept_type}\n\n\
         TODO: describe this type's frontmatter fields and semantics.\n"
    )
}

/// Create a minimal specification stub for every type in use that lacks one.
///
/// A derived-path collision blocks every write of the call, so a caller never
/// gets a partial scaffold silently missing the types it could not resolve.
/// Existing documents are never touched.
pub fn scaffold_missing_specs<'a>(
    root: &Path,
    concept_types: impl IntoIterator<Item = &'a str>,
    template: SpecTemplate<'_>,
    write: bool,
) -> io::Result<Scaffold> {
    let types: BTreeSet<&str> = concept_types
        .into_iter()
        .filter(|kind| !kind.is_empty())
        .collect();
    let mut by_path: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for kind in types {
        if let Some(relative) = template.relative_path(kind) {
            by_path.entry(relative).or_default().push(kind);
        }
    }
    let collisions: Vec<Collision> = by_path
        .iter()
        .filter(|(_, types)| types.len() > 1)
        .map(|(path, types)| Collision {
            path: path.clone(),
            types: types.iter().map(|kind| (*kind).to_owned()).collect(),
        })
        .collect();
    if !collisions.is_empty() {
        return Ok(Scaffold {
            created: Vec::new(),
            would_create: Vec::new(),
            collisions,
            written: false,
        });
    }
    let to_create: Vec<(&str, String)> = by_path
        .into_iter()
        .filter(|(relative, _)| !root.join(relative).is_file())
        .map(|(relative, types)| (types[0], relative))
        .collect();
    if !write {
        return Ok(Scaffold {
            created: Vec::new(),
            would_create: to_create
                .into_iter()
                .map(|(_, relative)| relative)
                .collect(),
            collisions: Vec::new(),
            written: false,
        });
    }
    let mut created = Vec::with_capacity(to_create.len());
    for (kind, relative) in to_create {
        let destination = root.join(&relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&destination, stub(kind))?;
        created.push(relative);
    }
    Ok(Scaffold {
        created,
        would_create: Vec::new(),
        collisions: Vec::new(),
        written: true,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_match_the_python_reference() {
        // Golden values produced by okf_parser.type_specs.type_slug.
        for (kind, slug) in [
            ("Reference", "reference"),
            ("Nota Técnica", "nota-tecnica"),
            ("Ação Civil Pública", "acao-civil-publica"),
            ("a/b c", "a-b-c"),
            ("  Spaced  Out ", "spaced-out"),
            ("ﬁ ligature", "fi-ligature"),
            ("Ǆ", "dz"),
            ("日本語", ""),
            ("a\u{1c}b", "a-b"),
            ("x--y", "x-y"),
            ("-lead-trail-", "lead-trail"),
            ("İstanbul", "istanbul"),
            ("ß straße", "strae"),
            ("Ⅻ roman", "xii-roman"),
            ("K kelvin", "k-kelvin"),
            ("a_b.c", "abc"),
            ("MixedCASE 42", "mixedcase-42"),
            ("Ω ohm", "ohm"),
            ("e\u{301}", "e"),
            ("under_score/slash", "underscore-slash"),
        ] {
            assert_eq!(type_slug(kind), slug, "{kind:?}");
        }
    }

    #[test]
    fn slugs_match_the_shared_conformance_cases() {
        // The same cases pin okf_parser.type_specs.type_slug.
        let cases: Vec<serde_json::Map<String, Value>> =
            serde_json::from_str(include_str!("../../conformance/type-spec-slugs.json")).unwrap();
        assert!(!cases.is_empty());
        for case in cases {
            let kind = case["concept_type"].as_str().unwrap();
            assert_eq!(type_slug(kind), case["slug"], "{}", case["name"]);
        }
    }

    #[test]
    fn a_template_without_the_placeholder_is_refused() {
        assert_eq!(
            SpecTemplate::new("docs/types.md").unwrap_err().to_string(),
            r#"specification template must contain {slug}: "docs/types.md""#
        );
    }

    #[test]
    fn required_fields_read_the_first_column_of_the_section_table() {
        let spec = "# Note\r\n\r\n## Required fields\r\n\r\n| Field | Meaning |\r\n\
                    |---|:--|\r\n| `owner` | who |\r\n| status | s |\r\n| owner | dup |\r\n\
                    ## Optional\n| later | x |\n";
        assert_eq!(required_fields(spec), ["owner", "status"]);
    }

    #[test]
    fn lines_split_like_python() {
        let lines: Vec<_> = split_lines("a\r\nb\rc\u{2028}d\n").collect();
        assert_eq!(lines, ["a", "b", "c", "d"]);
    }
}
