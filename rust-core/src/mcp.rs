//! `okf-parser serve`: the effect-aware MCP server (RFC 0008, RFC 0024).
//!
//! The protocol, the tool schemas and the effect annotations live here. A tool
//! is answered natively once its logic exists in Rust: `check`, `inventory`,
//! `graph`, `sql`, `apply_*`, `init_*` and `duckdb_export`. The others
//! (`schema`, `format_*`, `import_*`) are delegated to `python -m okf_parser.mcp_bridge`,
//! which runs the same service function the Python CLI does.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::{fmt, io};

use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::{CallToolResult, ContentBlock, Implementation, ServerCapabilities, ServerConfig};
use rmcp::transport::streamable_http_server::session::local::LocalSessionManager;
use rmcp::transport::streamable_http_server::{StreamableHttpServerConfig, StreamableHttpService};
use rmcp::{ServerHandler, ServiceExt, schemars, tool, tool_handler, tool_router};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::io::AsyncWriteExt;

use okf_db::export::ExportOptions;
use okf_db::query::QueryOptions;
use okf_engine::search::{Detail, Mode, SearchOutput, SearchRequest};

use crate::{commands, python};

const INSTRUCTIONS: &str = "Deterministic OKF inspection and preview tools. Explicit commit tools \
are available only when the server is launched with --allow-write. Tool annotations describe \
maximum effects and are hints, not authorization.";

/// Tools that commit changes; registered only under `--allow-write`.
pub const WRITE_TOOLS: [&str; 5] = [
    "format_write",
    "apply_write",
    "init_write",
    "import_write",
    "duckdb_export",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, clap::ValueEnum)]
