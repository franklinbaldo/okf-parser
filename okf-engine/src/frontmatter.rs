//! Writing frontmatter: lossless edits of authored documents, and the
//! canonical spelling of new ones.
//!
//! Both go through maintained YAML libraries rather than a hand-written
//! emitter (RFC 0024 decision 4): `yaml-edit` edits a concrete syntax tree,
//! so comments, quoting and layout outside the touched keys survive, and
//! `serde_yaml_ng` emits new documents. Every result is parsed back with the
//! engine's own frontmatter parser and must mean exactly what was intended,
//! so a library quirk can refuse a write but never corrupt one.

use std::fmt;
use std::str::FromStr;

use serde_json::{Map, Value};

use crate::yaml::parse_frontmatter;

/// The fields that lead a new document, in this order; the rest follow sorted.
pub const PREFERRED_KEYS: [&str; 3] = ["type", "title", "description"];

/// A frontmatter change that could not be made without changing something
/// else too; the document is left as authored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LossyEdit {
    /// The authored frontmatter is not something the editor can hold.
    Unparseable,
    /// The edited text would not mean the intended mapping.
    Diverged,
}

impl fmt::Display for LossyEdit {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unparseable => f.write_str("frontmatter cannot be edited losslessly"),
            Self::Diverged => f.write_str("the edited frontmatter would not round-trip"),
        }
    }
}

impl std::error::Error for LossyEdit {}

/// Apply `changes` to authored frontmatter: `Some(value)` sets a field to an
/// OKF YAML value, `None` removes it. Everything else is kept byte for byte.
pub fn edit_frontmatter(
    text: &str,
    changes: &[(String, Option<Value>)],
) -> Result<String, LossyEdit> {
    let mut expected = parse_frontmatter(text)
        .map_err(|_| LossyEdit::Unparseable)?
        .mapping;
    let document = if text.trim().is_empty() {
        yaml_edit::Document::from_str("{}\n")
    } else {
        yaml_edit::Document::from_str(text)
    }
    .map_err(|_| LossyEdit::Unparseable)?;
    let mapping = document.as_mapping().ok_or(LossyEdit::Unparseable)?;
    for (field, value) in changes {
        match value {
            Some(Value::String(value)) => {
                mapping.set(field.as_str(), yaml_edit::ScalarValue::string(value));
                expected.insert(field.clone(), Value::String(value.clone()));
            }
            Some(Value::Array(items)) => {
                let sequence = if mapping
                    .get_sequence(field.as_str())
                    .is_some_and(|sequence| !sequence.is_flow_style())
                {
                    yaml_edit::Sequence::new_pending_block()
                } else {
                    yaml_edit::Sequence::new_flow()
                };
                for item in items {
                    match item {
                        Value::String(value) => {
                            sequence.push(yaml_edit::ScalarValue::string(value));
                        }
                        Value::Null => sequence.push(yaml_edit::ScalarValue::null()),
                        _ => return Err(LossyEdit::Diverged),
                    }
                }
                mapping.set(field.as_str(), sequence);
                expected.insert(field.clone(), Value::Array(items.clone()));
            }
            Some(_) => return Err(LossyEdit::Diverged),
            None => {
                mapping.remove(field.as_str());
                expected.remove(field);
            }
        }
    }
    let mut edited = document.to_string();
    if edited.trim() == "{}" {
        edited.clear();
    }
    let actual = parse_frontmatter(&edited)
        .map_err(|_| LossyEdit::Diverged)?
        .mapping;
    if actual == expected {
        Ok(edited)
    } else {
        Err(LossyEdit::Diverged)
    }
}

/// A value outside the OKF frontmatter model (strings, null, lists, mappings).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RenderError {
    /// A number or boolean: OKF frontmatter scalars are strings.
    NonStringScalar { field: String },
    /// The emitted text did not parse back to the same mapping.
    Diverged,
}

impl fmt::Display for RenderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonStringScalar { field } => write!(
                f,
                "frontmatter field {field:?} holds a number or boolean; OKF scalars are strings"
            ),
            Self::Diverged => f.write_str("the rendered frontmatter would not round-trip"),
        }
    }
}

impl std::error::Error for RenderError {}

fn preferred_rank(key: &str) -> usize {
    PREFERRED_KEYS
        .iter()
        .position(|preferred| *preferred == key)
        .unwrap_or(PREFERRED_KEYS.len())
}

fn to_yaml(field: &str, value: &Value) -> Result<serde_yaml_ng::Value, RenderError> {
    Ok(match value {
        Value::Null => serde_yaml_ng::Value::Null,
        Value::String(text) => serde_yaml_ng::Value::String(text.clone()),
        Value::Array(items) => serde_yaml_ng::Value::Sequence(
            items
                .iter()
                .map(|item| to_yaml(field, item))
                .collect::<Result<_, _>>()?,
        ),
        Value::Object(mapping) => {
            let mut keys: Vec<&String> = mapping.keys().collect();
            keys.sort();
            let mut out = serde_yaml_ng::Mapping::with_capacity(keys.len());
            for key in keys {
                out.insert(
                    serde_yaml_ng::Value::String(key.clone()),
                    to_yaml(field, &mapping[key])?,
                );
            }
            serde_yaml_ng::Value::Mapping(out)
        }
        Value::Bool(_) | Value::Number(_) => {
            return Err(RenderError::NonStringScalar {
                field: field.to_owned(),
            });
        }
    })
}

