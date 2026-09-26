//! The Python interpreter of the environment this binary was installed into.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

// The environment's interpreter, searched from the executable's own location.
// A sibling interpreter covers the ordinary layout, where installers place
// this executable in the environment's own scripts directory. Walking the
// remaining ancestors covers layouts that place it deeper, which packaging
// tools do when they relocate a binary; it costs nothing when the sibling
// is already there.
fn find_python(executable: &Path) -> Option<OsString> {
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

/// The interpreter to delegate to: `OKF_PYTHON` when set, else the environment's
/// own, else the one on `PATH`. The override serves a binary that lives outside
/// any Python environment, such as a `cargo build` output.
pub fn interpreter() -> std::io::Result<PathBuf> {
    if let Some(configured) = std::env::var_os("OKF_PYTHON") {
        return Ok(PathBuf::from(configured));
    }
    let executable = std::env::current_exe()?;
    let fallback = if cfg!(windows) { "python" } else { "python3" };
    Ok(find_python(&executable).map_or_else(|| PathBuf::from(fallback), PathBuf::from))
}
