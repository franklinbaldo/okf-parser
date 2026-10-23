//! OKF bundles as DuckDB tables (RFC 0024 phase 4).
//!
//! Everything here runs on the `duckdb` crate: the `okf-parser duckdb`
//! export, RFC 0006 declared types, the starter schemas of
//! `init --infer-schema`, and the relational checks of
//! `check --relational-schema`. The binary enables the `bundled` feature,
//! so no DuckDB installation is needed.

pub mod apply;
pub mod catalog;
pub mod declared;
pub mod export;
pub mod import;
pub mod infer;
pub mod query;
pub mod relational;
pub mod relations;
pub mod schema;
pub mod source;
pub mod typed;
