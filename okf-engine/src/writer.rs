use serde_json::{Map, Number, Value};
use yaml_rust2::yaml::{Hash, Yaml};
use yaml_rust2::YamlEmitter;

fn number_to_yaml(number: &Number) -> Yaml {
    if let Some(value) = number.as_i64() {
        return Yaml::Integer(value);
    }
    if let Some(value) = number.as_u64()
        && value <= i64::MAX as u64
    {
        return Yaml::Integer(value as i64);
    }
    Yaml::Real(number.to_string())
}

fn value_to_yaml(value: &Value) -> Yaml {
    match value {
        Value::Null => Yaml::Null,
        Value::Bool(value) => Yaml::Boolean(*value),
        Value::Number(value) => number_to_yaml(value),
        Value::String(value) => Yaml::String(value.clone()),
        Value::Array(values) => Yaml::Array(values.iter().map(value_to_yaml).collect()),
        Value::Object(values) => Yaml::Hash(mapping_to_yaml(values)),
    }
}

fn mapping_to_yaml(mapping: &Map<String, Value>) -> Hash {
    let mut result = Hash::new();
    for (key, value) in mapping {
        result.insert(Yaml::String(key.clone()), value_to_yaml(value));
    }
    result
}

pub fn render_mapping(mapping: &Map<String, Value>) -> Result<String, String> {
    let document = Yaml::Hash(mapping_to_yaml(mapping));
    let mut output = String::new();
    YamlEmitter::new(&mut output)
        .dump(&document)
        .map_err(|error| format!("could not emit YAML: {error}"))?;
    let normalized = output.replace("\r\n", "\n").replace('\r', "\n");
    Ok(normalized.strip_prefix("---\n").unwrap_or(&normalized).to_owned())
}

pub fn render_frontmatter(mapping: &Map<String, Value>) -> Result<Vec<u8>, String> {
    let yaml = render_mapping(mapping)?;
    Ok(format!("---\n{}---\n", yaml.trim_end_matches('\n')).into_bytes())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{render_frontmatter, render_mapping};

    #[test]
    fn renders_native_scalars_sequences_and_mappings() {
        let value = json!({
            "type": "Observation",
            "count": 3,
            "active": true,
            "items": ["one", "two"],
            "meta": {"source": "test"}
        });
        let mapping = value.as_object().unwrap();

        let rendered = render_mapping(mapping).unwrap();

        assert!(rendered.contains("count: 3"));
        assert!(rendered.contains("active: true"));
        assert!(rendered.contains("items:"));
        assert!(rendered.contains("- one"));
        assert!(rendered.contains("meta:"));
        assert!(rendered.contains("source: test"));
    }

    #[test]
    fn frontmatter_is_utf8_lf_only() {
        let value = json!({"type": "Observation", "items": ["a", "b"]});
        let bytes = render_frontmatter(value.as_object().unwrap()).unwrap();

        assert!(bytes.starts_with(b"---\n"));
        assert!(bytes.ends_with(b"---\n"));
        assert!(!bytes.windows(2).any(|pair| pair == b"\r\n"));
    }
}
