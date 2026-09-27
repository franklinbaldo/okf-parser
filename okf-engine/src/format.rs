//! The canonical Markdown form: a surgical formatter.
//!
//! The document is parsed with pulldown-cmark and only syntax tokens are
//! rewritten, at their source offsets: list markers and numbering, `_`
//! emphasis, setext headings, table rows, hard breaks, blank lines, trailing
//! whitespace and the final newline, plus the physical order of simple
//! frontmatter. Every other byte (text, code, HTML, links) is kept verbatim.
//!
//! The rewrite must parse to the same event stream as the original. If the
//! full set of rewrites would not, only the core ones (markers, numbering,
//! whitespace) are tried; if even those would not, the document is refused
//! rather than changed.

use std::fmt;
use std::fs;
use std::io;
use std::ops::Range;
use std::path::{Path, PathBuf};

use pulldown_cmark::{Alignment, Event, Options, Parser, Tag, TagEnd};
use serde::Serialize;

use crate::LoadError;
use crate::engine::{discover, normalized_newlines, split_source};

/// The Markdown dialect the canonical form is defined over.
const DIALECT: Options = Options::ENABLE_TABLES
    .union(Options::ENABLE_STRIKETHROUGH)
    .union(Options::ENABLE_TASKLISTS);

/// CommonMark allows at most nine digits in an ordered-list marker.
const MAX_MARKER: u64 = 999_999_999;

/// Frontmatter keys that lead the physical order, in this order.
const LEADING_KEYS: [&str; 3] = ["type", "title", "description"];

/// Why a document was left as it is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FormatError {
    /// Every rewrite, even the core one, would change what the document means.
    ChangesStructure,
}

impl fmt::Display for FormatError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ChangesStructure => {
                f.write_str("formatting would change the document's structure")
            }
        }
    }
}

impl std::error::Error for FormatError {}

/// What formatting a tree found, and did.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FormatReport {
    pub markdown_count: usize,
    /// Documents not in canonical form (rewritten, with `written`).
    pub changed_paths: Vec<String>,
    /// Documents left alone: unreadable, not UTF-8, or refused.
    pub skipped: Vec<Skipped>,
    pub written: bool,
}

/// A document formatting left alone, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Skipped {
    pub path: String,
    pub reason: String,
}

impl FormatReport {
    /// Every document read and already canonical.
    pub fn clean(&self) -> bool {
        self.changed_paths.is_empty() && self.skipped.is_empty()
    }

    /// No formatting work left behind: a write never resolves skipped files.
    pub fn succeeded(&self) -> bool {
        self.skipped.is_empty() && (self.written || self.changed_paths.is_empty())
    }
}

/// Why a tree could not be formatted at all.
#[derive(Debug)]
pub enum FormatTreeError {
    Load(LoadError),
    /// Writing failed; every file already replaced was restored.
    Write(io::Error),
}

impl fmt::Display for FormatTreeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Load(error) => error.fmt(f),
            Self::Write(error) => write!(f, "could not write the formatted files: {error}"),
        }
    }
}

impl std::error::Error for FormatTreeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Load(error) => Some(error),
            Self::Write(error) => Some(error),
        }
    }
}

