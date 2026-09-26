use std::cmp::Ordering;
use std::collections::{BTreeSet, HashMap};
use std::fmt;

use serde_json::{Map, Value};
use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser, Tag};
use yaml_rust2::scanner::{Marker, ScanError, TScalarStyle};

const PREFERRED_KEYS: &[&str] = &["type", "title", "description"];
const UNSAFE_VALUE_PREFIXES: &[char] = &[
    '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%', '@', '`',
];

#[derive(Default)]
struct Loader {
    documents: Vec<Value>,
    stack: Vec<(Container, usize)>,
    anchors: HashMap<usize, Value>,
    error: Option<StructureError>,
    lossy_tags: BTreeSet<String>,
}

/// A YAML stream that scanned but does not describe a JSON-shaped document.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StructureError {
    MergeItemNotMapping,
    MergeValueNotMapping,
    DuplicateKey(String),
    NonStringKey,
    UnexpectedEnd,
    KeyWithoutValue,
    UnknownAnchor,
}

impl fmt::Display for StructureError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MergeItemNotMapping => f.write_str("YAML merge sequence items must be mappings"),
            Self::MergeValueNotMapping => f.write_str("YAML merge value must be a mapping"),
            Self::DuplicateKey(key) => write!(f, "duplicated key in mapping: {key}"),
            Self::NonStringKey => f.write_str("frontmatter keys must be strings"),
            Self::UnexpectedEnd => f.write_str("unexpected YAML collection end"),
            Self::KeyWithoutValue => f.write_str("mapping key has no value"),
            Self::UnknownAnchor => f.write_str("frontmatter contains a cyclic YAML anchor"),
        }
    }
}

/// Why a frontmatter block is not an OKF mapping.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FrontmatterError {
    Scan(ScanError),
    Structure(StructureError),
    MultipleDocuments,
    NotAMapping,
}

impl fmt::Display for FrontmatterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Scan(error) => write!(f, "invalid YAML frontmatter: {error}"),
            Self::Structure(error) => write!(f, "invalid YAML frontmatter: {error}"),
            Self::MultipleDocuments => {
                f.write_str("invalid YAML frontmatter: multiple documents are not supported")
            }
            Self::NotAMapping => f.write_str("frontmatter must be a YAML mapping"),
        }
    }
}

impl std::error::Error for FrontmatterError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Scan(error) => Some(error),
            _ => None,
        }
    }
}

enum Container {
    Sequence(Vec<Value>),
    Mapping {
        values: Map<String, Value>,
        key: Option<String>,
    },
}

impl Loader {
    fn insert(&mut self, value: Value, anchor: usize) {
        if anchor > 0 {
            self.anchors.insert(anchor, value.clone());
        }
        let Some((parent, _)) = self.stack.last_mut() else {
            self.documents.push(value);
            return;
        };
        match parent {
            Container::Sequence(values) => values.push(value),
            Container::Mapping { values, key } => {
                if let Some(name) = key.take() {
                    if name == "<<" {
                        let merged = match value {
                            Value::Object(mapping) => vec![mapping],
                            Value::Array(items) => items
                                .into_iter()
                                .map(|item| match item {
                                    Value::Object(mapping) => Ok(mapping),
                                    _ => Err(StructureError::MergeItemNotMapping),
                                })
                                .collect::<Result<Vec<_>, _>>()
                                .unwrap_or_else(|error| {
                                    self.error = Some(error);
                                    Vec::new()
                                }),
                            _ => {
                                self.error = Some(StructureError::MergeValueNotMapping);
                                Vec::new()
                            }
                        };
                        for mapping in merged {
                            for (key, value) in mapping {
                                values.entry(key).or_insert(value);
                            }
                        }
                    } else if let Some(entry) = values.get_mut(&name) {
                        *entry = value;
                        self.error = Some(StructureError::DuplicateKey(name));
                    } else {
                        values.insert(name, value);
                    }
                } else if let Value::String(name) = value {
                    *key = Some(name);
                } else {
                    self.error = Some(StructureError::NonStringKey);
                }
            }
        }
    }

