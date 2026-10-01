//! The language-neutral schema contract: one object tree per concept type,
//! compiled from every document's frontmatter, explicit casts and the
//! type's declared `.schema.sql`.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use unicode_general_category::{GeneralCategory, get_general_category};
use unicode_normalization::UnicodeNormalization;

use super::SchemaError;
use crate::catalog::{LogicalType, TypeFamily};

/// The scalar vocabulary observations and casts share.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CastKind {
    String,
    Boolean,
    Integer,
    Number,
    Date,
    Datetime,
}

impl CastKind {
    fn parse(kind: &str) -> Option<Self> {
        match kind {
            "string" => Some(Self::String),
            "boolean" => Some(Self::Boolean),
            "integer" => Some(Self::Integer),
            "number" => Some(Self::Number),
            "date" => Some(Self::Date),
            "datetime" => Some(Self::Datetime),
            _ => None,
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::String => "string",
            Self::Boolean => "boolean",
            Self::Integer => "integer",
            Self::Number => "number",
            Self::Date => "date",
            Self::Datetime => "datetime",
        }
    }

    /// The observation kind a declared catalog type stands for.
    pub const fn of_family(family: TypeFamily) -> Option<Self> {
        match family {
            TypeFamily::String | TypeFamily::Uuid => Some(Self::String),
            TypeFamily::Boolean => Some(Self::Boolean),
            TypeFamily::Integer => Some(Self::Integer),
            TypeFamily::Float | TypeFamily::Decimal => Some(Self::Number),
            TypeFamily::Date => Some(Self::Date),
            TypeFamily::Timestamp | TypeFamily::Timestamptz => Some(Self::Datetime),
            TypeFamily::List | TypeFamily::Unsupported => None,
        }
    }
}

/// One node of a contract tree.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "node", rename_all = "snake_case")]
pub enum Node {
    Scalar {
        kind: CastKind,
        #[serde(skip_serializing_if = "Option::is_none")]
        declared_type: Option<LogicalType>,
    },
    /// The concept's own `type`.
    Literal {
        value: String,
    },
    /// Observations mixing incompatible structural categories.
    Any,
    List {
        item: Box<Node>,
        item_nullable: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        declared_type: Option<LogicalType>,
    },
    /// A value identifying a document of another concept type.
    Ref(Reference),
    Object {
        fields: Vec<Field>,
    },
}

/// A reference: the carried value plus the declared foreign key it follows.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Reference {
    pub concept_type: String,
    pub columns: Vec<String>,
    pub referenced_columns: Vec<String>,
    pub position: usize,
    pub value: Box<Node>,
    pub embedded: bool,
}

impl Reference {
    /// The metadata every format publishes about a reference.
    pub fn metadata(&self) -> Value {
        serde_json::json!({
            "type": self.concept_type,
            "columns": self.columns,
            "referencedColumns": self.referenced_columns,
            "position": self.position,
        })
    }

    /// The one-line description formats without metadata carry.
    pub fn description(&self) -> String {
        format!(
            "references {}({})",
            self.concept_type,
            self.referenced_columns.join(", ")
        )
    }
}

/// One object field: presence and nullability are independent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Field {
    pub name: String,
    pub required: bool,
    pub nullable: bool,
    pub value: Node,
}

/// One concept type and its generated model name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Contract {
    pub concept_type: String,
    pub model_name: String,
    pub fields: Vec<Field>,
}

/// Parse repeatable `FIELD=TYPE` casts, rejecting ambiguity.
pub fn parse_casts(specifications: &[String]) -> Result<BTreeMap<String, CastKind>, SchemaError> {
    let mut casts: BTreeMap<String, CastKind> = BTreeMap::new();
    for specification in specifications {
        let parsed = specification.rsplit_once('=').and_then(|(path, kind)| {
            let path = path.trim();
            let kind = CastKind::parse(&kind.trim().to_lowercase())?;
            (!path.is_empty()).then(|| (path.to_owned(), kind))
        });
        let Some((path, kind)) = parsed else {
            return Err(SchemaError::Cast(format!(
                "invalid cast {}; expected FIELD=TYPE, where TYPE is boolean, date, datetime, \
                 integer, number, string",
                py_repr(specification)
            )));
        };
        if let Some(previous) = casts.get(&path)
            && *previous != kind
        {
            return Err(SchemaError::Cast(format!(
                "field {} has conflicting casts: {} and {}",
                py_repr(&path),
                previous.name(),
                kind.name()
            )));
        }
        casts.insert(path, kind);
    }
    Ok(casts)
}

