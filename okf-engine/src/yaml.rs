use std::collections::HashMap;

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
    error: Option<String>,
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
                                    _ => Err("YAML merge sequence items must be mappings"),
                                })
                                .collect::<Result<Vec<_>, _>>()
                                .unwrap_or_else(|error| {
                                    self.error = Some(error.into());
                                    Vec::new()
                                }),
                            _ => {
                                self.error = Some("YAML merge value must be a mapping".into());
                                Vec::new()
                            }
                        };
                        for mapping in merged {
                            for (key, value) in mapping {
                                values.entry(key).or_insert(value);
                            }
                        }
                    } else if values.insert(name.clone(), value).is_some() {
                        self.error = Some(format!("duplicated key in mapping: {name}"));
                    }
                } else if let Value::String(name) = value {
                    *key = Some(name);
                } else {
                    self.error = Some("frontmatter keys must be strings".into());
                }
            }
        }
    }

    fn finish(&mut self) {
        let Some((container, anchor)) = self.stack.pop() else {
            self.error = Some("unexpected YAML collection end".into());
            return;
        };
        let value = match container {
            Container::Sequence(values) => Value::Array(values),
            Container::Mapping { values, key: None } => Value::Object(values),
            Container::Mapping { key: Some(_), .. } => {
                self.error = Some("mapping key has no value".into());
                return;
            }
        };
        self.insert(value, anchor);
    }
}

impl MarkedEventReceiver for Loader {
    fn on_event(&mut self, event: Event, _marker: Marker) {
        if self.error.is_some() {
            return;
        }
        match event {
            Event::SequenceStart(anchor, _) => {
                self.stack.push((Container::Sequence(Vec::new()), anchor));
            }
            Event::MappingStart(anchor, _) => self.stack.push((
                Container::Mapping {
                    values: Map::new(),
                    key: None,
                },
                anchor,
            )),
            Event::SequenceEnd | Event::MappingEnd => self.finish(),
            Event::Scalar(value, style, anchor, tag) => {
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
                None => self.error = Some("frontmatter contains a cyclic YAML anchor".into()),
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

fn parse_mapping_yaml(source: &str) -> Result<Map<String, Value>, String> {
    let mut parser = Parser::new_from_str(source);
    let mut loader = Loader::default();
    parser
        .load(&mut loader, false)
        .map_err(|error: ScanError| format!("invalid YAML frontmatter: {error}"))?;
    if let Some(error) = loader.error {
        return Err(format!("invalid YAML frontmatter: {error}"));
    }
    if loader.documents.len() > 1 {
        return Err("invalid YAML frontmatter: multiple documents are not supported".into());
    }
    match loader.documents.into_iter().next().unwrap_or(Value::Null) {
        Value::Null => Ok(Map::new()),
        Value::Object(mapping) => Ok(mapping),
        _ => Err("frontmatter must be a YAML mapping".into()),
    }
}

pub fn parse_mapping(source: &str) -> Result<Map<String, Value>, String> {
    if let Some(mapping) = try_parse_canonical_mapping(source) {
        return Ok(mapping);
    }
    parse_mapping_yaml(source)
}

fn write_sorted(value: &Value, output: &mut String, spaced: bool) {
    match value {
        Value::Object(object) => {
            output.push('{');
            let mut entries = object.iter().collect::<Vec<_>>();
            entries.sort_by_key(|(key, _)| key.encode_utf16().collect::<Vec<_>>());
            for (index, (key, value)) in entries.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                    if spaced {
                        output.push(' ');
                    }
                }
                output.push_str(&serde_json::to_string(key).unwrap());
                output.push(':');
                if spaced {
                    output.push(' ');
                }
                write_sorted(value, output, spaced);
            }
            output.push('}');
        }
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                    if spaced {
                        output.push(' ');
                    }
                }
                write_sorted(value, output, spaced);
            }
            output.push(']');
        }
        other => output.push_str(&serde_json::to_string(other).unwrap()),
    }
}

pub fn sorted_json(mapping: &Map<String, Value>) -> String {
    let mut output = String::new();
    write_sorted(&Value::Object(mapping.clone()), &mut output, false);
    output
}

pub fn canonical_parsed(mapping: &Map<String, Value>, body: &str) -> String {
    let mut output = String::new();
    write_sorted(
        &Value::Array(vec![
            Value::Object(mapping.clone()),
            Value::String(body.into()),
        ]),
        &mut output,
        false,
    );
    output
}

#[cfg(test)]
mod tests {
    use super::{parse_mapping, parse_mapping_yaml, try_parse_canonical_mapping};

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