    fn finish(&mut self) {
        let Some((container, anchor)) = self.stack.pop() else {
            self.error = Some(StructureError::UnexpectedEnd);
            return;
        };
        let value = match container {
            Container::Sequence(values) => Value::Array(values),
            Container::Mapping { values, key: None } => Value::Object(values),
            Container::Mapping { key: Some(_), .. } => {
                self.error = Some(StructureError::KeyWithoutValue);
                return;
            }
        };
        self.insert(value, anchor);
    }
}

/// Standard tags whose values have a JSON representation. Scalars keep their
/// spelling (typed scalars stay strings). Any other tag, such as `!!binary`,
/// `!!set` or an application tag, has no faithful JSON form: the value keeps
/// its written spelling or structure, and the tag is reported (OKF102) so a
/// concept is never dropped over one field.
const JSON_TAGS: &[&str] = &[
    "str",
    "null",
    "bool",
    "int",
    "float",
    "timestamp",
    "map",
    "seq",
];

impl Loader {
    /// Remember a tag whose meaning JSON cannot carry.
    fn note_tag(&mut self, tag: Option<&Tag>) {
        let Some(tag) = tag else {
            return;
        };
        let yaml = tag.handle == "tag:yaml.org,2002:";
        let standard = yaml && JSON_TAGS.contains(&tag.suffix.as_str());
        let non_specific = tag.handle == "!" && tag.suffix.is_empty();
        if standard || non_specific {
            return;
        }
        let handle = if yaml { "!!" } else { tag.handle.as_str() };
        self.lossy_tags.insert(format!("{handle}{}", tag.suffix));
    }
}

impl MarkedEventReceiver for Loader {
    fn on_event(&mut self, event: Event, _marker: Marker) {
        if self.error.is_some() {
            return;
        }
        match event {
            Event::SequenceStart(anchor, tag) => {
                self.note_tag(tag.as_ref());
                self.stack.push((Container::Sequence(Vec::new()), anchor));
            }
            Event::MappingStart(anchor, tag) => {
                self.note_tag(tag.as_ref());
                self.stack.push((
                    Container::Mapping {
                        values: Map::new(),
                        key: None,
                    },
                    anchor,
                ));
            }
            Event::SequenceEnd | Event::MappingEnd => self.finish(),
            Event::Scalar(value, style, anchor, tag) => {
                self.note_tag(tag.as_ref());
                let tagged_null = tag.as_ref().is_some_and(|tag: &Tag| {
                    tag.handle == "tag:yaml.org,2002:" && tag.suffix == "null"
                });
                let implicit_null = style == TScalarStyle::Plain
                    && matches!(value.as_str(), "" | "~" | "null" | "Null" | "NULL");
                self.insert(
                    if tagged_null || implicit_null {
                        Value::Null
                    } else {
                        Value::String(value)
                    },
                    anchor,
                );
            }
            Event::Alias(anchor) => match self.anchors.get(&anchor).cloned() {
                Some(value) => self.insert(value, 0),
                None => self.error = Some(StructureError::UnknownAnchor),
            },
            _ => {}
        }
    }
}

fn preferred_rank(key: &str) -> usize {
    PREFERRED_KEYS
        .iter()
        .position(|candidate| *candidate == key)
        .unwrap_or(PREFERRED_KEYS.len())
}

fn simple_key(key: &str) -> bool {
    let mut bytes = key.bytes();
    let Some(first) = bytes.next() else {
        return false;
    };
    if !(first.is_ascii_alphabetic() || first == b'_') {
        return false;
    }
    bytes.all(|value| value.is_ascii_alphanumeric() || matches!(value, b'_' | b'.' | b'-'))
}

fn simple_scalar(value: &str) -> bool {
    value == value.trim()
        && !value.contains('\t')
        && !value.contains('#')
        && !value.contains(": ")
        && !value
            .chars()
            .next()
            .is_some_and(|first| UNSAFE_VALUE_PREFIXES.contains(&first))
}