/// A string as Python's `repr` spells it, for messages callers already know.
pub fn py_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for c in value.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_control() => out.push_str(&format!("\\x{:02x}", u32::from(c))),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python's `str.isalnum` for one character: a letter or a number.
fn is_alnum(c: char) -> bool {
    use GeneralCategory as G;
    matches!(
        get_general_category(c),
        G::UppercaseLetter
            | G::LowercaseLetter
            | G::TitlecaseLetter
            | G::ModifierLetter
            | G::OtherLetter
            | G::DecimalNumber
            | G::LetterNumber
            | G::OtherNumber
    )
}

/// Python's `str.isidentifier`.
fn is_identifier(value: &str) -> bool {
    let mut chars = value.chars();
    chars
        .next()
        .is_some_and(|c| c == '_' || unicode_ident::is_xid_start(c))
        && chars.all(unicode_ident::is_xid_continue)
}

/// The shared deterministic Unicode-aware generated identifier.
pub fn model_name(value: &str, suffix: &str) -> String {
    let normalized: String = value.nfkc().collect();
    let identifier: String = normalized
        .chars()
        .map(|c| if c == '_' || is_alnum(c) { c } else { '_' })
        .collect();
    let mut name: String = identifier
        .trim_matches('_')
        .split('_')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            let first = chars.next().map(|c| c.to_uppercase().collect::<String>());
            format!("{}{}", first.unwrap_or_default(), chars.as_str())
        })
        .collect();
    if name.is_empty() {
        name = "Concept".to_owned();
    }
    if name.chars().next().is_some_and(|c| {
        matches!(get_general_category(c), GeneralCategory::DecimalNumber) || c.is_ascii_digit()
    }) {
        name = format!("Concept{name}");
    }
    if !is_identifier(&name) {
        let encoded: String = normalized
            .chars()
            .map(|c| format!("U{:04X}", u32::from(c)))
            .collect();
        name = if encoded.is_empty() {
            "Concept".to_owned()
        } else {
            format!("Concept{encoded}")
        };
    }
    format!("{name}{suffix}")
}

/// Names for every value, refusing two values that normalize alike.
pub fn unique_model_names<'a>(
    values: impl IntoIterator<Item = &'a str>,
    suffix: &str,
) -> Result<BTreeMap<String, String>, SchemaError> {
    let values: BTreeSet<&str> = values.into_iter().collect();
    let mut names = BTreeMap::new();
    let mut owners: BTreeMap<String, &str> = BTreeMap::new();
    for value in values {
        let name = model_name(value, suffix);
        if let Some(previous) = owners.get(&name)
            && *previous != value
        {
            return Err(SchemaError::NameCollision(format!(
                "concept types {} and {} both normalize to {}",
                py_repr(previous),
                py_repr(value),
                py_repr(&name)
            )));
        }
        names.insert(value.to_owned(), name.clone());
        owners.insert(name, value);
    }
    Ok(names)
}

/// Compile options shared by every type.
pub struct CompileOptions<'a> {
    pub infer_types: bool,
    pub casts: BTreeMap<String, CastKind>,
    pub declared: &'a BTreeMap<String, BTreeMap<String, LogicalType>>,
}

struct Compiler<'a> {
    options: CompileOptions<'a>,
    used_casts: BTreeSet<String>,
}

/// Compile every type's documents into one contract each, ordered by type.
pub fn compile_contracts(
    documents_by_type: &BTreeMap<String, Vec<Map<String, Value>>>,
    options: CompileOptions<'_>,
) -> Result<Vec<Contract>, SchemaError> {
    let names = unique_model_names(documents_by_type.keys().map(String::as_str), "Concept")?;
    let mut compiler = Compiler {
        options,
        used_casts: BTreeSet::new(),
    };
    let mut contracts = Vec::with_capacity(documents_by_type.len());
    for (concept_type, documents) in documents_by_type {
        let documents: Vec<&Map<String, Value>> = documents.iter().collect();
        let fields = compiler.object(&documents, "", Some(concept_type))?;
        contracts.push(Contract {
            concept_type: concept_type.clone(),
            model_name: names[concept_type].clone(),
            fields,
        });
    }
    let unused: Vec<String> = compiler
        .options
        .casts
        .keys()
        .filter(|path| !compiler.used_casts.contains(*path))
        .map(|path| py_repr(path))
        .collect();
    if !unused.is_empty() {
        return Err(SchemaError::Cast(format!(
            "cast field was not found in the bundle: {}",
            unused.join(", ")
        )));
    }
    Ok(contracts)
}

