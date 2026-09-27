//! Offline bundle search (RFC 0016 phase 1): body lines ranked by a
//! deterministic BM25 scorer, or matched literally.
//!
//! Every non-blank body line of a concept is one passage. `lexical` ranks
//! passages with BM25 over Unicode case-folded word tokens (maximal runs of
//! alphanumerics and `_`); `literal` keeps the passages containing the
//! case-folded query. Hits are ordered by score, then path and body line, and
//! `context` widens each selected hit by whole body lines afterwards.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;
use std::str::FromStr;

use ignore::gitignore::{Gitignore, GitignoreBuilder};
use serde::Serialize;

use crate::ConceptRecord;
use crate::specs::split_lines;

const BM25_K1: f64 = 1.2;
const BM25_B: f64 = 0.75;

/// How passages are matched.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// Ranked by the built-in BM25 scorer.
    Lexical,
    /// Case-folded substring matches, all scored 1.
    Literal,
}

impl Mode {
    const fn engine(self) -> &'static str {
        match self {
            Self::Lexical => "builtin_lexical_v1",
            Self::Literal => "literal_v1",
        }
    }
}

impl FromStr for Mode {
    type Err = SearchError;

    fn from_str(mode: &str) -> Result<Self, SearchError> {
        match mode {
            "lexical" => Ok(Self::Lexical),
            "literal" => Ok(Self::Literal),
            "vector" | "hybrid" => Err(SearchError::NotConfigured(mode.to_owned())),
            other => Err(SearchError::UnsupportedMode(other.to_owned())),
        }
    }
}

/// How much of each hit is answered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Detail {
    /// `location<TAB>snippet` rows.
    Compact,
    /// `location<TAB>score<TAB>snippet` rows.
    Score,
    /// Structured results with provenance.
    Full,
}

impl FromStr for Detail {
    type Err = SearchError;

    fn from_str(detail: &str) -> Result<Self, SearchError> {
        match detail {
            "compact" => Ok(Self::Compact),
            "score" => Ok(Self::Score),
            "full" => Ok(Self::Full),
            other => Err(SearchError::UnsupportedDetail(other.to_owned())),
        }
    }
}

/// A search request that violates the RFC 0016 contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SearchError {
    EmptyQuery,
    ZeroLimit,
    /// Only a caller with signed counts can ask for this.
    NegativeContext,
    UnsupportedMode(String),
    UnsupportedDetail(String),
    NotConfigured(String),
    Profile(String),
    EmptyPathGlob,
    NegatedPathGlob,
    InvalidPathGlob(String),
    NonFiniteScore,
}

impl fmt::Display for SearchError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyQuery => f.write_str("query must not be empty"),
            Self::ZeroLimit => f.write_str("limit must be at least 1"),
            Self::NegativeContext => f.write_str("context must be non-negative"),
            Self::UnsupportedMode(mode) => write!(f, "unsupported search mode: {mode}"),
            Self::UnsupportedDetail(detail) => write!(f, "unsupported search detail: {detail}"),
            Self::NotConfigured(mode) => write!(f, "{mode} retrieval is not configured"),
            Self::Profile(profile) => write!(f, "profile is not configured: {profile}"),
            Self::EmptyPathGlob => {
                f.write_str("path_glob must be one non-empty positive path pattern")
            }
            Self::NegatedPathGlob => f.write_str("path_glob does not support negation"),
            Self::InvalidPathGlob(error) => write!(f, "invalid path_glob: {error}"),
            Self::NonFiniteScore => f.write_str("search engine produced a non-finite score"),
        }
    }
}

impl std::error::Error for SearchError {}

/// One search call.
#[derive(Debug, Clone, Copy)]
pub struct SearchRequest<'a> {
    pub query: &'a str,
    pub mode: Mode,
    pub limit: usize,
    pub context: usize,
    pub concept_type: Option<&'a str>,
    pub path_glob: Option<&'a str>,
    pub detail: Detail,
}

impl<'a> SearchRequest<'a> {
    /// A lexical, compact request for the ten best passages.
    pub const fn new(query: &'a str) -> Self {
        Self {
            query,
            mode: Mode::Lexical,
            limit: 10,
            context: 0,
            concept_type: None,
            path_glob: None,
            detail: Detail::Compact,
        }
    }
}

/// One structured hit.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SearchHit {
    pub rank: usize,
    pub score: f64,
    pub concept_id: String,
    pub concept_type: String,
    pub path: String,
    pub location: String,
    pub body_start_line: usize,
    pub body_end_line: usize,
    pub source_digest: String,
    pub text: String,
}

/// Non-semantic details of how a search was answered.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SearchDiagnostics {
    pub engine: &'static str,
}

