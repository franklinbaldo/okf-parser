//! Subject-first OKF commit messages: parse, validate and format.
//!
//! A commit message is a concept whose subject line is its `title`. An
//! optional envelope after the subject carries authored metadata:
//!
//! ```text
//! Subject
//!
//! --- okf
//! type: Change
//! ---
//!
//! Body
//! ```
//!
//! Without an envelope the message is a plain `Commit`. The metadata is
//! read with the same strict YAML as a concept's frontmatter.

use std::fmt;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::engine::{parse_text, parsed_value_digest, source_digest};
use crate::yaml::canonical_json;

const ENVELOPE: &str = "--- okf";
const DEFAULT_TYPE: &str = "Commit";
const RESERVED_KEYS: [&str; 17] = [
    "title",
    "source_kind",
    "repository_identity",
    "object_format",
    "object_type",
    "oid",
    "commit_oid",
    "tree_oid",
    "parent_oids",
    "parents",
    "author",
    "committer",
    "refs",
    "source_digest",
    "parsed_digest",
    "diagnostics",
    "provenance",
];

/// A structural error in a commit message, with a stable code.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CommitMessageError {
    pub code: &'static str,
    pub message: String,
    /// The 1-based source line the error points at, when there is one.
    pub line: Option<usize>,
}

impl CommitMessageError {
    fn new(code: &'static str, message: impl Into<String>, line: Option<usize>) -> Self {
        Self {
            code,
            message: message.into(),
            line,
        }
    }
}

impl fmt::Display for CommitMessageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for CommitMessageError {}

/// The canonical projection of one commit message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommitMessage {
    pub subject: String,
    pub authored_metadata: Map<String, Value>,
    pub effective_frontmatter: Map<String, Value>,
    pub body: String,
    pub has_envelope: bool,
    pub source_digest: String,
    pub parsed_digest: String,
}

/// Split the envelope after `--- okf`: its metadata block, the text after
/// the closing `---`, and the closing line's number.
fn closing_delimiter(envelope: &str) -> Result<(&str, &str, usize), CommitMessageError> {
    let mut lines = envelope.split_inclusive('\n');
    let first = lines.next().unwrap_or_default();
    if first.strip_suffix('\n').unwrap_or(first) != ENVELOPE {
        return Err(CommitMessageError::new(
            "GIT_MESSAGE_ENVELOPE",
            "structured commit metadata must start with exact '--- okf'",
            Some(3),
        ));
    }
    let mut offset = first.len();
    for (index, line) in lines.enumerate() {
        if line.strip_suffix('\n').unwrap_or(line) == "---" {
            let metadata = &envelope[first.len()..offset];
            let after = &envelope[offset + line.len()..];
            return Ok((
                metadata.strip_suffix('\n').unwrap_or(metadata),
                after,
                index + 4,
            ));
        }
        offset += line.len();
    }
    Err(CommitMessageError::new(
        "GIT_MESSAGE_UNTERMINATED_ENVELOPE",
        "structured commit metadata has no closing '---' delimiter",
        Some(3),
    ))
}

fn metadata(block: &str) -> Result<Map<String, Value>, CommitMessageError> {
    parse_text(&format!("---\n{block}\n---\n"), false)
        .map(|parsed| parsed.frontmatter.unwrap_or_default())
        .map_err(|error| CommitMessageError::new("GIT_MESSAGE_YAML", error.to_string(), None))
}

fn effective_type(metadata: &Map<String, Value>) -> Result<String, CommitMessageError> {
    let reserved: Vec<&str> = RESERVED_KEYS
        .iter()
        .copied()
        .filter(|key| metadata.contains_key(*key))
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    if !reserved.is_empty() {
        return Err(CommitMessageError::new(
            "GIT_MESSAGE_RESERVED_KEY",
            format!(
                "commit metadata uses adapter-owned key(s): {}",
                reserved.join(", ")
            ),
            Some(3),
        ));
    }
    match metadata.get("type") {
        None => Ok(DEFAULT_TYPE.to_owned()),
        Some(Value::String(kind)) if !kind.trim().is_empty() => Ok(kind.trim().to_owned()),
        Some(_) => Err(CommitMessageError::new(
            "GIT_MESSAGE_TYPE",
            "commit metadata type must be a non-empty string",
            Some(3),
        )),
    }
}