/// Check, or with `write` rewrite, every Markdown file below `root` that the
/// exclusion rules leave in. Every file is formatted before any is written,
/// and the writes replace all of them or none.
pub fn format_tree(
    root: &Path,
    exclude: &[String],
    write: bool,
) -> Result<FormatReport, FormatTreeError> {
    let root = fs::canonicalize(root).map_err(|source| {
        FormatTreeError::Load(LoadError::Root {
            path: root.to_path_buf(),
            source,
        })
    })?;
    let paths = discover(&root, exclude).map_err(FormatTreeError::Load)?;
    let mut changed_paths = Vec::new();
    let mut skipped = Vec::new();
    let mut replacements: Vec<(PathBuf, Vec<u8>)> = Vec::new();
    for path in &paths {
        let relative = path
            .strip_prefix(&root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        let text = match fs::read(path).map(String::from_utf8) {
            Ok(Ok(text)) => text,
            Ok(Err(_)) => {
                skipped.push(Skipped {
                    path: relative,
                    reason: "not UTF-8".to_owned(),
                });
                continue;
            }
            Err(error) => {
                skipped.push(Skipped {
                    path: relative,
                    reason: error.to_string(),
                });
                continue;
            }
        };
        match format_markdown(&text) {
            Ok(formatted) if formatted != text => {
                changed_paths.push(relative);
                replacements.push((path.clone(), formatted.into_bytes()));
            }
            Ok(_) => {}
            Err(error) => skipped.push(Skipped {
                path: relative,
                reason: error.to_string(),
            }),
        }
    }
    if write && !replacements.is_empty() {
        crate::write::commit_all(&replacements, |from, to| fs::rename(from, to))
            .map_err(FormatTreeError::Write)?;
    }
    Ok(FormatReport {
        markdown_count: paths.len(),
        changed_paths,
        skipped,
        written: write,
    })
}

/// The canonical form of one Markdown document.
pub fn format_markdown(text: &str) -> Result<String, FormatError> {
    let text = normalized_newlines(text);
    let (frontmatter, body) = match split_source(&text) {
        Some((block, body)) if text.starts_with("---") => (Some(block), body),
        _ => (None, text.as_ref()),
    };
    let body = format_body(body)?;
    let Some(block) = frontmatter else {
        return Ok(body);
    };
    let block = ordered_frontmatter(block);
    let mut out = String::from("---\n");
    if !block.is_empty() {
        out.push_str(&block);
        out.push('\n');
    }
    out.push_str("---\n");
    if !body.is_empty() {
        out.push('\n');
        out.push_str(&body);
    }
    Ok(out)
}

/// Which rewrites to attempt.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Tier {
    /// Markers, numbering, hard breaks and whitespace.
    Core,
    /// Core, plus emphasis, setext headings, tables and block spacing.
    Full,
}

fn format_body(body: &str) -> Result<String, FormatError> {
    let original = events(body);
    for tier in [Tier::Full, Tier::Core] {
        let candidate = tidy_lines(&apply(body, &edits(body, tier)));
        if events(&candidate) == original {
            return Ok(candidate);
        }
    }
    Err(FormatError::ChangesStructure)
}

fn events(text: &str) -> Vec<Event<'_>> {
    Parser::new_ext(text, DIALECT).collect()
}

/// One replacement of `range` in the source.
struct Edit {
    range: Range<usize>,
    text: String,
}

fn apply(source: &str, edits: &[Edit]) -> String {
    let mut sorted: Vec<&Edit> = edits.iter().collect();
    sorted.sort_by_key(|edit| (edit.range.start, edit.range.end));
    let mut out = String::with_capacity(source.len() + 16);
    let mut cursor = 0;
    for edit in sorted {
        if edit.range.start < cursor {
            // Overlapping rewrites of one token: the first one wins.
            continue;
        }
        out.push_str(&source[cursor..edit.range.start]);
        out.push_str(&edit.text);
        cursor = edit.range.end;
    }
    out.push_str(&source[cursor..]);
    out
}

/// What a list uses as its marker, so an adjacent sibling list can differ.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Marker {
    Bullet(char),
    Ordered(char),
}

struct ListState {
    marker: Marker,
    start: u64,
    items: u64,
}

/// Walks the parsed document and records the rewrites of one tier.
struct Collector<'a> {
    source: &'a str,
    tier: Tier,
    edits: Vec<Edit>,
    lists: Vec<ListState>,
    /// Per container depth: the marker of the list that just closed there, if
    /// the last block at that depth was a list.
    previous: Vec<Option<Marker>>,
    /// Container depth: the document, block quotes and list items.
    depth: usize,
    /// Where the last top-level block ended, for blank-line spacing.
    top_level_end: Option<usize>,
}