/// The structured (`full`) answer.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SearchResults {
    pub query: String,
    pub mode: Mode,
    pub profile: Option<String>,
    pub results: Vec<SearchHit>,
    pub diagnostics: SearchDiagnostics,
}

/// A search answer: TSV rows (`compact`, `score`) or structured results.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum SearchOutput {
    Rows(String),
    Full(SearchResults),
}

struct Passage<'a> {
    concept: &'a ConceptRecord,
    body: &'a [&'a str],
    line: usize,
}

impl Passage<'_> {
    fn text(&self) -> &str {
        self.body[self.line - 1]
    }
}

struct Hit<'a> {
    passage: Passage<'a>,
    score: f64,
}

/// Search `concepts` (an already-loaded bundle's) without touching the disk.
pub fn search(
    concepts: &[ConceptRecord],
    request: &SearchRequest<'_>,
) -> Result<SearchOutput, SearchError> {
    let query = request.query.trim();
    if query.is_empty() {
        return Err(SearchError::EmptyQuery);
    }
    if request.limit == 0 {
        return Err(SearchError::ZeroLimit);
    }
    let matcher = request.path_glob.map(path_matcher).transpose()?;
    let bodies: Vec<(&ConceptRecord, Vec<&str>)> = concepts
        .iter()
        .filter(|concept| {
            request
                .concept_type
                .is_none_or(|t| concept.concept_type == t)
        })
        .filter(|concept| {
            matcher.as_ref().is_none_or(|matcher| {
                matcher
                    .matched_path_or_any_parents(Path::new(&concept.path), false)
                    .is_ignore()
            })
        })
        .map(|concept| (concept, split_lines(&concept.body).collect()))
        .collect();
    let passages = bodies.iter().flat_map(|(concept, body)| {
        body.iter()
            .enumerate()
            .filter(|(_, line)| !line.trim().is_empty())
            .map(|(index, _)| Passage {
                concept,
                body,
                line: index + 1,
            })
    });
    let mut hits = match request.mode {
        Mode::Lexical => lexical_hits(passages.collect(), query),
        Mode::Literal => literal_hits(passages, query),
    };
    hits.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.passage.concept.path.cmp(&b.passage.concept.path))
            .then_with(|| a.passage.line.cmp(&b.passage.line))
    });
    hits.truncate(request.limit);
    let context = request.context;
    Ok(match request.detail {
        Detail::Compact => SearchOutput::Rows(rows(&hits, context, false)?),
        Detail::Score => SearchOutput::Rows(rows(&hits, context, true)?),
        Detail::Full => SearchOutput::Full(SearchResults {
            query: query.to_owned(),
            mode: request.mode,
            profile: None,
            results: hits
                .iter()
                .enumerate()
                .map(|(index, hit)| full_hit(index + 1, hit, context))
                .collect(),
            diagnostics: SearchDiagnostics {
                engine: request.mode.engine(),
            },
        }),
    })
}

fn path_matcher(pattern: &str) -> Result<Gitignore, SearchError> {
    let rule = pattern.trim_end();
    if rule.is_empty() || rule.starts_with('#') {
        return Err(SearchError::EmptyPathGlob);
    }
    if rule.starts_with('!') {
        return Err(SearchError::NegatedPathGlob);
    }
    let mut builder = GitignoreBuilder::new("");
    builder
        .add_line(None, pattern)
        .map_err(|error| SearchError::InvalidPathGlob(error.to_string()))?;
    builder
        .build()
        .map_err(|error| SearchError::InvalidPathGlob(error.to_string()))
}

/// Case-folded maximal runs of alphanumerics and `_`.
fn tokens(text: &str) -> Vec<String> {
    caseless::default_case_fold_str(text)
        .split(|c: char| !(c.is_alphanumeric() || c == '_'))
        .filter(|token| !token.is_empty())
        .map(str::to_owned)
        .collect()
}