pub enum Transport {
    Stdio,
    Http,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PathArgs {
    path: PathBuf,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CheckArgs {
    path: PathBuf,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    require_spec: Option<String>,
    #[serde(default)]
    normative_spec: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    relational_schema: Option<PathBuf>,
    #[serde(default)]
    classify: bool,
}

/// At most this many passages answer a `search` call.
const SEARCH_LIMIT: usize = 100;

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum SearchModeArg {
    /// Ranked by BM25 over case-folded words.
    #[default]
    Lexical,
    /// Lines containing the case-folded query.
    Literal,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum SearchDetailArg {
    /// `location<TAB>snippet` rows: the cheapest answer.
    #[default]
    Compact,
    /// Rows with a `score` column.
    Score,
    /// Structured hits with concept id, type, body lines and source digest.
    Full,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SearchArgs {
    path: PathBuf,
    query: String,
    #[serde(default)]
    mode: SearchModeArg,
    /// Answer at most this many passages (1-100).
    #[serde(default = "default_search_limit")]
    limit: usize,
    /// Widen each hit by this many body lines on each side.
    #[serde(default)]
    context: usize,
    /// Only concepts of this type.
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    concept_type: Option<String>,
    /// Only documents matching this gitignore-style pattern.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    path_glob: Option<String>,
    #[serde(default)]
    detail: SearchDetailArg,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
}

const fn default_search_limit() -> usize {
    10
}

/// At most this many rows answer a `sql` call unless it asks for fewer.
const SQL_ROW_LIMIT: usize = 1_000;

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SqlArgs {
    path: PathBuf,
    /// One read-only query (SELECT, WITH, FROM ..., VALUES) over `concepts`,
    /// `links`, `reserved`, `diagnostics` and, with `spec_template`, one table
    /// per declared type.
    query: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    spec_template: Option<String>,
    /// Return at most this many rows (1-1000).
    #[serde(default = "default_sql_limit")]
    limit: usize,
}

fn default_sql_limit() -> usize {
    SQL_ROW_LIMIT
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InventoryArgs {
    path: PathBuf,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default)]
    digests: bool,
}

#[derive(Debug, Default, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(rename_all = "lowercase")]
#[schemars(inline)]
pub enum SchemaFormat {
    #[default]
    Json,
    Zod,
    Pydantic,
    Graphql,
}

#[derive(Debug, Default, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(rename_all = "lowercase")]
#[schemars(inline)]
pub enum ZodImport {
    #[default]
    Zod,
    Astro,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SchemaArgs {
    path: PathBuf,
    #[serde(default, rename = "format")]
    schema_format: SchemaFormat,
    #[serde(default)]
    infer_types: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    cast: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default)]
    zod_import: ZodImport,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    spec_template: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApplyArgs {
    path: PathBuf,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    sql: Option<String>,
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    type_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    field: Option<String>,
    #[serde(default, rename = "from", skip_serializing_if = "Option::is_none")]
    from_value: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    to: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    spec_template: Option<String>,
}

impl ApplyArgs {
    fn into_input(
        self,
        write: bool,
        expected_preview_token: Option<String>,
    ) -> commands::ApplyInput {
        commands::ApplyInput {
            path: self.path,
            sql: self.sql,
            type_name: self.type_name,
            field: self.field,
            from_value: self.from_value,
            to: self.to,
            exclude: self.exclude.unwrap_or_default(),
            spec_template: self.spec_template,
            write,
            expected_preview_token,
        }
    }
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ApplyWriteArgs {
    path: PathBuf,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    sql: Option<String>,
    #[serde(default, rename = "type", skip_serializing_if = "Option::is_none")]
    type_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    field: Option<String>,
    #[serde(default, rename = "from", skip_serializing_if = "Option::is_none")]
    from_value: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    to: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    spec_template: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    expected_preview_token: Option<String>,
}

impl ApplyWriteArgs {
    fn into_input(self) -> commands::ApplyInput {
        let apply = ApplyArgs {
            path: self.path,
            sql: self.sql,
            type_name: self.type_name,
            field: self.field,
            from_value: self.from_value,
            to: self.to,
            exclude: self.exclude,
            spec_template: self.spec_template,
        };
        apply.into_input(true, self.expected_preview_token)
    }
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct InitArgs {
    path: PathBuf,
    spec_template: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default)]
    infer_schema: bool,
}

#[derive(Debug, Default, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(rename_all = "kebab-case")]
#[schemars(inline)]
pub enum ImportConflictPolicy {
    #[default]
    Skip,
    VerifyIdentical,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ImportPreviewArgs {
    // Anything DuckDB can scan: a local file, a URL or another source string.
    source: String,
    path: PathBuf,
    #[serde(rename = "type")]
    type_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    id_column: Option<String>,
    #[serde(default)]
    overwrite: bool,
    #[serde(default)]
    on_conflict: ImportConflictPolicy,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ImportWriteArgs {
    // Anything DuckDB can scan: a local file, a URL or another source string.
    source: String,
    path: PathBuf,
    #[serde(rename = "type")]
    type_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    id_column: Option<String>,
    #[serde(default)]
    overwrite: bool,
    #[serde(default)]
    on_conflict: ImportConflictPolicy,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    expected_preview_token: Option<String>,
}

#[derive(Debug, Deserialize, Serialize, schemars::JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DuckdbExportArgs {
    path: PathBuf,
    // A DuckDB database name: a file, `:memory:` or another DuckDB target.
    #[serde(default = "default_database")]
    database: String,
    #[serde(default = "default_schema")]
    schema: String,
    #[serde(default)]
    overwrite: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    exclude: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    spec_template: Option<String>,
}

fn default_database() -> String {
    "knowledge.duckdb".into()
}

fn default_schema() -> String {
    "okf".into()
}

#[derive(Clone)]
pub struct OkfServer {
    tool_router: ToolRouter<Self>,
    python: Arc<PathBuf>,
}

impl OkfServer {
    pub fn new(allow_write: bool, python: PathBuf) -> Self {
        let mut tool_router = Self::tool_router();
        if !allow_write {
            for name in WRITE_TOOLS {
                tool_router.remove_route(name);
            }
        }
        Self {
            tool_router,
            python: Arc::new(python),
        }
    }

    async fn delegate(&self, tool: &str, arguments: impl Serialize) -> CallToolResult {
        let request = json!({"tool": tool, "arguments": arguments});
        match python_bridge(&self.python, &request).await {
            Ok(Value::String(text)) => CallToolResult::success(vec![ContentBlock::text(text)]),
            Ok(value) => CallToolResult::structured(value),
            Err(error) => tool_error(&error),
        }
    }
}

/// A tool failure as the client sees it; the only place errors become text.
fn tool_error(error: &dyn std::error::Error) -> CallToolResult {
    CallToolResult::error(vec![ContentBlock::text(error.to_string())])
}

/// Why a delegated call produced no result.
#[derive(Debug)]
enum BridgeError {
    Spawn {
        python: PathBuf,
        source: io::Error,
    },
    NoStdin,
    Io(io::Error),
    Encode(serde_json::Error),
    /// The bridge exited non-zero; this is the Python exception it reported.
    Raised(String),
    InvalidResponse(serde_json::Error),
}

impl fmt::Display for BridgeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Spawn { python, source } => {
                write!(f, "cannot start {}: {source}", python.display())
            }
            Self::NoStdin => f.write_str("python bridge has no stdin"),
            Self::Io(error) => error.fmt(f),
            Self::Encode(error) => write!(f, "cannot encode bridge request: {error}"),
            Self::Raised(exception) => f.write_str(exception),
            Self::InvalidResponse(error) => write!(f, "invalid bridge response: {error}"),
        }
    }
}

impl std::error::Error for BridgeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Spawn { source, .. } => Some(source),
            Self::Io(error) => Some(error),
            Self::Encode(error) | Self::InvalidResponse(error) => Some(error),
            Self::NoStdin | Self::Raised(_) => None,
        }
    }
}