fn edits(source: &str, tier: Tier) -> Vec<Edit> {
    let mut collector = Collector {
        source,
        tier,
        edits: Vec::new(),
        lists: Vec::new(),
        previous: vec![None],
        depth: 0,
        top_level_end: None,
    };
    let mut table: Option<(Vec<Alignment>, Vec<Range<usize>>)> = None;
    for (event, range) in Parser::new_ext(source, DIALECT).into_offset_iter() {
        match event {
            Event::Start(tag) => {
                if is_block(&tag) {
                    collector.block_start(&tag, &range);
                }
                match tag {
                    Tag::List(start) => collector.list_start(start),
                    Tag::Item => collector.item_start(&range),
                    Tag::BlockQuote(_) => collector.enter(),
                    Tag::Emphasis => collector.emphasis(&range, 1),
                    Tag::Strong => collector.emphasis(&range, 2),
                    Tag::Heading { level, .. } => collector.heading(&range, level as usize),
                    Tag::Table(alignments) => table = Some((alignments, Vec::new())),
                    Tag::TableCell => {
                        if let Some((_, cells)) = table.as_mut() {
                            cells.push(range.clone());
                        }
                    }
                    _ => {}
                }
            }
            Event::End(end) => {
                match end {
                    TagEnd::List(_) => collector.list_end(),
                    TagEnd::Item | TagEnd::BlockQuote(_) => collector.leave(),
                    TagEnd::TableHead | TagEnd::TableRow => {
                        if let Some((alignments, cells)) = table.as_mut() {
                            let head = matches!(end, TagEnd::TableHead);
                            collector.table_row(&range, std::mem::take(cells), head, alignments);
                        }
                    }
                    TagEnd::Table => table = None,
                    _ => {}
                }
                if is_block_end(end) && collector.depth == 0 {
                    collector.top_level_end = Some(range.end);
                }
            }
            Event::HardBreak => collector.hard_break(&range),
            Event::Rule => {
                collector.block_start(&Tag::Paragraph, &range);
                if collector.depth == 0 {
                    collector.top_level_end = Some(range.end);
                }
            }
            _ => {}
        }
    }
    collector.edits
}

fn is_block(tag: &Tag<'_>) -> bool {
    matches!(
        tag,
        Tag::Paragraph
            | Tag::Heading { .. }
            | Tag::BlockQuote(_)
            | Tag::CodeBlock(_)
            | Tag::HtmlBlock
            | Tag::List(_)
            | Tag::Table(_)
            | Tag::FootnoteDefinition(_)
    )
}

fn is_block_end(end: TagEnd) -> bool {
    matches!(
        end,
        TagEnd::Paragraph
            | TagEnd::Heading(_)
            | TagEnd::BlockQuote(_)
            | TagEnd::CodeBlock
            | TagEnd::HtmlBlock
            | TagEnd::List(_)
            | TagEnd::Table
            | TagEnd::FootnoteDefinition
    )
}

/// The first non-space byte at or after `at`: pulldown-cmark block ranges
/// start at the line's indentation, not at the token.
fn token_start(source: &str, at: usize) -> usize {
    at + source[at..].len() - source[at..].trim_start_matches(' ').len()
}

fn line_start(source: &str, at: usize) -> usize {
    source[..at].rfind('\n').map_or(0, |newline| newline + 1)
}

fn line_end(source: &str, at: usize) -> usize {
    source[at..]
        .find('\n')
        .map_or(source.len(), |newline| at + newline)
}