impl Compiler<'_> {
    fn object(
        &mut self,
        documents: &[&Map<String, Value>],
        parent: &str,
        concept_type: Option<&str>,
    ) -> Result<Vec<Field>, SchemaError> {
        let mut keys: BTreeSet<String> = documents
            .iter()
            .flat_map(|document| document.keys().cloned())
            .collect();
        if let Some(concept_type) = concept_type {
            keys.insert("type".to_owned());
            if parent.is_empty()
                && let Some(declared) = self.options.declared.get(concept_type)
            {
                keys.extend(declared.keys().cloned());
            }
        }
        let mut fields = Vec::with_capacity(keys.len());
        for name in keys {
            let path = if parent.is_empty() {
                name.clone()
            } else {
                format!("{parent}.{name}")
            };
            let present: Vec<&Value> = documents
                .iter()
                .filter_map(|document| document.get(&name))
                .collect();
            let (required, nullable, value) = match concept_type {
                Some(concept_type) if name == "type" => (
                    true,
                    false,
                    Node::Literal {
                        value: concept_type.to_owned(),
                    },
                ),
                _ => (
                    present.len() == documents.len(),
                    present.iter().any(|value| value.is_null()),
                    self.value(&present, &path, concept_type)?,
                ),
            };
            fields.push(Field {
                name,
                required,
                nullable,
                value,
            });
        }
        Ok(fields)
    }

    fn value(
        &mut self,
        values: &[&Value],
        path: &str,
        concept_type: Option<&str>,
    ) -> Result<Node, SchemaError> {
        let non_null: Vec<&Value> = values.iter().copied().filter(|v| !v.is_null()).collect();
        let declared = match concept_type {
            Some(concept_type) if !self.options.casts.contains_key(path) => self
                .options
                .declared
                .get(concept_type)
                .and_then(|columns| columns.get(path)),
            _ => None,
        };
        if let Some(declared) = declared {
            return Ok(declared_node(declared, &non_null));
        }
        if non_null.is_empty() {
            return self.scalar(&[], path);
        }
        if non_null.iter().all(|value| value.is_object()) {
            if self.options.casts.contains_key(path) {
                return Err(SchemaError::Cast(format!(
                    "cannot cast {}: the field contains objects",
                    py_repr(path)
                )));
            }
            let documents: Vec<&Map<String, Value>> = non_null
                .iter()
                .filter_map(|value| value.as_object())
                .collect();
            return Ok(Node::Object {
                fields: self.object(&documents, path, None)?,
            });
        }
        if non_null.iter().all(|value| value.is_array()) {
            let items: Vec<&Value> = non_null
                .iter()
                .filter_map(|value| value.as_array())
                .flatten()
                .collect();
            return Ok(Node::List {
                item: Box::new(self.value(&items, path, None)?),
                item_nullable: items.iter().any(|item| item.is_null()),
                declared_type: None,
            });
        }
        if non_null
            .iter()
            .any(|value| value.is_object() || value.is_array())
        {
            if self.options.casts.contains_key(path) {
                return Err(SchemaError::Cast(format!(
                    "cannot cast {}: the field mixes scalar and structured values",
                    py_repr(path)
                )));
            }
            return Ok(Node::Any);
        }
        let texts: Vec<String> = non_null.iter().map(|value| scalar_text(value)).collect();
        self.scalar(&texts, path)
    }

    fn scalar(&mut self, values: &[String], path: &str) -> Result<Node, SchemaError> {
        if let Some(&explicit) = self.options.casts.get(path) {
            self.used_casts.insert(path.to_owned());
            if !can_classify_as(values, explicit) {
                let sample = values
                    .iter()
                    .find(|value| !can_classify_as(std::slice::from_ref(value), explicit))
                    .unwrap_or(&values[0]);
                return Err(SchemaError::Cast(format!(
                    "cannot cast {} to {}: {} is incompatible",
                    py_repr(path),
                    explicit.name(),
                    py_repr(sample)
                )));
            }
            return Ok(Node::Scalar {
                kind: explicit,
                declared_type: None,
            });
        }
        let kind = if self.options.infer_types {
            classify_lexemes(values)
        } else {
            CastKind::String
        };
        Ok(Node::Scalar {
            kind,
            declared_type: None,
        })
    }
}