/// The canonical frontmatter of a new document: `type`, `title` and
/// `description` first, every other field and every nested mapping in key
/// order, lists as given.
pub fn render_frontmatter(frontmatter: &Map<String, Value>) -> Result<String, RenderError> {
    let mut keys: Vec<&String> = frontmatter.keys().collect();
    keys.sort_by(|a, b| (preferred_rank(a), *a).cmp(&(preferred_rank(b), *b)));
    let mut mapping = serde_yaml_ng::Mapping::with_capacity(keys.len());
    for key in keys {
        mapping.insert(
            serde_yaml_ng::Value::String(key.clone()),
            to_yaml(key, &frontmatter[key])?,
        );
    }
    let text = if mapping.is_empty() {
        String::new()
    } else {
        serde_yaml_ng::to_string(&serde_yaml_ng::Value::Mapping(mapping))
            .map_err(|_| RenderError::Diverged)?
    };
    let parsed = parse_frontmatter(&text)
        .map_err(|_| RenderError::Diverged)?
        .mapping;
    if parsed == *frontmatter {
        Ok(text)
    } else {
        Err(RenderError::Diverged)
    }
}

/// A whole new OKF document: its frontmatter block, then `body` verbatim.
pub fn render_document(
    frontmatter: &Map<String, Value>,
    body: &str,
) -> Result<String, RenderError> {
    Ok(format!(
        "---\n{}---\n{body}",
        render_frontmatter(frontmatter)?
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn mapping(value: Value) -> Map<String, Value> {
        let Value::Object(mapping) = value else {
            unreachable!()
        };
        mapping
    }

    fn changes(items: &[(&str, Option<&str>)]) -> Vec<(String, Option<Value>)> {
        items
            .iter()
            .map(|(k, v)| {
                (
                    (*k).to_owned(),
                    v.map(|value| Value::String(value.to_owned())),
                )
            })
            .collect()
    }

    #[test]
    fn edits_keep_everything_they_do_not_touch() {
        let text = "type: Nota   # the type\ntitle: 'Quoted'\nstatus: draft # keep\nold: x\n";
        let edited = edit_frontmatter(
            text,
            &changes(&[
                ("status", Some("final")),
                ("old", None),
                ("new", Some("yes")),
            ]),
        )
        .unwrap();
        assert_eq!(
            edited,
            "type: Nota   # the type\ntitle: 'Quoted'\nstatus: final # keep\nnew: 'yes'\n"
        );
    }

    #[test]
    fn list_edits_are_structured_and_preserve_observed_style() {
        let flow = edit_frontmatter(
            "type: Note\ntags: [a, b]\n",
            &[("tags".into(), Some(json!(["a", "c", "123", null])))],
        )
        .unwrap();
        assert!(flow.contains("tags: [a, c, '123', null]"), "{flow}");
        assert_eq!(
            parse_frontmatter(&flow).unwrap().mapping["tags"],
            json!(["a", "c", "123", null])
        );

        let block = edit_frontmatter(
            "type: Note\ntags:\n- a\n- b\n",
            &[("tags".into(), Some(json!(["a", "c"])))],
        )
        .unwrap();
        assert!(block.contains("tags:\n- a\n- c"), "{block}");
        assert_eq!(
            parse_frontmatter(&block).unwrap().mapping["tags"],
            json!(["a", "c"])
        );
    }

    #[test]
    fn edited_values_that_need_quoting_stay_strings() {
        for value in [
            "123",
            "null",
            "a: b",
            "has # hash",
            " lead",
            "",
            "~",
            "multi\nline",
        ] {
            let edited = edit_frontmatter("type: Nota\n", &changes(&[("f", Some(value))])).unwrap();
            let parsed = parse_frontmatter(&edited).unwrap().mapping;
            assert_eq!(parsed["f"], value, "{value:?} -> {edited:?}");
        }
    }

    #[test]
    fn edits_keep_flow_mappings_anchors_and_tags_meaningful() {
        for text in [
            "{type: Note, status: draft}",
            "base: &b\n  k: v\nother:\n  <<: *b\ntype: Note\nstatus: x",
            "type: Note\nstatus: !!str draft",
        ] {
            let edited = edit_frontmatter(text, &changes(&[("status", Some("final"))])).unwrap();
            assert_eq!(
                parse_frontmatter(&edited).unwrap().mapping["status"],
                "final"
            );
        }
    }

    #[test]
    fn frontmatter_that_does_not_parse_is_refused() {
        assert_eq!(
            edit_frontmatter("a: [", &changes(&[("a", Some("b"))])),
            Err(LossyEdit::Unparseable)
        );
    }

    #[test]
    fn rendering_orders_fields_and_round_trips() {
        let frontmatter = mapping(json!({
            "zeta": "1", "type": "Note", "description": "d", "title": "yes",
            "meta": {"z": "1", "a": {"k": "v"}, "l": [{"x": "1"}, []]},
            "empty": {}, "none": [], "n": null, "key: odd": "v", "multi": "a\nb",
            "num": "0012", "tilde": "~", "uni": "ação 知識"
        }));
        let text = render_frontmatter(&frontmatter).unwrap();
        let keys: Vec<&str> = text
            .lines()
            .filter(|line| !line.starts_with(' ') && !line.starts_with('-'))
            .filter_map(|line| line.split(": ").next())
            .collect();
        assert_eq!(&keys[..3], ["type", "title", "description"]);
        assert_eq!(parse_frontmatter(&text).unwrap().mapping, frontmatter);
        assert!(!text.lines().any(|line| line.ends_with(' ')), "{text}");
    }

    #[test]
    fn numbers_and_booleans_are_refused() {
        assert_eq!(
            render_frontmatter(&mapping(json!({"type": "Note", "n": 1}))),
            Err(RenderError::NonStringScalar { field: "n".into() })
        );
    }

    #[test]
    fn a_document_is_its_frontmatter_block_then_the_body() {
        let document = render_document(&mapping(json!({"type": "Note"})), "# A\n").unwrap();
        assert_eq!(document, "---\ntype: Note\n---\n# A\n");
    }
}