impl OkfServer {
    /// `init_preview`/`init_write`.
    async fn init(&self, args: InitArgs, write: bool) -> CallToolResult {
        native(move || {
            commands::init(
                &args.path,
                exclude(&args.exclude),
                &args.spec_template,
                write,
                args.infer_schema,
            )
        })
        .await
    }
}

/// Run one delegated call and return its JSON result, or the error it raised.
async fn python_bridge(python: &Path, request: &Value) -> Result<Value, BridgeError> {
    let mut child = tokio::process::Command::new(python)
        .args(["-m", "okf_parser.mcp_bridge"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|source| BridgeError::Spawn {
            python: python.to_owned(),
            source,
        })?;
    let mut stdin = child.stdin.take().ok_or(BridgeError::NoStdin)?;
    let body = serde_json::to_vec(request).map_err(BridgeError::Encode)?;
    stdin.write_all(&body).await.map_err(BridgeError::Io)?;
    drop(stdin);
    let output = child.wait_with_output().await.map_err(BridgeError::Io)?;
    if !output.status.success() {
        return Err(BridgeError::Raised(bridge_error(&String::from_utf8_lossy(
            &output.stderr,
        ))));
    }
    serde_json::from_slice(&output.stdout).map_err(BridgeError::InvalidResponse)
}

/// Keep the exception line of a Python traceback, which is what a caller can act on.
fn bridge_error(stderr: &str) -> String {
    stderr
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("python bridge failed without output")
        .trim()
        .to_owned()
}

/// Run a native command off the async runtime and return its report as
/// structured content, or its error as the tool's error text.
async fn native<T, E>(command: impl FnOnce() -> Result<T, E> + Send + 'static) -> CallToolResult
where
    T: Serialize + Send + 'static,
    E: std::error::Error + Send + 'static,
{
    match tokio::task::spawn_blocking(command).await {
        Ok(Ok(report)) => match serde_json::to_value(report) {
            Ok(value) => CallToolResult::structured(value),
            Err(error) => tool_error(&error),
        },
        Ok(Err(error)) => tool_error(&error),
        Err(error) => tool_error(&error),
    }
}

fn exclude(patterns: &Option<Vec<String>>) -> &[String] {
    patterns.as_deref().unwrap_or_default()
}

#[tool_router]
impl OkfServer {
    #[tool(
        description = "Validate every Markdown file recursively as OKF v0.2.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn check(&self, Parameters(args): Parameters<CheckArgs>) -> CallToolResult {
        native(move || {
            let options = commands::CheckOptions {
                require_spec: args.require_spec.as_deref(),
                normative_spec: args.normative_spec,
                classify: args.classify,
                relational_schema: args.relational_schema.as_deref(),
            };
            commands::check(&args.path, exclude(&args.exclude), options)
        })
        .await
    }

    #[tool(
        description = "Count concepts by type and optionally expose deterministic content digests.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn inventory(&self, Parameters(args): Parameters<InventoryArgs>) -> CallToolResult {
        native(move || commands::inventory(&args.path, exclude(&args.exclude), args.digests)).await
    }

    #[tool(
        description = "Run one read-only SQL query over the bundle: tables concepts, links, \
            reserved and diagnostics, plus one table per declared type with spec_template. \
            The query cannot read files or reach the network. `truncated` says whether \
            `limit` cut the rows short.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn sql(&self, Parameters(args): Parameters<SqlArgs>) -> CallToolResult {
        native(move || {
            let options = QueryOptions {
                spec_template: args.spec_template.as_deref(),
                limit: Some(args.limit.clamp(1, SQL_ROW_LIMIT)),
            };
            commands::sql(&args.path, exclude(&args.exclude), &args.query, options)
        })
        .await
    }

    #[tool(
        description = "Search concept bodies offline. Each non-blank body line is a passage; \
            `lexical` ranks them by BM25, `literal` keeps those containing the query. \
            Compact rows are `location<TAB>snippet` with locations like `path#B12`; \
            `full` adds concept id, type and source digest. Use `context` to read around a hit.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn search(&self, Parameters(args): Parameters<SearchArgs>) -> CallToolResult {
        let answer = tokio::task::spawn_blocking(move || {
            let request = SearchRequest {
                query: &args.query,
                mode: match args.mode {
                    SearchModeArg::Lexical => Mode::Lexical,
                    SearchModeArg::Literal => Mode::Literal,
                },
                limit: args.limit.clamp(1, SEARCH_LIMIT),
                context: args.context,
                concept_type: args.concept_type.as_deref(),
                path_glob: args.path_glob.as_deref(),
                detail: match args.detail {
                    SearchDetailArg::Compact => Detail::Compact,
                    SearchDetailArg::Score => Detail::Score,
                    SearchDetailArg::Full => Detail::Full,
                },
            };
            commands::search(&args.path, exclude(&args.exclude), &request)
        })
        .await;
        match answer {
            Ok(Ok(SearchOutput::Rows(rows))) => {
                CallToolResult::success(vec![ContentBlock::text(rows)])
            }
            Ok(Ok(full)) => match serde_json::to_value(full) {
                Ok(value) => CallToolResult::structured(value),
                Err(error) => tool_error(&error),
            },
            Ok(Err(error)) => tool_error(&error),
            Err(error) => tool_error(&error),
        }
    }

    #[tool(
        description = "Summarize resolved concept relationships.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn graph(&self, Parameters(args): Parameters<PathArgs>) -> CallToolResult {
        native(move || commands::graph(&args.path, exclude(&args.exclude))).await
    }

    #[tool(
        description = "Export schemas or validation source from one shared contract.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = false,
            open_world_hint = true
        )
    )]
    async fn schema(&self, Parameters(args): Parameters<SchemaArgs>) -> CallToolResult {
        self.delegate("schema", args).await
    }

    #[tool(
        description = "Check mdformat style without modifying files.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn format_check(&self, Parameters(args): Parameters<PathArgs>) -> CallToolResult {
        self.delegate("format_check", args).await
    }

    #[tool(
        description = "Preview a frontmatter edit written as SQL: each concept type is a \
            table, each scalar field a VARCHAR column; the final tables say what changes. \
            Returns the changed paths and a preview_token for apply_write.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn apply_preview(&self, Parameters(args): Parameters<ApplyArgs>) -> CallToolResult {
        native(move || commands::apply(&args.into_input(false, None))).await
    }

    #[tool(
        description = "Plan missing specification files without creating them.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn init_preview(&self, Parameters(args): Parameters<InitArgs>) -> CallToolResult {
        self.init(args, false).await
    }

    #[tool(
        description = "Plan a tabular import without creating or replacing concept files.",
        annotations(
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = true
        )
    )]
    async fn import_preview(
        &self,
        Parameters(args): Parameters<ImportPreviewArgs>,
    ) -> CallToolResult {
        self.delegate("import_preview", args).await
    }

    #[tool(
        description = "Rewrite Markdown files into canonical format.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn format_write(&self, Parameters(args): Parameters<PathArgs>) -> CallToolResult {
        self.delegate("format_write", args).await
    }

    #[tool(
        description = "Commit a frontmatter edit written as SQL, as apply_preview describes it. \
            Pass the preview's token as expected_preview_token to refuse a changed candidate.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = false,
            open_world_hint = false
        )
    )]
    async fn apply_write(&self, Parameters(args): Parameters<ApplyWriteArgs>) -> CallToolResult {
        native(move || commands::apply(&args.into_input())).await
    }

    #[tool(
        description = "Create missing specification files using the existing scaffold service.",
        annotations(
            read_only_hint = false,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn init_write(&self, Parameters(args): Parameters<InitArgs>) -> CallToolResult {
        self.init(args, true).await
    }

    #[tool(
        description = "Commit a tabular import using the existing import service.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = false,
            open_world_hint = true
        )
    )]
    async fn import_write(&self, Parameters(args): Parameters<ImportWriteArgs>) -> CallToolResult {
        self.delegate("import_write", args).await
    }

    #[tool(
        description = "Materialize the bundle into a persistent DuckDB database.",
        annotations(
            read_only_hint = false,
            destructive_hint = true,
            idempotent_hint = false,
            open_world_hint = true
        )
    )]
    async fn duckdb_export(
        &self,
        Parameters(args): Parameters<DuckdbExportArgs>,
    ) -> CallToolResult {
        native(move || {
            let options = ExportOptions {
                database: &args.database,
                schema: &args.schema,
                overwrite: args.overwrite,
                exclude: exclude(&args.exclude),
                spec_template: args.spec_template.as_deref(),
            };
            commands::export(&args.path, &options)
        })
        .await
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for OkfServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("okf-parser", env!("CARGO_PKG_VERSION")))
            .with_instructions(INSTRUCTIONS)
    }
}