/// A scalar's text: the frontmatter view keeps every scalar as a string.
fn scalar_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

fn declared_node(declared: &LogicalType, non_null: &[&Value]) -> Node {
    match (&declared.family, &declared.element) {
        (TypeFamily::List, Some(element)) => {
            let item_nullable = non_null
                .iter()
                .filter_map(|value| value.as_array())
                .flatten()
                .any(Value::is_null);
            Node::List {
                item: Box::new(declared_node(element, &[])),
                item_nullable,
                declared_type: Some(declared.clone()),
            }
        }
        _ => Node::Scalar {
            kind: CastKind::of_family(declared.family).unwrap_or(CastKind::String),
            declared_type: Some(declared.clone()),
        },
    }
}

fn is_signed_digits(value: &str) -> bool {
    let digits = value.strip_prefix(['+', '-']).unwrap_or(value);
    !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit())
}

fn is_number(value: &str) -> bool {
    let body = value.strip_prefix(['+', '-']).unwrap_or(value);
    let (mantissa, exponent) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], Some(&body[at + 1..])),
        None => (body, None),
    };
    let mantissa_ok = match mantissa.split_once('.') {
        None => !mantissa.is_empty() && mantissa.bytes().all(|b| b.is_ascii_digit()),
        Some((whole, fraction)) => {
            whole.bytes().all(|b| b.is_ascii_digit())
                && fraction.bytes().all(|b| b.is_ascii_digit())
                && (!whole.is_empty() || !fraction.is_empty())
        }
    };
    mantissa_ok && exponent.is_none_or(is_signed_digits)
}

fn digits(value: &str) -> Option<u32> {
    (!value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()))
        .then(|| value.parse().ok())
        .flatten()
}

fn is_calendar_date(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return false;
    }
    let (Some(year), Some(month), Some(day)) = (
        digits(&value[..4]),
        digits(&value[5..7]),
        digits(&value[8..]),
    ) else {
        return false;
    };
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    year >= 1 && (1..=12).contains(&month) && day >= 1 && day <= days[month as usize - 1]
}

/// `YYYY-MM-DD[Tt]HH:MM[:SS[.frac]][Z|±HH:MM]`, with a real calendar date.
fn is_datetime(value: &str) -> bool {
    if value.len() < 16 || !value.is_char_boundary(10) || !is_calendar_date(&value[..10]) {
        return false;
    }
    let rest = &value[10..];
    let Some(rest) = rest.strip_prefix(['T', 't']) else {
        return false;
    };
    let two = |text: &str, max: u32| digits(text).is_some_and(|n| text.len() == 2 && n <= max);
    if rest.len() < 5 || !rest.is_char_boundary(5) || &rest[2..3] != ":" {
        return false;
    }
    if !two(&rest[..2], 23) || !two(&rest[3..5], 59) {
        return false;
    }
    let mut rest = &rest[5..];
    if let Some(seconds) = rest.strip_prefix(':') {
        if seconds.len() < 2 || !seconds.is_char_boundary(2) || !two(&seconds[..2], 59) {
            return false;
        }
        rest = &seconds[2..];
        if let Some(fraction) = rest.strip_prefix('.') {
            let length = fraction.bytes().take_while(u8::is_ascii_digit).count();
            if length == 0 {
                return false;
            }
            rest = &fraction[length..];
        }
    }
    match rest {
        "" | "Z" | "z" => true,
        offset => {
            offset.len() == 6
                && offset.starts_with(['+', '-'])
                && &offset[3..4] == ":"
                && two(&offset[1..3], 23)
                && two(&offset[4..], 59)
        }
    }
}

