mod commands;
mod engine;
mod mcp;
mod protocol;
mod python;
use clap::{Parser, Subcommand};
use serde::Deserialize;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::Command as ProcessCommand;

use mcp::Transport;
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
    /// Preview or commit a single-concept body edit (JSON request on stdin).
    #[command(name = "__edit", hide = true)]
    Edit,
    /// Check a bundle for the Python shell (JSON request on stdin).
    #[command(name = "__check", hide = true)]
    CheckRequest,
    /// Scaffold type specifications for the Python shell (JSON request on stdin).
    #[command(name = "__init-specs", hide = true)]
    InitSpecsRequest,
    #[command(name = "__engine-load", hide = true)]
    Load {
        root: PathBuf,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
        #[arg(long, default_value_t = 32)]
        read_concurrency: usize,
    },
    /// Validate every Markdown file recursively as OKF v0.2.
    Check {
        path: PathBuf,
        #[arg(long)]
        exclude: Vec<String>,
        /// Require every type in use to have the document this template derives.
        #[arg(long)]
        require_spec: Option<String>,
        /// Report the specification rules as errors instead of warnings.
        #[arg(long)]
        normative_spec: bool,
        /// Validate declared relations (answered by the Python engine).
        #[arg(long)]
        relational_schema: Option<String>,
        /// Explain how each candidate Markdown file participated.
        #[arg(long)]
        classify: bool,
    },
    /// Count concepts by type and optionally expose deterministic content digests.
    Inventory {
        path: PathBuf,
        #[arg(long)]
        exclude: Vec<String>,
        #[arg(long)]
        digests: bool,
    },
    /// Summarize the resolved concept graph.
    Graph {
        path: PathBuf,
        #[arg(long)]
        exclude: Vec<String>,
    },
    /// Scaffold missing specification documents, and optionally a starter `.schema.sql`.
    Init {
        path: PathBuf,
        #[arg(long)]
        spec_template: String,
        #[arg(long)]
        exclude: Vec<String>,
        #[arg(long)]
        write: bool,
        /// Also propose a starter `.schema.sql` (answered by the Python engine).
        #[arg(long)]
        infer_schema: bool,
    },
    /// Serve effect-aware MCP tools, exposing explicit commit tools only on opt-in.
    Serve {
        #[arg(long, value_enum, default_value_t = Transport::Stdio)]
        transport: Transport,
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value_t = 8000)]
        port: u16,
        /// Accept this HTTP `Host` header (repeatable), e.g. the public hostname
        /// behind a proxy. Loopback names are always accepted.
        #[arg(long = "allowed-host")]
        allowed_host: Vec<String>,
        /// Also register the tools that commit changes to the bundle.
        #[arg(long)]
        allow_write: bool,
    },
}
#[derive(Deserialize)]
struct Legacy {
    documents: Vec<String>,
}

type Outcome = Result<i32, Box<dyn std::error::Error>>;

fn stdin_text() -> io::Result<String> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    Ok(input)
}

/// Print a report as the CLI's JSON and exit with `code`.
fn print(report: &impl serde::Serialize, code: i32) -> Outcome {
    io::stdout()
        .lock()
        .write_all(commands::render(report)?.as_bytes())?;
    Ok(code)
}

fn run() -> Outcome {
    let cli = Cli::parse();
    match cli.command {
        Command::Facts => {
            let request: Legacy = serde_json::from_str(&stdin_text()?)?;
            let facts: Vec<_> = request
                .documents
                .iter()
                .map(|v| engine::markdown_facts(v))
                .collect();
            serde_json::to_writer(io::stdout().lock(), &facts)?;
        }
        Command::Edit => {
            serde_json::to_writer(io::stdout().lock(), &protocol::edit(&stdin_text()?)?)?;
        }
        Command::CheckRequest => {
            serde_json::to_writer(io::stdout().lock(), &protocol::check(&stdin_text()?)?)?;
        }
        Command::InitSpecsRequest => {
            serde_json::to_writer(io::stdout().lock(), &protocol::init_specs(&stdin_text()?)?)?;
        }
        Command::Load {
            root,
            exclude,
            read_concurrency,
        } => {
            let data = engine::load_bundle(&root, &exclude, read_concurrency)?;
            serde_json::to_writer(io::stdout().lock(), &protocol::LoadResponse::new(&data))?;
        }
        Command::Check {
            relational_schema: Some(_),
            ..
        }
        | Command::Init {
            infer_schema: true, ..
        } => return python_cli(),
        Command::Check {
            path,
            exclude,
            require_spec,
            normative_spec,
            relational_schema: None,
            classify,
        } => {
            let report = commands::check(
                &path,
                &exclude,
                require_spec.as_deref(),
                normative_spec,
                classify,
            )?;
            let code = i32::from(!report.conformant);
            return print(&report, code);
        }
        Command::Inventory {
            path,
            exclude,
            digests,
        } => return print(&commands::inventory(&path, &exclude, digests)?, 0),
        Command::Graph { path, exclude } => return print(&commands::graph(&path, &exclude)?, 0),
        Command::Init {
            path,
            spec_template,
            exclude,
            write,
            infer_schema: false,
        } => {
            let specs = commands::init_specs(&path, &exclude, &spec_template, write)?;
            let code = i32::from(!specs.collisions.is_empty());
            return print(&commands::InitReport { specs }, code);
        }
        Command::Serve {
            transport,
            host,
            port,
            allowed_host,
            allow_write,
        } => mcp::serve(transport, &host, port, &allowed_host, allow_write)?,
    }
    Ok(0)
}

/// Hand the whole command line to the Python CLI and return its exit code.
fn python_cli() -> Outcome {
    let status = ProcessCommand::new(python::interpreter()?)
        .arg("-m")
        .arg("okf_parser.cli")
        .args(std::env::args_os().skip(1))
        .status()?;
    Ok(status.code().unwrap_or(1))
}

/// Commands this binary answers itself; everything else is the Python CLI's.
const NATIVE: &[&str] = &[
    "__engine-facts",
    "__engine-load",
    "__edit",
    "__check",
    "__init-specs",
    "check",
    "inventory",
    "graph",
    "init",
    "serve",
];

fn main() {
    let native = std::env::args()
        .nth(1)
        .is_some_and(|command| NATIVE.contains(&command.as_str()));
    let outcome = if native { run() } else { python_cli() };
    match outcome {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("okf-parser: {error}");
            std::process::exit(1);
        }
    }
}