impl Collector<'_> {
    fn push(&mut self, range: Range<usize>, text: impl Into<String>) {
        self.edits.push(Edit {
            range,
            text: text.into(),
        });
    }

    fn enter(&mut self) {
        self.depth += 1;
        self.previous.push(None);
    }

    fn leave(&mut self) {
        self.depth -= 1;
        self.previous.pop();
    }

    /// A block begins: forget any adjacent list and, at the top level, make
    /// sure a blank line separates it from the block before.
    fn block_start(&mut self, tag: &Tag<'_>, range: &Range<usize>) {
        if !matches!(tag, Tag::List(_)) {
            self.previous[self.depth] = None;
        }
        if self.tier == Tier::Full && self.depth == 0 {
            let start = line_start(self.source, range.start);
            if self.top_level_end.is_some() && start > 0 {
                let before = line_start(self.source, start - 1);
                if !self.source[before..start - 1].trim().is_empty() {
                    self.push(start..start, "\n");
                }
            }
        }
    }

    fn list_start(&mut self, start: Option<u64>) {
        let adjacent = self.previous[self.depth];
        let marker = match start {
            None => Marker::Bullet(if adjacent == Some(Marker::Bullet('-')) {
                '*'
            } else {
                '-'
            }),
            Some(_) => Marker::Ordered(if adjacent == Some(Marker::Ordered('.')) {
                ')'
            } else {
                '.'
            }),
        };
        self.lists.push(ListState {
            marker,
            start: start.unwrap_or(1),
            items: 0,
        });
    }

    fn list_end(&mut self) {
        if let Some(list) = self.lists.pop() {
            self.previous[self.depth] = Some(list.marker);
        }
    }

    fn item_start(&mut self, range: &Range<usize>) {
        let source = self.source;
        let Some(list) = self.lists.last_mut() else {
            return;
        };
        let index = list.items;
        list.items += 1;
        let marker = list.marker;
        let start = list.start;
        let item = token_start(source, range.start)..range.end;
        let (old_marker_end, new) = match marker {
            Marker::Bullet(bullet) => (item.start + 1, bullet.to_string()),
            Marker::Ordered(delimiter) => {
                let digits = source[item.start..]
                    .bytes()
                    .take_while(u8::is_ascii_digit)
                    .count();
                // Past the marker limit the item would stop being a list item.
                let new = match start + index {
                    number if number <= MAX_MARKER => format!("{number}{delimiter}"),
                    _ => format!("{}{delimiter}", &source[item.start..item.start + digits]),
                };
                (item.start + digits + 1, new)
            }
        };
        self.remark(&item, old_marker_end, &new);
        self.enter();
    }

    /// Rewrite an item's marker as `new` followed by one space, and move the
    /// item's continuation lines by however much its content column moved.
    fn remark(&mut self, range: &Range<usize>, old_marker_end: usize, new: &str) {
        let source = self.source;
        let rest = &source[old_marker_end..line_end(source, old_marker_end)];
        let spaces = rest.len() - rest.trim_start_matches(' ').len();
        let old_marker_width = old_marker_end - range.start;
        // One to four spaces before content set the content column; an empty
        // first line, a tab or five spaces (indented code) put it one past
        // the marker.
        let aligned = (1..=4).contains(&spaces)
            && !rest[spaces..].is_empty()
            && !rest[spaces..].starts_with('\t');
        let (old_end, old_width, replacement) = if aligned {
            (
                old_marker_end + spaces,
                old_marker_width + spaces,
                format!("{new} "),
            )
        } else {
            (old_marker_end, old_marker_width + 1, new.to_owned())
        };
        let column = range.start - line_start(source, range.start);
        // The item's continuation lines, with the spaces each could give up.
        let mut lines = Vec::new();
        let mut cursor = line_end(source, range.start) + 1;
        while cursor < range.end {
            let end = line_end(source, cursor);
            let line = &source[cursor..end];
            let in_container =
                line.len() > column && line[..column].chars().all(|c| c == ' ' || c == '>');
            if !line.trim().is_empty() && in_container {
                let indent = &line[column..];
                lines.push((
                    cursor + column,
                    indent.len() - indent.trim_start_matches(' ').len(),
                ));
            }
            cursor = end + 1;
        }
        let new_width = new.len() + 1;
        let cut = old_width.saturating_sub(new_width);
        if lines.iter().any(|&(_, spaces)| spaces < cut) {
            // The body cannot move left that far: keep its column instead.
            if aligned {
                let padded = format!("{new}{}", " ".repeat(old_width - new.len()));
                if source[range.start..old_end] != padded {
                    self.push(range.start..old_end, padded);
                }
            }
            return;
        }
        if source[range.start..old_end] == replacement {
            return;
        }
        self.push(range.start..old_end, replacement);
        for (at, _) in lines {
            if new_width > old_width {
                self.push(at..at, " ".repeat(new_width - old_width));
            } else if cut > 0 {
                self.push(at..at + cut, "");
            }
        }
    }

    fn emphasis(&mut self, range: &Range<usize>, width: usize) {
        if self.tier != Tier::Full {
            return;
        }
        let source = self.source.as_bytes();
        if range.end - range.start < 2 * width + 1 {
            return;
        }
        let opening = &source[range.start..range.start + width];
        let closing = &source[range.end - width..range.end];
        if opening.iter().all(|&b| b == b'_') && closing.iter().all(|&b| b == b'_') {
            let stars = "*".repeat(width);
            self.push(range.start..range.start + width, stars.clone());
            self.push(range.end - width..range.end, stars);
        }
    }

    fn heading(&mut self, range: &Range<usize>, level: usize) {
        if self.tier != Tier::Full {
            return;
        }
        let start = token_start(self.source, range.start);
        let text = &self.source[start..range.end];
        if text.starts_with('#') {
            return;
        }
        let mut lines = text.lines();
        let (Some(content), Some(_underline), None) = (lines.next(), lines.next(), lines.next())
        else {
            return;
        };
        let end = start + text.trim_end_matches('\n').len();
        self.push(
            start..end,
            format!("{} {}", "#".repeat(level), content.trim()),
        );
    }

    fn table_row(
        &mut self,
        range: &Range<usize>,
        cells: Vec<Range<usize>>,
        head: bool,
        alignments: &[Alignment],
    ) {
        if self.tier != Tier::Full || cells.is_empty() {
            return;
        }
        let source = self.source;
        let cells: Vec<&str> = cells
            .iter()
            .map(|cell| source[cell.clone()].trim())
            .collect();
        let row = format!("| {} |", cells.join(" | "));
        let start = token_start(source, range.start);
        let end = line_end(source, start);
        self.push(start..end, row);
        if head {
            let delimiter_start = end + 1;
            if delimiter_start >= source.len() {
                return;
            }
            let delimiter_end = line_end(source, delimiter_start);
            let line = &source[delimiter_start..delimiter_end];
            let column = start - line_start(source, start);
            let prefix = column.min(line.find(['|', '-', ':']).unwrap_or(line.len()));
            let cells: Vec<&str> = alignments
                .iter()
                .map(|alignment| match alignment {
                    Alignment::None => "---",
                    Alignment::Left => ":--",
                    Alignment::Right => "--:",
                    Alignment::Center => ":-:",
                })
                .collect();
            self.push(
                delimiter_start + prefix..delimiter_end,
                format!("| {} |", cells.join(" | ")),
            );
        }
    }

    fn hard_break(&mut self, range: &Range<usize>) {
        let text = &self.source[range.clone()];
        if text.starts_with(' ') {
            let spaces = text.len() - text.trim_start_matches(' ').len();
            self.push(range.start..range.start + spaces, "\\");
        }
    }
}