/// Classify an aggregate only when every spelling supports one kind.
pub fn classify_lexemes(values: &[String]) -> CastKind {
    if values.is_empty() {
        return CastKind::String;
    }
    let all = |test: fn(&str) -> bool| values.iter().all(|value| test(value));
    if values.iter().all(|value| {
        let folded = caseless::default_case_fold_str(value);
        folded == "true" || folded == "false"
    }) {
        CastKind::Boolean
    } else if all(is_signed_digits) {
        CastKind::Integer
    } else if all(is_number) {
        CastKind::Number
    } else if all(is_calendar_date) {
        CastKind::Date
    } else if all(is_datetime) {
        CastKind::Datetime
    } else {
        CastKind::String
    }
}

/// Whether every spelling satisfies an explicit cast without coercion.
pub fn can_classify_as(values: &[String], kind: CastKind) -> bool {
    if values.is_empty() || kind == CastKind::String {
        return true;
    }
    let inferred = classify_lexemes(values);
    match kind {
        CastKind::Number => matches!(inferred, CastKind::Integer | CastKind::Number),
        CastKind::Datetime => matches!(inferred, CastKind::Date | CastKind::Datetime),
        _ => inferred == kind,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|v| (*v).to_owned()).collect()
    }

    #[test]
    fn lexemes_classify_like_the_python_contract() {
        assert_eq!(
            classify_lexemes(&strings(&["True", "false"])),
            CastKind::Boolean
        );
        assert_eq!(
            classify_lexemes(&strings(&["+1", "-20"])),
            CastKind::Integer
        );
        assert_eq!(
            classify_lexemes(&strings(&["1", ".5", "2.", "1e3"])),
            CastKind::Number
        );
        assert_eq!(classify_lexemes(&strings(&["2024-02-29"])), CastKind::Date);
        assert_eq!(
            classify_lexemes(&strings(&["2023-02-29"])),
            CastKind::String
        );
        assert_eq!(
            classify_lexemes(&strings(&[
                "2024-01-02T03:04",
                "2024-01-02t03:04:05.1+01:30"
            ])),
            CastKind::Datetime
        );
        assert_eq!(
            classify_lexemes(&strings(&["2024-01-02T24:00"])),
            CastKind::String
        );
        assert!(can_classify_as(
            &strings(&["2024-01-02"]),
            CastKind::Datetime
        ));
    }

    fn kind(name: &str) -> CastKind {
        serde_json::from_value(serde_json::json!(name)).unwrap()
    }

    /// `conformance/schema-inference.json`, shared with the TypeScript package.
    #[test]
    fn lexemes_follow_the_shared_inference_corpus() {
        let corpus: Value =
            serde_json::from_str(include_str!("../../../conformance/schema-inference.json"))
                .unwrap();
        let values = |case: &Value| -> Vec<String> {
            serde_json::from_value(case["values"].clone()).unwrap()
        };
        for case in corpus["classification"].as_array().unwrap() {
            let expected = kind(case["kind"].as_str().unwrap());
            assert_eq!(classify_lexemes(&values(case)), expected, "{}", case["id"]);
        }
        for case in corpus["casts"].as_array().unwrap() {
            let cast = kind(case["kind"].as_str().unwrap());
            let valid = case["valid"].as_bool().unwrap();
            assert_eq!(
                can_classify_as(&values(case), cast),
                valid,
                "{}",
                case["id"]
            );
        }
    }

    #[test]
    fn model_names_are_capwords_identifiers() {
        assert_eq!(
            model_name("customer account", "Concept"),
            "CustomerAccountConcept"
        );
        assert_eq!(model_name("ação_rápida", "Schema"), "AçãoRápidaSchema");
        assert_eq!(model_name("3d model", "Concept"), "Concept3dModelConcept");
        assert_eq!(model_name("!!!", "Concept"), "ConceptConcept");
        assert!(unique_model_names(["a b", "a_b"], "Concept").is_err());
    }

    #[test]
    fn casts_parse_and_conflict() {
        let casts = parse_casts(&strings(&["n = Integer", "d=date"])).unwrap();
        assert_eq!(casts["n"], CastKind::Integer);
        assert!(parse_casts(&strings(&["n=integer", "n=number"])).is_err());
        assert!(parse_casts(&strings(&["n"])).is_err());
    }
}