fn key_follows(previous: &str, current: &str) -> bool {
    let previous_rank = preferred_rank(previous);
    let current_rank = preferred_rank(current);
    if current_rank != previous_rank {
        return current_rank > previous_rank;
    }
    current_rank == PREFERRED_KEYS.len() && current > previous
}

fn try_parse_canonical_mapping(source: &str) -> Option<Map<String, Value>> {
    if source.is_empty() {
        return Some(Map::new());
    }
    let mut result = Map::new();
    let mut previous: Option<&str> = None;
    for line in source.split('\n') {
        let (key, remainder) = line.split_once(':')?;
        if !simple_key(key) {
            return None;
        }
        if let Some(prior) = previous
            && !key_follows(prior, key)
        {
            return None;
        }
        let value = if remainder.is_empty() {
            ""
        } else {
            let value = remainder.strip_prefix(' ')?;
            if value.starts_with(' ') {
                return None;
            }
            value
        };
        if !simple_scalar(value) {
            return None;
        }
        let parsed = if matches!(value, "" | "~" | "null" | "Null" | "NULL") {
            Value::Null
        } else {
            Value::String(value.into())
        };
        if result.insert(key.into(), parsed).is_some() {
            return None;
        }
        previous = Some(key);
    }
    Some(result)
}

/// A frontmatter mapping plus the tags whose meaning it could not carry.
#[derive(Debug)]
pub struct Frontmatter {
    pub mapping: Map<String, Value>,
    /// Tags with no JSON representation, spelled `!!binary` / `!local`, sorted.
    pub lossy_tags: Vec<String>,
}

#[cfg(test)]
fn parse_mapping_yaml(source: &str) -> Result<Map<String, Value>, FrontmatterError> {
    parse_frontmatter_yaml(source).map(|parsed| parsed.mapping)
}

fn parse_frontmatter_yaml(source: &str) -> Result<Frontmatter, FrontmatterError> {
    let mut parser = Parser::new_from_str(source);
    let mut loader = Loader::default();
    parser
        .load(&mut loader, false)
        .map_err(FrontmatterError::Scan)?;
    if let Some(error) = loader.error {
        return Err(FrontmatterError::Structure(error));
    }
    if loader.documents.len() > 1 {
        return Err(FrontmatterError::MultipleDocuments);
    }
    let mapping = match loader.documents.into_iter().next().unwrap_or(Value::Null) {
        Value::Null => Map::new(),
        Value::Object(mapping) => mapping,
        _ => return Err(FrontmatterError::NotAMapping),
    };
    Ok(Frontmatter {
        mapping,
        lossy_tags: loader.lossy_tags.into_iter().collect(),
    })
}

pub fn parse_mapping(source: &str) -> Result<Map<String, Value>, FrontmatterError> {
    parse_frontmatter(source).map(|parsed| parsed.mapping)
}

pub fn parse_frontmatter(source: &str) -> Result<Frontmatter, FrontmatterError> {
    if let Some(mapping) = try_parse_canonical_mapping(source) {
        return Ok(Frontmatter {
            mapping,
            lossy_tags: Vec::new(),
        });
    }
    parse_frontmatter_yaml(source)
}

/// Key order of the canonical JSON: UTF-16 code units (RFC 8785), compared
/// lazily so ordering allocates nothing.
fn utf16_order(left: &str, right: &str) -> Ordering {
    left.encode_utf16().cmp(right.encode_utf16())
}

fn write_json_string(value: &str, output: &mut Vec<u8>) {
    serde_json::to_writer(output, value).expect("serializing a str into a Vec cannot fail");
}