/// Strip trailing whitespace, collapse blank-line runs and end with exactly
/// one newline, leaving code and HTML blocks untouched.
fn tidy_lines(text: &str) -> String {
    let mut protected: Vec<Range<usize>> = Vec::new();
    for (event, range) in Parser::new_ext(text, DIALECT).into_offset_iter() {
        if matches!(
            event,
            Event::Start(Tag::CodeBlock(_) | Tag::HtmlBlock)
                | Event::Html(_)
                | Event::InlineHtml(_)
                | Event::Code(_)
        ) {
            protected.push(range);
        }
    }
    let mut out = String::with_capacity(text.len());
    let mut blank_run = true; // Drops leading blank lines.
    let mut cursor = 0;
    for line in text.split_inclusive('\n') {
        let span = cursor..cursor + line.len();
        cursor = span.end;
        let content = line.strip_suffix('\n').unwrap_or(line);
        if protected
            .iter()
            .any(|range| range.start < span.end && span.start < range.end)
        {
            out.push_str(content);
            out.push('\n');
            blank_run = false;
            continue;
        }
        let trimmed = content.trim_end_matches([' ', '\t']);
        if trimmed.is_empty() {
            if !blank_run {
                out.push('\n');
            }
            blank_run = true;
        } else {
            out.push_str(trimmed);
            out.push('\n');
            blank_run = false;
        }
    }
    while out.ends_with("\n\n") {
        out.pop();
    }
    if out.trim().is_empty() {
        out.clear();
    }
    out
}

/// Reorder a flat, obviously simple frontmatter block (`type`, `title`,
/// `description`, then the rest by name); any other block is kept as is.
fn ordered_frontmatter(block: &str) -> String {
    let mut rows: Vec<(&str, &str)> = Vec::new();
    for line in block.split('\n') {
        let Some(key) = simple_key(line) else {
            return block.to_owned();
        };
        if rows.iter().any(|(seen, _)| *seen == key) {
            return block.to_owned();
        }
        rows.push((key, line));
    }
    rows.sort_by(|(a, _), (b, _)| frontmatter_order(a).cmp(&frontmatter_order(b)));
    rows.iter()
        .map(|(_, line)| *line)
        .collect::<Vec<_>>()
        .join("\n")
}

fn frontmatter_order(key: &str) -> (usize, &str) {
    LEADING_KEYS
        .iter()
        .position(|leading| *leading == key)
        .map_or((LEADING_KEYS.len(), key), |index| (index, ""))
}