fn lexical_hits<'a>(passages: Vec<Passage<'a>>, query: &str) -> Vec<Hit<'a>> {
    let mut terms = tokens(query);
    let mut seen = std::collections::HashSet::new();
    terms.retain(|term| seen.insert(term.clone()));
    if terms.is_empty() || passages.is_empty() {
        return Vec::new();
    }
    let tokenized: Vec<Vec<String>> = passages.iter().map(|p| tokens(p.text())).collect();
    #[allow(clippy::cast_precision_loss)] // Passage and token counts are far below 2^52.
    let (count, average) = {
        let count = tokenized.len() as f64;
        let total: usize = tokenized.iter().map(Vec::len).sum();
        (count, total as f64 / count)
    };
    if average == 0.0 {
        return Vec::new();
    }
    #[allow(clippy::cast_precision_loss)] // As above.
    let idf: Vec<(&String, f64)> = terms
        .iter()
        .filter_map(|term| {
            let frequency = tokenized.iter().filter(|t| t.contains(term)).count() as f64;
            (frequency > 0.0).then(|| {
                let ratio = (count - frequency + 0.5) / (frequency + 0.5);
                (term, (1.0 + ratio).ln())
            })
        })
        .collect();
    passages
        .into_iter()
        .zip(&tokenized)
        .filter_map(|(passage, tokens)| {
            let mut frequencies: HashMap<&str, usize> = HashMap::new();
            for token in tokens {
                *frequencies.entry(token).or_default() += 1;
            }
            #[allow(clippy::cast_precision_loss)] // As above.
            let normalizer = BM25_K1 * (1.0 - BM25_B + BM25_B * tokens.len() as f64 / average);
            let score = idf.iter().fold(0.0, |score, (term, idf)| {
                match frequencies.get(term.as_str()) {
                    #[allow(clippy::cast_precision_loss)] // As above.
                    Some(&frequency) => {
                        let frequency = frequency as f64;
                        score + idf * (frequency * (BM25_K1 + 1.0) / (frequency + normalizer))
                    }
                    None => score,
                }
            });
            (score > 0.0).then_some(Hit { passage, score })
        })
        .collect()
}

fn literal_hits<'a>(passages: impl Iterator<Item = Passage<'a>>, query: &str) -> Vec<Hit<'a>> {
    let needle = caseless::default_case_fold_str(query);
    passages
        .filter(|passage| caseless::default_case_fold_str(passage.text()).contains(&needle))
        .map(|passage| Hit {
            passage,
            score: 1.0,
        })
        .collect()
}

/// The hit's lines widened by `context`, bounded by its body.
fn expanded<'a>(passage: &Passage<'a>, context: usize) -> (usize, usize, &'a [&'a str]) {
    let start = passage.line.saturating_sub(context).max(1);
    let end = (passage.line + context).min(passage.body.len());
    (start, end, &passage.body[start - 1..end])
}

fn location(path: &str, start: usize, end: usize) -> String {
    let mut escaped = String::with_capacity(path.len());
    for c in path.chars() {
        match c {
            '%' => escaped.push_str("%25"),
            '#' => escaped.push_str("%23"),
            '\u{0}'..='\u{1f}' | '\u{7f}' => escaped.push_str(&format!("%{:02X}", u32::from(c))),
            c => escaped.push(c),
        }
    }
    if start == end {
        format!("{escaped}#B{start}")
    } else {
        format!("{escaped}#B{start}-B{end}")
    }
}