/// Parse a commit message into its effective OKF projection.
pub fn parse(source: &str) -> Result<CommitMessage, CommitMessageError> {
    let exact_digest = source_digest(source);
    let normalized = source.replace("\r\n", "\n").replace('\r', "\n");
    let normalized = normalized.strip_prefix('\u{feff}').unwrap_or(&normalized);
    let (subject, remainder, separated) = match normalized.split_once('\n') {
        Some((subject, remainder)) => (subject, remainder, true),
        None => (normalized, "", false),
    };
    if subject.trim().is_empty() {
        return Err(CommitMessageError::new(
            "GIT_MESSAGE_EMPTY_SUBJECT",
            "commit subject must be non-empty",
            Some(1),
        ));
    }
    let blank_separator = separated && remainder.starts_with('\n');
    let candidate = if blank_separator {
        &remainder[1..]
    } else {
        remainder
    };
    let has_envelope = candidate == ENVELOPE || candidate.starts_with("--- okf\n");

    let (authored, body) = if has_envelope {
        let (block, after, closing_line) = closing_delimiter(candidate)?;
        if !after.is_empty() && !after.starts_with('\n') {
            return Err(CommitMessageError::new(
                "GIT_MESSAGE_BODY_SEPARATOR",
                "structured commit metadata must be followed by a blank line before the body",
                Some(closing_line + 1),
            ));
        }
        let body = after.strip_prefix('\n').unwrap_or_default();
        if body.lines().any(|line| line == ENVELOPE) {
            return Err(CommitMessageError::new(
                "GIT_MESSAGE_DUPLICATE_ENVELOPE",
                "commit message contains more than one OKF envelope",
                None,
            ));
        }
        (metadata(block)?, body.to_owned())
    } else {
        let body = if blank_separator {
            candidate
        } else {
            remainder
        };
        (Map::new(), body.to_owned())
    };

    let kind = effective_type(&authored)?;
    let mut effective = authored.clone();
    effective.insert("type".to_owned(), Value::String(kind));
    effective.insert("title".to_owned(), Value::String(subject.to_owned()));
    let parsed_digest = parsed_value_digest(&effective, &body);
    Ok(CommitMessage {
        subject: subject.to_owned(),
        authored_metadata: authored,
        effective_frontmatter: effective,
        body,
        has_envelope,
        source_digest: exact_digest,
        parsed_digest,
    })
}

/// Parse, and with `require_envelope`, refuse a plain legacy message.
pub fn validate(source: &str, require_envelope: bool) -> Result<CommitMessage, CommitMessageError> {
    let parsed = parse(source)?;
    if require_envelope && !parsed.has_envelope {
        return Err(CommitMessageError::new(
            "GIT_MESSAGE_ENVELOPE_REQUIRED",
            "repository policy requires an OKF commit envelope",
            Some(3),
        ));
    }
    Ok(parsed)
}

/// The fields formatting reads: the authored parts of a message.
#[derive(Debug, Clone, Deserialize)]
pub struct Authored {
    pub subject: String,
    pub authored_metadata: Map<String, Value>,
    pub body: String,
    pub has_envelope: bool,
}

/// Format a message idempotently: metadata as one canonical JSON pair per
/// line in UTF-16 key order, which is also valid YAML.
pub fn format(message: &Authored) -> String {
    let with_body = |head: String| {
        if message.body.is_empty() {
            head
        } else {
            format!("{head}\n\n{}", message.body)
        }
    };
    if !message.has_envelope {
        return with_body(message.subject.clone());
    }
    let mut keys: Vec<&String> = message.authored_metadata.keys().collect();
    keys.sort_by(|a, b| a.encode_utf16().cmp(b.encode_utf16()));
    let mut block = format!("{ENVELOPE}\n");
    for key in keys {
        block.push_str(&canonical_json(&Value::String(key.clone())));
        block.push_str(": ");
        block.push_str(&canonical_json(&message.authored_metadata[key]));
        block.push('\n');
    }
    block.push_str("---");
    with_body(format!("{}\n\n{block}", message.subject))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn corpus() -> Value {
        serde_json::from_str(include_str!("../../conformance/git-commit-messages.json")).unwrap()
    }

    #[test]
    fn valid_vectors_parse_and_format_idempotently() {
        for case in corpus()["valid"].as_array().unwrap() {
            let source = case["source"].as_str().unwrap();
            let parsed = parse(source).unwrap_or_else(|error| panic!("{source:?}: {error}"));
            assert_eq!(parsed.subject, case["subject"].as_str().unwrap());
            assert_eq!(
                Value::Object(parsed.authored_metadata.clone()),
                case["authored_metadata"]
            );
            assert_eq!(
                Value::Object(parsed.effective_frontmatter.clone()),
                case["effective_frontmatter"]
            );
            assert_eq!(parsed.body, case["body"].as_str().unwrap());
            assert_eq!(parsed.has_envelope, case["has_envelope"].as_bool().unwrap());
            assert_eq!(
                parsed.source_digest,
                case["source_digest"].as_str().unwrap()
            );
            assert_eq!(
                parsed.parsed_digest,
                case["parsed_digest"].as_str().unwrap()
            );

            let authored = Authored {
                subject: parsed.subject.clone(),
                authored_metadata: parsed.authored_metadata.clone(),
                body: parsed.body.clone(),
                has_envelope: parsed.has_envelope,
            };
            let formatted = format(&authored);
            assert_eq!(formatted, case["formatted"].as_str().unwrap(), "{source:?}");
            let reparsed = parse(&formatted).unwrap();
            assert_eq!(reparsed.effective_frontmatter, parsed.effective_frontmatter);
            assert_eq!(reparsed.body, parsed.body);
        }
    }

    #[test]
    fn invalid_vectors_fail_with_their_code() {
        for case in corpus()["invalid"].as_array().unwrap() {
            let source = case["source"].as_str().unwrap();
            let error = parse(source).expect_err(source);
            assert_eq!(error.code, case["code"].as_str().unwrap(), "{source:?}");
        }
    }

    #[test]
    fn policy_can_require_an_envelope() {
        let source = "Legacy commit\n\nStill readable.";
        assert!(!parse(source).unwrap().has_envelope);
        assert_eq!(
            validate(source, true).unwrap_err().code,
            "GIT_MESSAGE_ENVELOPE_REQUIRED"
        );
    }
}