fn write_object(object: &Map<String, Value>, output: &mut Vec<u8>) {
    let mut entries: Vec<(&String, &Value)> = object.iter().collect();
    entries.sort_unstable_by(|(left, _), (right, _)| utf16_order(left, right));
    output.push(b'{');
    for (index, (key, value)) in entries.into_iter().enumerate() {
        if index > 0 {
            output.push(b',');
        }
        write_json_string(key, output);
        output.push(b':');
        write_value(value, output);
    }
    output.push(b'}');
}

/// Write `value` as compact JSON with every object's keys in UTF-16 order.
fn write_value(value: &Value, output: &mut Vec<u8>) {
    match value {
        Value::Object(object) => write_object(object, output),
        Value::Array(values) => {
            output.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(b',');
                }
                write_value(value, output);
            }
            output.push(b']');
        }
        Value::String(text) => write_json_string(text, output),
        other => serde_json::to_writer(output, other)
            .expect("serializing a JSON scalar into a Vec cannot fail"),
    }
}

fn into_string(output: Vec<u8>) -> String {
    String::from_utf8(output).expect("serde_json writes UTF-8")
}

pub fn sorted_json(mapping: &Map<String, Value>) -> String {
    let mut output = Vec::new();
    write_object(mapping, &mut output);
    into_string(output)
}

/// The canonical `[frontmatter, body]` pair the parsed digest is taken over.
pub fn canonical_parsed(mapping: &Map<String, Value>, body: &str) -> String {
    let mut output = Vec::with_capacity(body.len() + 64);
    output.push(b'[');
    write_object(mapping, &mut output);
    output.push(b',');
    write_json_string(body, &mut output);
    output.push(b']');
    into_string(output)
}

#[cfg(test)]
mod tests {
    use super::{
        FrontmatterError, StructureError, canonical_parsed, parse_frontmatter, parse_mapping,
        parse_mapping_yaml, sorted_json, try_parse_canonical_mapping,
    };
    use serde_json::{Map, Value, json};

