mod engine;
mod mcp;
mod python;
use clap::{Parser, Subcommand};
use serde::Deserialize;
use std::io::{self, Read};
use std::path::PathBuf;
use std::process::{Command as ProcessCommand, ExitStatus};

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
    #[command(name = "__engine-load", hide = true)]
    Load {
        root: PathBuf,
        #[arg(long = "exclude")]
        exclude: Vec<String>,
        #[arg(long, default_value_t = 32)]
        read_concurrency: usize,
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
        Command::Serve {
            transport,
            host,
            port,
            allowed_host,
            allow_write,
        } => mcp::serve(transport, &host, port, &allowed_host, allow_write)?,
    }
    Ok(())
}

fn python_cli() -> Result<ExitStatus, Box<dyn std::error::Error>> {
    Ok(ProcessCommand::new(python::interpreter()?)
        .arg("-m")
        .arg("okf_parser.cli")
        .args(std::env::args_os().skip(1))
        .status()?)
}

fn main() {
    let internal = matches!(
        std::env::args().nth(1).as_deref(),
        Some("__engine-facts" | "__engine-load" | "serve")
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
