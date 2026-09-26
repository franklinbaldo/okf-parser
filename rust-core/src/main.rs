mod commands;
mod engine;
mod mcp;
mod protocol;
mod python;
use clap::{Args, Parser, Subcommand};
use serde::Deserialize;
use std::ffi::OsString;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::Command as ProcessCommand;

use mcp::Transport;
use okf_db::export::ExportOptions;
use okf_db::query::QueryOptions;
/// The command line is declared here in full, so `--help` lists every public
/// command. Commands that still need Python (RFC 0024 phases 4-6) are declared
/// as pass-through: their arguments, `--help` included, go to the Python CLI
/// unparsed.
#[derive(Parser)]
#[command(
    name = "okf-parser",
    version = env!("CARGO_PKG_VERSION"),
    about = "Validate, inspect and transform Open Knowledge Format bundles",
    arg_required_else_help = true
)]
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
    /// Scaffold specifications and starter schemas (JSON request on stdin).
    #[command(name = "__init", hide = true)]
    InitRequest,
    /// Export a bundle into DuckDB for the Python API (JSON request on stdin).
    #[command(name = "__export-duckdb", hide = true)]
    ExportRequest,
    /// Run a `.schema.sql` and read its declared table (JSON request on stdin).
    #[command(name = "__declared-schema", hide = true)]
    DeclaredSchema,
    /// Run a relational schema and read its keys (JSON request on stdin).
    #[command(name = "__relational-schema", hide = true)]
    RelationalSchema,
    /// Snapshot a bundle for the Python apply planner (JSON request on stdin).
    #[command(name = "__apply-snapshot", hide = true)]
    ApplySnapshot,
    /// Edit, fingerprint and commit a planned apply (JSON request on stdin).
    #[command(name = "__apply-commit", hide = true)]
    ApplyCommit,
    /// Query a loaded bundle for the Python shell (JSON request on stdin).
    #[command(name = "__sql", hide = true)]
    SqlRequest,
    /// Render new OKF documents canonically (JSON request on stdin).
    #[command(name = "__render", hide = true)]
    Render,
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
        /// Skip files matching this gitignore-style pattern (repeatable).
        #[arg(long, value_name = "PATTERN")]
        exclude: Vec<String>,
        /// Require every type in use to have the document this template derives.
        #[arg(long, value_name = "TEMPLATE")]
        require_spec: Option<String>,
        /// Report the specification rules as errors instead of warnings.
        #[arg(long)]
        normative_spec: bool,
        /// Check the keys and references declared in this SQL file (usually
        /// `okf.schema.sql`, relative to the bundle): OKF020-OKF022.
        #[arg(long, value_name = "PATH")]
        relational_schema: Option<PathBuf>,
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
        /// Where each type's specification lives, e.g. `specs/{slug}.md`.
        #[arg(long, value_name = "TEMPLATE")]
        spec_template: String,
        /// Skip files matching this gitignore-style pattern (repeatable).
        #[arg(long, value_name = "PATTERN")]
        exclude: Vec<String>,
        /// Create the files; without it, only report what would be created.
        #[arg(long)]
        write: bool,
        /// Also propose a starter `.schema.sql` beside each spec that lacks
        /// one, typed from the values the documents use.
        #[arg(long)]
        infer_schema: bool,
    },
    /// Run one read-only SQL query over the bundle's tables.
    #[command(
        after_help = "Tables: concepts, links, reserved and diagnostics (schema okf) and, \
        with --spec-template, one per declared type (schema okf_types), all on the search path. \
        The query cannot read files, reach the network or change settings.\n\n\
        Example: okf-parser sql notes \"SELECT concept_type, count(*) FROM concepts GROUP BY 1\""
    )]
    Sql {
        path: PathBuf,
        /// One SQL query (SELECT, WITH, FROM ..., VALUES).
        query: String,
        /// Skip files matching this gitignore-style pattern (repeatable).
        #[arg(long, value_name = "PATTERN")]
        exclude: Vec<String>,
        /// Also materialize each declared type (see `duckdb --spec-template`).
        #[arg(long, value_name = "TEMPLATE")]
        spec_template: Option<String>,
        /// Return at most this many rows.
        #[arg(long)]
        limit: Option<usize>,
    },
    /// Materialize every row of a DuckDB-readable source (CSV, Parquet, JSON) as a concept.
    #[command(name = "import", disable_help_flag = true)]
    Import(Delegated),
    /// Export JSON Schema, Zod, Pydantic source, or GraphQL SDL.
    #[command(disable_help_flag = true)]
    Schema(Delegated),
    /// Check mdformat style, writing only when --write is explicit.
    #[command(disable_help_flag = true)]
    Format(Delegated),
    /// Mutate frontmatter fields via a bounded ALTER TABLE + UPDATE SQL script.
    #[command(disable_help_flag = true)]
    Apply(Delegated),
    /// Materialize an OKF bundle into a DuckDB database file.
    #[command(
        after_help = "Tables: concepts, links, reserved and diagnostics in SCHEMA; with \
        --spec-template, one table per declared type in SCHEMA_types."
    )]
    Duckdb {
        path: PathBuf,
        /// The DuckDB database to write: a file, or `:memory:`.
        #[arg(default_value = "knowledge.duckdb")]
        database: String,
        /// The schema that receives the tables.
        #[arg(default_value = "okf")]
        schema: String,
        /// Replace tables the schema already has instead of refusing.
        #[arg(long)]
        overwrite: bool,
        /// Skip files matching this gitignore-style pattern (repeatable).
        #[arg(long, value_name = "PATTERN")]
        exclude: Vec<String>,
        /// Run each type's `.schema.sql` beside the spec this template derives
        /// and write its typed table into `{schema}_types`.
        #[arg(long, value_name = "TEMPLATE")]
        spec_template: Option<String>,
    },
    /// List opt-in OKF type packs registered by installed package metadata.
    #[command(disable_help_flag = true)]
    Packs(Delegated),
    /// Preview or install an opt-in type pack into an ordinary OKF bundle.
    #[command(name = "add-pack", disable_help_flag = true)]
    AddPack(Delegated),
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
/// The unparsed arguments of a command the Python CLI answers.
#[derive(Args)]
struct Delegated {
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    args: Vec<OsString>,
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
        Command::InitRequest => {
            serde_json::to_writer(io::stdout().lock(), &protocol::init(&stdin_text()?)?)?;
        }
        Command::ExportRequest => {
            let response = protocol::export_duckdb(&stdin_text()?)?;
            serde_json::to_writer(io::stdout().lock(), &response)?;
        }
        Command::DeclaredSchema => {
            let response = protocol::declared_schema(&stdin_text()?)?;
            serde_json::to_writer(io::stdout().lock(), &response)?;
        }
        Command::RelationalSchema => {
            let response = protocol::relational_schema(&stdin_text()?)?;
            serde_json::to_writer(io::stdout().lock(), &response)?;
        }
        Command::ApplySnapshot => {
            let response = protocol::apply_snapshot(&stdin_text()?)?;
            serde_json::to_writer(io::stdout().lock(), &response)?;
        }
        Command::ApplyCommit => {
            serde_json::to_writer(io::stdout().lock(), &protocol::apply(&stdin_text()?)?)?;
        }
        Command::SqlRequest => {
            serde_json::to_writer(io::stdout().lock(), &protocol::sql(&stdin_text()?)?)?;
        }
        Command::Render => {
            serde_json::to_writer(io::stdout().lock(), &protocol::render(&stdin_text()?)?)?;
        }
        Command::Load {
            root,
            exclude,
            read_concurrency,
        } => {
            let data = engine::load_bundle(&root, &exclude, read_concurrency)?;
            serde_json::to_writer(io::stdout().lock(), &protocol::LoadResponse::new(&data))?;
        }
        Command::Import(_)
        | Command::Schema(_)
        | Command::Format(_)
        | Command::Apply(_)
        | Command::Packs(_)
        | Command::AddPack(_) => return python_cli(),
        Command::Check {
            path,
            exclude,
            require_spec,
            normative_spec,
            relational_schema,
            classify,
        } => {
            let options = commands::CheckOptions {
                require_spec: require_spec.as_deref(),
                normative_spec,
                classify,
                relational_schema: relational_schema.as_deref(),
            };
            let report = commands::check(&path, &exclude, options)?;
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
            infer_schema,
        } => {
            let report = commands::init(&path, &exclude, &spec_template, write, infer_schema)?;
            let code = i32::from(report.collided());
            return print(&report, code);
        }
        Command::Sql {
            path,
            query,
            exclude,
            spec_template,
            limit,
        } => {
            let options = QueryOptions {
                spec_template: spec_template.as_deref(),
                limit,
            };
            return print(&commands::sql(&path, &exclude, &query, options)?, 0);
        }
        Command::Duckdb {
            path,
            database,
            schema,
            overwrite,
            exclude,
            spec_template,
        } => {
            let options = ExportOptions {
                database: &database,
                schema: &schema,
                overwrite,
                exclude: &exclude,
                spec_template: spec_template.as_deref(),
            };
            let answer = commands::export(&path, &options)?;
            let code = i32::from(answer.refused());
            return print(&answer, code);
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

fn main() {
    match run() {
        Ok(code) => std::process::exit(code),
        Err(error) => {
            eprintln!("okf-parser: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn help_lists_every_public_command_and_hides_the_protocol() {
        let help = Cli::command().render_long_help().to_string();
        for command in [
            "check",
            "sql",
            "inventory",
            "graph",
            "init",
            "import",
            "schema",
            "format",
            "apply",
            "duckdb",
            "packs",
            "add-pack",
            "serve",
        ] {
            assert!(
                help.lines()
                    .any(|line| line.trim_start().starts_with(command)),
                "{command} missing from:\n{help}"
            );
        }
        assert!(!help.contains("__"), "{help}");
    }

    #[test]
    fn delegated_commands_keep_every_argument_unparsed() {
        let cli = Cli::try_parse_from(["okf-parser", "schema", "b", "--format", "zod", "--help"])
            .unwrap();
        let Command::Schema(Delegated { args }) = cli.command else {
            panic!("schema should be delegated");
        };
        assert_eq!(args, ["b", "--format", "zod", "--help"]);
    }
}
