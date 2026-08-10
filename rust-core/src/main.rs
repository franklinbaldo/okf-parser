mod database;
mod engine;
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use std::io::{self, Read};
use std::path::PathBuf;
#[derive(Parser)]
#[command(name = "okf", version, about = "Native OKF engine")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}
#[derive(Subcommand)]
enum Command {
    Load {
        root: PathBuf,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
        #[arg(long, default_value_t = 32)]
        read_concurrency: usize,
    },
    Duckdb {
        root: PathBuf,
        database: PathBuf,
        #[arg(long, default_value = "okf")]
        schema: String,
        #[arg(long)]
        overwrite: bool,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
        #[arg(long, default_value_t = 32)]
        read_concurrency: usize,
    },
}
#[derive(Deserialize)]
struct Legacy {
    documents: Vec<String>,
}
#[derive(Serialize)]
struct ResultData<'a> {
    database: String,
    schema: &'a str,
    root: &'a str,
    conformant: bool,
    markdown_count: usize,
    concept_count: usize,
    link_count: usize,
    diagnostic_count: usize,
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    match cli.command {
        None => {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            let request: Legacy = serde_json::from_str(&input)?;
            let facts: Vec<_> = request
                .documents
                .iter()
                .map(|v| engine::markdown_facts(v))
                .collect();
            serde_json::to_writer(io::stdout().lock(), &facts)?;
        }
        Some(Command::Load {
            root,
            exclude,
            read_concurrency,
        }) => {
            serde_json::to_writer(
                io::stdout().lock(),
                &engine::load_bundle(&root, &exclude, read_concurrency)?,
            )?;
        }
        Some(Command::Duckdb {
            root,
            database,
            schema,
            overwrite,
            exclude,
            read_concurrency,
        }) => {
            let bundle = engine::load_bundle(&root, &exclude, read_concurrency)?;
            database::materialize(&database, &schema, &bundle, overwrite)?;
            serde_json::to_writer(
                io::stdout().lock(),
                &ResultData {
                    database: database.to_string_lossy().into(),
                    schema: &schema,
                    root: &bundle.root,
                    conformant: !bundle.diagnostics.iter().any(|v| v.severity == "error"),
                    markdown_count: bundle.markdown_count,
                    concept_count: bundle.concepts.len(),
                    link_count: bundle.links.len(),
                    diagnostic_count: bundle.diagnostics.len(),
                },
            )?;
        }
    }
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("okf: {error}");
        std::process::exit(1);
    }
}
