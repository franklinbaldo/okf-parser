mod database;
mod engine;
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};
use std::io::{self, Read};
use std::path::PathBuf;
use std::process::{Command as ProcessCommand, ExitStatus};
#[derive(Parser)]
#[command(name = "okf-parser", version = env!("CARGO_PKG_VERSION"), about = "Native OKF engine")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    #[command(name = "__engine-facts", hide = true)]
    Facts,
    #[command(name = "__engine-load", hide = true)]
    Load {
        root: PathBuf,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
        #[arg(long, default_value_t = 32)]
        read_concurrency: usize,
    },
    #[command(name = "__engine-duckdb", hide = true)]
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
        Command::Facts => {
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
        Command::Load {
            root,
            exclude,
            read_concurrency,
        } => {
            serde_json::to_writer(
                io::stdout().lock(),
                &engine::load_bundle(&root, &exclude, read_concurrency)?,
            )?;
        }
        Command::Duckdb {
            root,
            database,
            schema,
            overwrite,
            exclude,
            read_concurrency,
        } => {
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

// The environment's interpreter, searched from the executable's own location.
// Two layouts exist: the executable directly in the environment's scripts
// directory (sibling python), and maturin's vendored layout where the real
// binary lives in site-packages/okf_parser.scripts/ — several levels below
// the environment root — because bundling an external dynamic library moves
// it there. Walking every ancestor covers both without guessing which one
// this build used.
fn find_python(executable: &std::path::Path) -> Option<std::ffi::OsString> {
    let candidates: &[&[&str]] = if cfg!(windows) {
        &[&["python.exe"], &["Scripts", "python.exe"]]
    } else {
        &[&["python"], &["bin", "python"], &["bin", "python3"]]
    };
    for dir in executable.ancestors().skip(1) {
        for parts in candidates {
            let mut candidate = dir.to_path_buf();
            for part in *parts {
                candidate.push(part);
            }
            if candidate.is_file() {
                return Some(candidate.into_os_string());
            }
        }
    }
    None
}

fn python_cli() -> Result<ExitStatus, Box<dyn std::error::Error>> {
    let executable = std::env::current_exe()?;
    let python = find_python(&executable).unwrap_or_else(|| {
        if cfg!(windows) {
            "python".into()
        } else {
            "python3".into()
        }
    });
    Ok(ProcessCommand::new(python)
        .arg("-m")
        .arg("okf_parser.cli")
        .args(std::env::args_os().skip(1))
        .status()?)
}

fn main() {
    let internal = matches!(
        std::env::args().nth(1).as_deref(),
        Some("__engine-facts" | "__engine-load" | "__engine-duckdb")
    );
    if internal {
        if let Err(error) = run() {
            eprintln!("okf-parser: {error}");
            std::process::exit(1);
        }
        return;
    }
    match python_cli() {
        Ok(status) => std::process::exit(status.code().unwrap_or(1)),
        Err(error) => {
            eprintln!("okf-parser: {error}");
            std::process::exit(1);
        }
    }
}
