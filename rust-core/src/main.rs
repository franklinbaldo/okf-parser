use std::io::{self, Read};

use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct Request {
    documents: Vec<String>,
}

#[derive(Serialize)]
struct Facts {
    links: Vec<String>,
    headings: Vec<(u8, String)>,
}

fn heading_level(level: HeadingLevel) -> u8 {
    match level {
        HeadingLevel::H1 => 1,
        HeadingLevel::H2 => 2,
        HeadingLevel::H3 => 3,
        HeadingLevel::H4 => 4,
        HeadingLevel::H5 => 5,
        HeadingLevel::H6 => 6,
    }
}

fn markdown_facts(source: &str) -> Facts {
    let mut links = Vec::new();
    let mut headings = Vec::new();
    let mut heading: Option<(u8, Option<usize>, usize)> = None;

    for (event, range) in Parser::new(source).into_offset_iter() {
        match &event {
            Event::Start(Tag::Link { dest_url, .. }) => {
                links.push(dest_url.clone().into_string());
            }
            Event::Start(Tag::Heading { level, .. }) => {
                heading = Some((heading_level(*level), None, 0));
            }
            Event::End(TagEnd::Heading(_)) => {
                if let Some((level, start, end)) = heading.take() {
                    headings.push((
                        level,
                        start.map_or_else(String::new, |start| source[start..end].into()),
                    ));
                }
            }
            _ if heading.is_some() => {
                if let Some((_, start, end)) = heading.as_mut() {
                    *start = Some(start.map_or(range.start, |value| value.min(range.start)));
                    *end = (*end).max(range.end);
                }
            }
            _ => {}
        }
    }

    Facts { links, headings }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let request: Request = serde_json::from_str(&input)?;
    let facts: Vec<Facts> = request
        .documents
        .iter()
        .map(|source| markdown_facts(source))
        .collect();
    serde_json::to_writer(io::stdout().lock(), &facts)?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("okf-core: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::markdown_facts;

    #[test]
    fn extracts_links_and_headings_in_source_order() {
        let facts = markdown_facts("# One `code`\n\n[A](a.md) ![image](ignored.png)\n## Two\n");
        assert_eq!(facts.links, ["a.md"]);
        assert_eq!(
            facts.headings,
            [(1, "One `code`".into()), (2, "Two".into())]
        );
    }

    #[test]
    fn preserves_authored_heading_markup_like_markdown_it() {
        let facts = markdown_facts("# One *em*\n\n[Link](a.md)\n===\n");
        assert_eq!(
            facts.headings,
            [(1, "One *em*".into()), (1, "[Link](a.md)".into()),]
        );
    }
}
