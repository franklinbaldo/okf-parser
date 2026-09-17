mod engine;
use clap::{Parser, Subcommand};
use serde::Deserialize;
use serde_json::Value;
use std::io::{self, Read, Write};
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
    #[command(name = "__engine-render-frontmatter", hide = true)]
    RenderFrontmatter,
}
#[derive(Deserialize)]
struct Legacy {
    documents: Vec<String>,
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
        Command::RenderFrontmatter => {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            let value: Value = serde_json::from_str(&input)?;
            let mapping = value
                .as_object()
                .ok_or("frontmatter render request must be a JSON object")?;
            let bytes = engine::render_frontmatter(mapping).map_err(io::Error::other)?;
            io::stdout().lock().write_all(&bytes)?;
        }
    }
    Ok(())
}

// The environment's interpreter, searched from the executable's own location.
// A sibling interpreter covers the ordinary layout, where installers place
// this executable in the environment's own scripts directory. Walking the
// remaining ancestors covers layouts that place it deeper, which packaging
// tools do when they relocate a binary; it costs nothing when the sibling
// is already there.
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
        Some("__engine-facts" | "__engine-load" | "__engine-render-frontmatter")
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