    /// The writer this module used before it borrowed: clone into a `Value`,
    /// sort keys by an allocated UTF-16 vector, serialize each piece.
    fn reference(value: &Value, output: &mut String) {
        match value {
            Value::Object(object) => {
                output.push('{');
                let mut entries = object.iter().collect::<Vec<_>>();
                entries.sort_by_key(|(key, _)| key.encode_utf16().collect::<Vec<_>>());
                for (index, (key, value)) in entries.into_iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    output.push_str(&serde_json::to_string(key).unwrap());
                    output.push(':');
                    reference(value, output);
                }
                output.push('}');
            }
            Value::Array(values) => {
                output.push('[');
                for (index, value) in values.iter().enumerate() {
                    if index > 0 {
                        output.push(',');
                    }
                    reference(value, output);
                }
                output.push(']');
            }
            other => output.push_str(&serde_json::to_string(other).unwrap()),
        }
    }

    fn tricky_mapping() -> Map<String, Value> {
        // UTF-16 puts U+10000 (a 0xD800 surrogate) before U+FFFF; UTF-8
        // and char order put it after.
        let value = json!({
            "\u{10000}": "astral",
            "\u{ffff}": "bmp",
            "type": "Note",
            "b": [1, "two", null, true, {"z": 1, "a": [{"y": 2, "x": 1}]}],
            "a\"quote": "line\nbreak\t\u{1}",
            "é": 1.5,
            "": {}
        });
        let Value::Object(mapping) = value else {
            unreachable!()
        };
        mapping
    }

    #[test]
    fn canonical_writer_is_byte_identical_to_the_reference() {
        let mapping = tricky_mapping();
        let mut expected = String::new();
        reference(&Value::Object(mapping.clone()), &mut expected);
        assert_eq!(sorted_json(&mapping), expected);

        let body = "# Título\n\n\"quoted\" \u{1F600}\n";
        let mut expected = String::new();
        reference(
            &Value::Array(vec![
                Value::Object(mapping.clone()),
                Value::String(body.into()),
            ]),
            &mut expected,
        );
        assert_eq!(canonical_parsed(&mapping, body), expected);
    }

    #[test]
    fn canonical_keys_follow_utf16_order() {
        let json = sorted_json(&tricky_mapping());
        assert!(json.find("\u{10000}").unwrap() < json.find("\u{ffff}").unwrap());
    }

    #[test]
    fn structural_errors_are_typed() {
        assert_eq!(
            parse_frontmatter("a: 1\na: 2").err(),
            Some(FrontmatterError::Structure(StructureError::DuplicateKey(
                "a".into()
            )))
        );
        assert_eq!(
            parse_frontmatter("- a").err(),
            Some(FrontmatterError::NotAMapping)
        );
        assert!(matches!(
            parse_frontmatter("a: [").err(),
            Some(FrontmatterError::Scan(_))
        ));
    }

    #[test]
    fn frontmatter_errors_keep_their_diagnostic_wording() {
        let error = parse_frontmatter("a: 1\na: 2").unwrap_err();
        assert_eq!(
            error.to_string(),
            "invalid YAML frontmatter: duplicated key in mapping: a"
        );
        assert_eq!(
            FrontmatterError::NotAMapping.to_string(),
            "frontmatter must be a YAML mapping"
        );
    }

    #[test]
    fn json_representable_tags_are_accepted() {
        let source = "type: !!str Reference\nn: !!int 3\nitems: !!seq [a]\nmeta: !!map {k: v}";

        assert!(parse_mapping(source).is_ok());
    }

    #[test]
    fn tags_with_no_json_representation_keep_their_spelling_and_are_reported() {
        let parsed =
            parse_frontmatter("type: Note\nblob: !!binary aGk=\ncustom: !thing value").unwrap();
        assert_eq!(parsed.mapping["blob"], "aGk=");
        assert_eq!(parsed.mapping["custom"], "value");
        assert_eq!(parsed.lossy_tags, ["!!binary", "!thing"]);
    }

    #[test]
    fn tagged_collections_keep_their_structure() {
        let parsed = parse_frontmatter("items: !!set {a: null}\npairs: !!omap [a: 1]").unwrap();
        assert_eq!(parsed.mapping["items"], serde_json::json!({"a": null}));
        assert_eq!(parsed.mapping["pairs"], serde_json::json!([{"a": "1"}]));
        assert_eq!(parsed.lossy_tags, ["!!omap", "!!set"]);
    }

    #[test]
    fn json_representable_tags_are_not_reported() {
        let parsed = parse_frontmatter("type: !!str Reference\nn: !!int 3").unwrap();
        assert!(parsed.lossy_tags.is_empty());
    }

    #[test]
    fn canonical_flat_mapping_matches_yaml_parser() {
        let source = "type: Reference\ntitle: Example\ndescription: Plain text\nactive: false\ncreated: 2026-08-28\nnothing: null";

        assert_eq!(
            try_parse_canonical_mapping(source),
            Some(parse_mapping_yaml(source).unwrap())
        );
        assert_eq!(parse_mapping(source), parse_mapping_yaml(source));
    }

    #[test]
    fn out_of_order_mapping_falls_back_without_changing_semantics() {
        let source = "title: Example\ntype: Reference\nactive: false";

        assert!(try_parse_canonical_mapping(source).is_none());
        assert_eq!(parse_mapping(source), parse_mapping_yaml(source));
    }

    #[test]
    fn complex_yaml_falls_back_without_changing_semantics() {
        let source = "type: Reference\nitems:\n  - one\n  - two";

        assert!(try_parse_canonical_mapping(source).is_none());
        assert_eq!(parse_mapping(source), parse_mapping_yaml(source));
    }

    #[test]
    fn comments_and_quoted_scalars_stay_on_yaml_path() {
        for source in [
            "type: Reference # comment",
            "type: 'Reference'",
            "type: Reference\ntitle: Example: subtitle",
        ] {
            assert!(try_parse_canonical_mapping(source).is_none());
            assert_eq!(parse_mapping(source), parse_mapping_yaml(source));
        }
    }
}