/// The `Host` header values the HTTP transport accepts.
///
/// `bind` is where the socket listens, not what clients call the server: behind
/// a proxy or on a public hostname the request arrives as `Host:
/// service.example.com`. rmcp's loopback defaults stay, a concrete bind address
/// is added (clients may address it directly), an unspecified one (`0.0.0.0`,
/// `::`) is not, and every `--allowed-host` is added. Validation itself is never
/// disabled: it is the DNS-rebinding protection.
pub fn allowed_hosts(defaults: &[String], bind: &str, extra: &[String]) -> Vec<String> {
    let unspecified = bind
        .trim_matches(['[', ']'])
        .parse::<std::net::IpAddr>()
        .is_ok_and(|ip| ip.is_unspecified());
    let bind = (!unspecified).then_some(bind);
    let mut hosts: Vec<String> = Vec::new();
    for host in defaults
        .iter()
        .map(String::as_str)
        .chain(bind)
        .chain(extra.iter().map(String::as_str))
    {
        if !hosts.iter().any(|known| known == host) {
            hosts.push(host.to_owned());
        }
    }
    hosts
}

pub fn serve(
    transport: Transport,
    host: &str,
    port: u16,
    allowed_host: &[String],
    allow_write: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let python = python::interpreter()?;
    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(async move {
        match transport {
            Transport::Stdio => {
                let service = OkfServer::new(allow_write, python)
                    .serve(rmcp::transport::stdio())
                    .await?;
                service.waiting().await?;
            }
            Transport::Http => {
                let mut config = StreamableHttpServerConfig::default();
                config.allowed_hosts = allowed_hosts(&config.allowed_hosts, host, allowed_host);
                let service = StreamableHttpService::new(
                    move || Ok(OkfServer::new(allow_write, python.clone())),
                    Arc::new(LocalSessionManager::default()),
                    config,
                );
                let router = axum::Router::new().nest_service("/mcp", service);
                let listener = tokio::net::TcpListener::bind((host, port)).await?;
                axum::serve(listener, router).await?;
            }
        }
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use okf_engine::LoadError;

    fn tool_names(server: &OkfServer) -> Vec<String> {
        let mut names: Vec<_> = server
            .tool_router
            .list_all()
            .into_iter()
            .map(|tool| tool.name.into_owned())
            .collect();
        names.sort();
        names
    }

    #[test]
    fn default_profile_hides_every_commit_tool() {
        let names = tool_names(&OkfServer::new(false, PathBuf::from("python")));
        assert_eq!(
            names,
            [
                "apply_preview",
                "check",
                "format_check",
                "graph",
                "import_preview",
                "init_preview",
                "inventory",
                "schema",
                "search",
                "sql",
            ]
        );
    }

    #[test]
    fn allow_write_adds_exactly_the_commit_tools() {
        let names = tool_names(&OkfServer::new(true, PathBuf::from("python")));
        assert_eq!(names.len(), 15);
        for tool in WRITE_TOOLS {
            assert!(names.iter().any(|name| name == tool), "missing {tool}");
        }
    }

    #[test]
    fn delegated_arguments_keep_their_wire_names() {
        let args: ApplyArgs =
            serde_json::from_value(json!({"path": "b", "type": "T", "from": "x"})).unwrap();
        assert_eq!(
            serde_json::to_value(args).unwrap(),
            json!({"path": "b", "type": "T", "from": "x"})
        );
    }

    fn strings(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn allowed_hosts_keep_loopback_and_add_a_concrete_bind_address() {
        let defaults = strings(&["localhost", "127.0.0.1"]);
        assert_eq!(
            allowed_hosts(&defaults, "10.0.0.5", &[]),
            strings(&["localhost", "127.0.0.1", "10.0.0.5"])
        );
        assert_eq!(
            allowed_hosts(&defaults, "127.0.0.1", &[]),
            strings(&["localhost", "127.0.0.1"])
        );
    }

    #[test]
    fn allowed_hosts_never_treat_an_unspecified_bind_as_a_host() {
        let defaults = strings(&["localhost"]);
        for bind in ["0.0.0.0", "::", "[::]"] {
            assert_eq!(
                allowed_hosts(&defaults, bind, &strings(&["svc.example.com"])),
                strings(&["localhost", "svc.example.com"]),
                "bind {bind}"
            );
        }
    }

    #[test]
    fn tool_arguments_reject_unknown_keys() {
        let error = serde_json::from_value::<ApplyArgs>(json!({"path": "b", "write": true}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("unknown field `write`"), "{error}");
    }

    #[test]
    fn scalar_defaults_are_concrete_not_nullable() {
        let args: DuckdbExportArgs = serde_json::from_value(json!({"path": "b"})).unwrap();
        assert_eq!(
            serde_json::to_value(args).unwrap(),
            json!({"path": "b", "database": "knowledge.duckdb", "schema": "okf", "overwrite": false})
        );
    }

    #[test]
    fn path_arguments_keep_a_string_schema() {
        let schema = serde_json::to_value(schemars::schema_for!(DuckdbExportArgs)).unwrap();
        for field in ["path", "database"] {
            assert_eq!(schema["properties"][field]["type"], "string", "{field}");
        }
        let check = serde_json::to_value(schemars::schema_for!(CheckArgs)).unwrap();
        assert_eq!(
            check["properties"]["relational_schema"]["type"],
            json!(["string", "null"])
        );
    }

    #[test]
    fn a_missing_bundle_is_a_typed_graph_error() {
        assert!(matches!(
            commands::graph(Path::new("/definitely/not/a/bundle"), &[]),
            Err(LoadError::Root { .. })
        ));
    }

    #[test]
    fn bridge_errors_render_only_at_the_tool_boundary() {
        let error = BridgeError::Raised("ValueError: bad bundle".into());
        let result = tool_error(&error);
        assert_eq!(result.is_error, Some(true));
        assert_eq!(
            serde_json::to_value(&result.content).unwrap()[0]["text"],
            "ValueError: bad bundle"
        );
    }

    #[test]
    fn bridge_error_keeps_the_exception_line() {
        let stderr = "Traceback (most recent call last):\n  ...\nValueError: bad bundle\n\n";
        assert_eq!(bridge_error(stderr), "ValueError: bad bundle");
    }
}