fn rows(hits: &[Hit<'_>], context: usize, with_score: bool) -> Result<String, SearchError> {
    let mut out = String::from(if with_score {
        "location\tscore\tsnippet"
    } else {
        "location\tsnippet"
    });
    for hit in hits {
        let (start, end, lines) = expanded(&hit.passage, context);
        let snippet = lines
            .iter()
            .map(|line| line.replace('\t', " "))
            .collect::<Vec<_>>()
            .join(" ");
        out.push('\n');
        out.push_str(&location(&hit.passage.concept.path, start, end));
        if with_score {
            out.push('\t');
            out.push_str(&score_text(hit.score)?);
        }
        out.push('\t');
        out.push_str(&snippet);
    }
    Ok(out)
}

fn full_hit(rank: usize, hit: &Hit<'_>, context: usize) -> SearchHit {
    let (start, end, lines) = expanded(&hit.passage, context);
    let concept = hit.passage.concept;
    SearchHit {
        rank,
        score: hit.score,
        concept_id: concept.concept_id.clone(),
        concept_type: concept.concept_type.clone(),
        path: concept.path.clone(),
        location: location(&concept.path, start, end),
        body_start_line: start,
        body_end_line: end,
        source_digest: concept.source_digest.clone(),
        text: lines.join("\n"),
    }
}

/// `score` with 12 significant digits, as C's `%.12g` spells it.
fn score_text(score: f64) -> Result<String, SearchError> {
    if !score.is_finite() {
        return Err(SearchError::NonFiniteScore);
    }
    if score == 0.0 {
        return Ok("0".to_owned());
    }
    let scientific = format!("{score:.11e}");
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    if (-4..12).contains(&exponent) {
        let decimals = usize::try_from(11 - exponent).unwrap_or(0);
        Ok(trim_zeros(&format!("{score:.decimals$}")).to_owned())
    } else {
        let sign = if exponent < 0 { '-' } else { '+' };
        Ok(format!(
            "{}e{sign}{:02}",
            trim_zeros(mantissa),
            exponent.unsigned_abs()
        ))
    }
}

fn trim_zeros(number: &str) -> &str {
    if number.contains('.') {
        number.trim_end_matches('0').trim_end_matches('.')
    } else {
        number
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn concept(path: &str, concept_type: &str, body: &str) -> ConceptRecord {
        ConceptRecord {
            concept_id: path.trim_end_matches(".md").to_owned(),
            logical_key: path.to_owned(),
            path: path.to_owned(),
            concept_type: concept_type.to_owned(),
            title: None,
            description: None,
            source_digest: "sha256:0".to_owned(),
            parsed_digest: "sha256:0".to_owned(),
            frontmatter_json: "{}".to_owned(),
            body: body.to_owned(),
        }
    }

    fn rows_of(output: SearchOutput) -> String {
        match output {
            SearchOutput::Rows(rows) => rows,
            SearchOutput::Full(_) => panic!("expected rows"),
        }
    }

    #[test]
    fn literal_matches_case_fold_and_keep_body_coordinates() {
        let concepts = [concept(
            "legal.md",
            "Tese",
            "# Título\nA Straße está aberta.\n",
        )];
        let request = SearchRequest {
            mode: Mode::Literal,
            ..SearchRequest::new("STRASSE")
        };
        assert_eq!(
            rows_of(search(&concepts, &request).unwrap()),
            "location\tsnippet\nlegal.md#B2\tA Straße está aberta."
        );
    }

    #[test]
    fn lexical_ranking_prefers_the_denser_passage() {
        let concepts = [
            concept("b.md", "Tese", "alpha beta gamma delta"),
            concept("a.md", "Tese", "alpha alpha beta"),
            concept("c.md", "Tese", "unrelated"),
        ];
        let request = SearchRequest {
            detail: Detail::Full,
            ..SearchRequest::new("alpha beta")
        };
        let SearchOutput::Full(results) = search(&concepts, &request).unwrap() else {
            panic!("expected full results");
        };
        let paths: Vec<&str> = results
            .results
            .iter()
            .map(|hit| hit.path.as_str())
            .collect();
        assert_eq!(paths, ["a.md", "b.md"]);
        assert!(results.results[0].score > results.results[1].score);
        assert_eq!(results.diagnostics.engine, "builtin_lexical_v1");
    }

    #[test]
    fn limit_applies_before_context_and_paths_are_escaped() {
        let concepts = [
            concept("a%#.md", "Tese", "before\nneedle\nafter"),
            concept("b.md", "Tese", "needle"),
        ];
        let request = SearchRequest {
            mode: Mode::Literal,
            limit: 1,
            context: 1,
            ..SearchRequest::new("needle")
        };
        assert_eq!(
            rows_of(search(&concepts, &request).unwrap()),
            "location\tsnippet\na%25%23.md#B1-B3\tbefore needle after"
        );
    }

    #[test]
    fn path_glob_matches_like_an_exclusion_rule() {
        let concepts = [
            concept("a/deep/one.md", "Tese", "needle one"),
            concept("b/two.md", "Tese", "needle two"),
        ];
        let request = SearchRequest {
            mode: Mode::Literal,
            path_glob: Some("a/**/*.md"),
            ..SearchRequest::new("needle")
        };
        assert_eq!(
            rows_of(search(&concepts, &request).unwrap()),
            "location\tsnippet\na/deep/one.md#B1\tneedle one"
        );
        for (glob, error) in [
            ("!a.md", SearchError::NegatedPathGlob),
            ("# comment", SearchError::EmptyPathGlob),
        ] {
            let request = SearchRequest {
                path_glob: Some(glob),
                ..SearchRequest::new("needle")
            };
            assert_eq!(search(&concepts, &request).unwrap_err(), error);
        }
    }

    #[test]
    fn modes_and_details_parse_with_contract_messages() {
        assert_eq!(
            "vector".parse::<Mode>().unwrap_err().to_string(),
            "vector retrieval is not configured"
        );
        assert_eq!(
            "regex".parse::<Mode>().unwrap_err().to_string(),
            "unsupported search mode: regex"
        );
        assert_eq!(
            "verbose".parse::<Detail>().unwrap_err().to_string(),
            "unsupported search detail: verbose"
        );
    }

    #[test]
    fn scores_print_with_twelve_significant_digits() {
        assert_eq!(score_text(1.0).unwrap(), "1");
        assert_eq!(score_text(0.123_456_789_012_345).unwrap(), "0.123456789012");
        assert_eq!(score_text(1.5e-7).unwrap(), "1.5e-07");
        assert_eq!(score_text(2.0e13).unwrap(), "2e+13");
        assert_eq!(score_text(f64::NAN), Err(SearchError::NonFiniteScore));
    }
}
