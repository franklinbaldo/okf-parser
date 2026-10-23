//! Lightweight development facade for the native OKF engine.
//!
//! The public `okf-parser` binary requires the `full` feature and therefore
//! keeps its SQL and MCP surface unchanged. This library target deliberately
//! remains available with `--no-default-features`, so engine work does not
//! compile DuckDB, RMCP, Axum, Tokio, or their transitive dependencies.

pub use okf_engine as engine;
