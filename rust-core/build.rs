//! Let a binary linked against a prebuilt libduckdb find it beside itself.
//!
//! Without the `bundled` feature, an SQL-enabled build links the shared
//! library and records only the absolute directory it was downloaded to. A
//! wheel ships the library next to the executable (`.data/scripts/`), so the
//! loader must also look in the executable's own directory: `$ORIGIN` on
//! Linux, `@executable_path` on macOS. Windows searches that directory for
//! DLLs by default. Engine-only builds do not link DuckDB at all.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    if std::env::var_os("CARGO_FEATURE_SQL").is_none()
        || std::env::var_os("CARGO_FEATURE_BUNDLED").is_some()
    {
        return;
    }
    match std::env::var("CARGO_CFG_TARGET_OS").as_deref() {
        Ok("linux") => println!("cargo:rustc-link-arg-bins=-Wl,-rpath,$ORIGIN"),
        Ok("macos") => println!("cargo:rustc-link-arg-bins=-Wl,-rpath,@executable_path"),
        _ => {}
    }
}