/// The key of a `key: plain value` line whose YAML meaning is obvious.
fn simple_key(line: &str) -> Option<&str> {
    let (key, remainder) = line.split_once(':')?;
    let mut chars = key.chars();
    let first = chars.next()?;
    if !(first.is_ascii_alphabetic() || first == '_')
        || !chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
    {
        return None;
    }
    let value = match remainder {
        "" => "",
        _ if remainder.starts_with("  ") || !remainder.starts_with(' ') => return None,
        _ => &remainder[1..],
    };
    let unsafe_start = value
        .chars()
        .next()
        .is_some_and(|c| "-?:,[]{}#&*!|>'\"%@`".contains(c));
    if value != value.trim() || value.contains(['\t', '#']) || value.contains(": ") || unsafe_start
    {
        return None;
    }
    Some(key)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn format(text: &str) -> String {
        let formatted = format_markdown(text).unwrap();
        assert_eq!(
            format_markdown(&formatted).unwrap(),
            formatted,
            "not idempotent"
        );
        formatted
    }

    #[test]
    fn bullets_become_dashes_and_adjacent_lists_stay_distinct() {
        assert_eq!(
            format("# H\n\n*   item\n+ other\n"),
            "# H\n\n- item\n\n* other\n"
        );
        assert_eq!(format("- a\n\n* b\n"), "- a\n\n* b\n");
    }

    #[test]
    fn ordered_lists_are_numbered_consecutively_from_their_start() {
        assert_eq!(
            format("1. first\n1. second\n1. third\n"),
            "1. first\n2. second\n3. third\n"
        );
        assert_eq!(format("3) a\n3) b\n"), "3. a\n4. b\n");
        let ten: String = (1..=10).map(|n| format!("{n}. item{n}\n")).collect();
        assert_eq!(format(&ten), ten);
    }

    #[test]
    fn a_wider_marker_shifts_the_item_body() {
        let source: String = (0..10).map(|_| "1. item\n\n   more\n\n").collect();
        let formatted = format(&source);
        assert!(formatted.contains("10. item\n\n    more\n"), "{formatted}");
    }

    #[test]
    fn emphasis_headings_and_hard_breaks_take_one_spelling() {
        assert_eq!(
            format("Title\n=====\n\n_a_ and __b__ end  \nnext\n"),
            "# Title\n\n*a* and **b** end\\\nnext\n"
        );
    }

    #[test]
    fn tables_are_compact_and_keep_alignment() {
        assert_eq!(
            format("|  a  |b|\n|:---|---:|\n| 1 |  2   |\n"),
            "| a | b |\n| :-- | --: |\n| 1 | 2 |\n"
        );
    }

    #[test]
    fn code_html_and_text_are_verbatim() {
        let source = "Text with x86_64 and #25 and [[wiki]]\n\n```text\n   ┌──┐   \n   └──┘\n```\n\n<div>\n  raw   \n</div>\n";
        assert_eq!(format(source), source);
    }

    #[test]
    fn whitespace_and_blank_lines_are_normalized() {
        assert_eq!(
            format("\n\n# H\ntext   \n\n\n\nmore"),
            "# H\n\ntext\n\nmore\n"
        );
    }

    #[test]
    fn frontmatter_is_ordered_and_separated() {
        assert_eq!(
            format("---\nb: 2\ntype: Note\ntitle: T\n---\n# H\n"),
            "---\ntype: Note\ntitle: T\nb: 2\n---\n\n# H\n"
        );
        let complex = "---\ntype: Note\ntags: [a, b]\ncount: 0012\n---\n\n# H\n";
        assert_eq!(format(complex), complex);
    }

    #[test]
    fn a_tree_is_checked_then_written_and_unreadable_files_are_skipped() {
        let root = std::env::temp_dir().join(format!("okf-format-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("a.md"), "*   item\n").unwrap();
        std::fs::write(root.join("b.md"), b"# Caf\xe9\n").unwrap();

        let checked = format_tree(&root, &[], false).unwrap();
        let written = format_tree(&root, &[], true).unwrap();
        let text = std::fs::read_to_string(root.join("a.md")).unwrap();
        std::fs::remove_dir_all(&root).unwrap();

        assert_eq!(checked.changed_paths, ["a.md"]);
        assert!(!checked.succeeded() && !checked.clean());
        assert_eq!(checked.skipped[0].path, "b.md");
        assert_eq!(text, "- item\n");
        assert!(
            written.written && !written.succeeded(),
            "b.md stays skipped"
        );
    }
}
