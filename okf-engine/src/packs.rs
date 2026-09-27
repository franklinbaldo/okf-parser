//! Opt-in type packs: ordinary OKF specification files installed into a bundle.
//!
//! A pack is a directory holding a `pack.json` manifest and the files it
//! lists. The packs shipped with okf-parser are embedded in the binary; any
//! other pack is a local directory named by path. Installing never replaces
//! authored work: a file that already exists with different content is a
//! collision, and one collision cancels the whole write.

use std::fmt;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

use include_dir::{Dir, include_dir};
use serde::{Deserialize, Serialize};

use crate::write::create_exclusive;

static BUILTIN: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/packs");

const MANIFEST: &str = "pack.json";

/// What `pack.json` declares about a pack.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub name: String,
    pub version: String,
    pub description: String,
    pub types: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub standard: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub standard_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub standard_schema: Option<String>,
    /// The pack-relative files and directories it installs; every file but
    /// the manifest when absent.
    #[serde(default, skip_serializing)]
    pub files: Option<Vec<String>>,
}

/// One UTF-8 file a pack installs, at its bundle-relative path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackFile {
    pub path: String,
    pub content: String,
}

/// A manifest and the files it installs, in path order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pack {
    pub manifest: Manifest,
    pub files: Vec<PackFile>,
}

/// A pack's JSON summary: its manifest plus the paths it installs.
#[derive(Debug, Clone, Serialize)]
pub struct PackSummary<'a> {
    #[serde(flatten)]
    pub manifest: &'a Manifest,
    pub files: Vec<&'a str>,
}

impl Pack {
    pub fn summary(&self) -> PackSummary<'_> {
        PackSummary {
            manifest: &self.manifest,
            files: self.files.iter().map(|file| file.path.as_str()).collect(),
        }
    }
}

/// Why a pack could not be read or installed.
#[derive(Debug)]
pub enum PackError {
    /// No embedded pack has this name, and it is not a directory.
    Unknown {
        name: String,
        available: Vec<String>,
    },
    /// The pack's `pack.json` is missing or not a valid manifest.
    Manifest {
        pack: String,
        detail: String,
    },
    /// A path the manifest lists, or a file in the pack, is unusable.
    File {
        pack: String,
        detail: String,
    },
    /// A pack path would land outside the destination.
    Escape {
        path: String,
    },
    /// The destination exists and is not a directory.
    Destination {
        path: PathBuf,
    },
    Io(io::Error),
}

impl PackError {
    /// The protocol error kind: the caller's fault, or the filesystem's.
    pub const fn is_request(&self) -> bool {
        !matches!(self, Self::Io(_))
    }
}

impl fmt::Display for PackError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown { name, available } => {
                let available = if available.is_empty() {
                    "none".to_owned()
                } else {
                    available.join(", ")
                };
                write!(
                    f,
                    "unknown type pack {name:?}; available packs: {available}, or a path to a pack directory"
                )
            }
            Self::Manifest { pack, detail } => {
                write!(f, "type pack {pack}: invalid {MANIFEST}: {detail}")
            }
            Self::File { pack, detail } => write!(f, "type pack {pack}: {detail}"),
            Self::Escape { path } => {
                write!(f, "pack file path must be relative and contained: {path:?}")
            }
            Self::Destination { path } => {
                write!(f, "pack destination is not a directory: {}", path.display())
            }
            Self::Io(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for PackError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            _ => None,
        }
    }
}

impl From<io::Error> for PackError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

/// A pack-relative path with only normal components, as a `/`-joined string.
fn relative_path(path: &str) -> Option<String> {
    let parts: Vec<&str> = Path::new(path)
        .components()
        .map(|component| match component {
            Component::Normal(part) => part.to_str(),
            _ => None,
        })
        .collect::<Option<_>>()?;
    (!parts.is_empty()).then(|| parts.join("/"))
}

/// Whether `path` is one of `selected` or lies below one of them.
fn is_selected(path: &str, selected: &[String]) -> bool {
    selected.iter().any(|entry| {
        path == entry
            || path
                .strip_prefix(entry.as_str())
                .is_some_and(|rest| rest.starts_with('/'))
    })
}

/// Build a pack from its manifest text and every file it holds.
fn assemble(label: &str, manifest: &str, files: Vec<PackFile>) -> Result<Pack, PackError> {
    let manifest: Manifest =
        serde_json::from_str(manifest).map_err(|error| PackError::Manifest {
            pack: label.to_owned(),
            detail: error.to_string(),
        })?;
    let mut files: Vec<PackFile> = files
        .into_iter()
        .filter(|file| file.path != MANIFEST)
        .collect();
    if let Some(selected) = &manifest.files {
        let selected: Vec<String> = selected
            .iter()
            .map(|entry| {
                relative_path(entry).ok_or_else(|| PackError::Manifest {
                    pack: label.to_owned(),
                    detail: format!("files entry must be a contained relative path: {entry:?}"),
                })
            })
            .collect::<Result<_, _>>()?;
        if let Some(missing) = selected.iter().find(|entry| {
            !files
                .iter()
                .any(|file| is_selected(&file.path, &[(*entry).clone()]))
        }) {
            return Err(PackError::Manifest {
                pack: label.to_owned(),
                detail: format!("files entry {missing:?} matches nothing in the pack"),
            });
        }
        files.retain(|file| is_selected(&file.path, &selected));
    }
    files.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(Pack { manifest, files })
}

fn embedded_files(dir: &Dir<'_>, root: &Path, label: &str) -> Result<Vec<PackFile>, PackError> {
    let mut files = Vec::new();
    for file in dir.files() {
        let path = file
            .path()
            .strip_prefix(root)
            .ok()
            .and_then(|path| path.to_str())
            .map(|path| path.replace('\\', "/"))
            .unwrap_or_default();
        let content = file.contents_utf8().ok_or_else(|| PackError::File {
            pack: label.to_owned(),
            detail: format!("{path} is not UTF-8"),
        })?;
        files.push(PackFile {
            path,
            content: content.to_owned(),
        });
    }
    for child in dir.dirs() {
        files.extend(embedded_files(child, root, label)?);
    }
    Ok(files)
}

/// Every pack embedded in the binary, in name order.
pub fn builtin() -> Result<Vec<Pack>, PackError> {
    let mut packs = BUILTIN
        .dirs()
        .map(|dir| {
            let label = dir.path().display().to_string();
            let manifest = dir
                .get_file(dir.path().join(MANIFEST))
                .and_then(|file| file.contents_utf8())
                .ok_or_else(|| PackError::Manifest {
                    pack: label.clone(),
                    detail: "missing".to_owned(),
                })?;
            assemble(&label, manifest, embedded_files(dir, dir.path(), &label)?)
        })
        .collect::<Result<Vec<_>, _>>()?;
    packs.sort_by(|a, b| a.manifest.name.cmp(&b.manifest.name));
    Ok(packs)
}

/// Read a pack from a local directory. Symbolic links are refused, so the
/// pack is exactly the files a reviewer sees in it.
pub fn from_dir(root: &Path) -> Result<Pack, PackError> {
    let label = root.display().to_string();
    let manifest =
        fs::read_to_string(root.join(MANIFEST)).map_err(|error| PackError::Manifest {
            pack: label.clone(),
            detail: error.to_string(),
        })?;
    let mut files = Vec::new();
    for entry in walkdir::WalkDir::new(root)
        .follow_links(false)
        .sort_by_file_name()
    {
        let entry = entry.map_err(io::Error::from)?;
        let relative = entry
            .path()
            .strip_prefix(root)
            .ok()
            .and_then(|path| path.to_str())
            .map(|path| path.replace('\\', "/"))
            .unwrap_or_default();
        if entry.file_type().is_symlink() {
            return Err(PackError::File {
                pack: label,
                detail: format!("{relative} is a symbolic link"),
            });
        }
        if !entry.file_type().is_file() {
            continue;
        }
        let bytes = fs::read(entry.path())?;
        let content = String::from_utf8(bytes).map_err(|_| PackError::File {
            pack: label.clone(),
            detail: format!("{relative} is not UTF-8"),
        })?;
        files.push(PackFile {
            path: relative,
            content,
        });
    }
    assemble(&label, &manifest, files)
}

/// An embedded pack by name, or the pack directory at that path.
pub fn resolve(name: &str) -> Result<Pack, PackError> {
    let builtin = builtin()?;
    if let Some(pack) = builtin.iter().find(|pack| pack.manifest.name == name) {
        return Ok(pack.clone());
    }
    let path = Path::new(name);
    if path.is_dir() {
        return from_dir(path);
    }
    Err(PackError::Unknown {
        name: name.to_owned(),
        available: builtin.into_iter().map(|pack| pack.manifest.name).collect(),
    })
}

/// What installing a pack found, and with `write`, did.
#[derive(Debug, Clone, Serialize)]
pub struct InstallReport<'a> {
    pub pack: PackSummary<'a>,
    pub destination: String,
    pub write: bool,
    pub planned: Vec<String>,
    pub unchanged: Vec<String>,
    pub collisions: Vec<String>,
    pub written: Vec<String>,
}

/// `destination/path`, refusing a path or a symbolic link that leaves it.
fn safe_target(destination: &Path, path: &str) -> Result<PathBuf, PackError> {
    let escape = || PackError::Escape {
        path: path.to_owned(),
    };
    let relative = relative_path(path)
        .filter(|relative| relative == path)
        .ok_or_else(escape)?;
    let target = destination.join(&relative);
    // The deepest existing path below the destination must resolve inside
    // it; a dangling link resolves nowhere and is refused too.
    let root = canonical_or_self(destination);
    let mut probe = target.clone();
    while probe != destination && probe.starts_with(destination) {
        if probe.symlink_metadata().is_ok() {
            let inside = fs::canonicalize(&probe).is_ok_and(|resolved| resolved.starts_with(&root));
            return if inside { Ok(target) } else { Err(escape()) };
        }
        probe.pop();
    }
    Ok(target)
}

fn canonical_or_self(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Preview, and with `write` perform, copying a pack into `destination`.
///
/// Files already identical are left alone; any other existing file is a
/// collision, and a collision writes nothing. The write creates each new
/// file exclusively and removes what it created if a later file fails.
pub fn install<'a>(
    pack: &'a Pack,
    destination: &Path,
    write: bool,
) -> Result<InstallReport<'a>, PackError> {
    if destination.exists() && !destination.is_dir() {
        return Err(PackError::Destination {
            path: destination.to_path_buf(),
        });
    }
    let mut planned = Vec::new();
    let mut unchanged = Vec::new();
    let mut collisions = Vec::new();
    let mut targets = Vec::new();
    for file in &pack.files {
        let target = safe_target(destination, &file.path)?;
        match fs::symlink_metadata(&target) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                planned.push(file.path.clone());
                targets.push((target, file));
            }
            Err(error) => return Err(error.into()),
            Ok(metadata) if !metadata.is_file() => collisions.push(file.path.clone()),
            Ok(_) => match fs::read(&target) {
                Ok(existing) if existing == file.content.as_bytes() => {
                    unchanged.push(file.path.clone());
                }
                Ok(_) => collisions.push(file.path.clone()),
                Err(error) => return Err(error.into()),
            },
        }
    }

    let mut written = Vec::new();
    if write && collisions.is_empty() {
        let mut created: Vec<&Path> = Vec::new();
        let outcome = targets.iter().try_for_each(|(target, file)| {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            if create_exclusive(target, file.content.as_bytes())? {
                created.push(target);
                written.push(file.path.clone());
                Ok(())
            } else {
                Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!(
                        "{} was created while the pack was being installed",
                        file.path
                    ),
                ))
            }
        });
        if let Err(error) = outcome {
            for path in created {
                let _ = fs::remove_file(path);
            }
            return Err(error.into());
        }
    }

    Ok(InstallReport {
        pack: pack.summary(),
        destination: destination.display().to_string(),
        write,
        planned,
        unchanged,
        collisions,
        written,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "okf-packs-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn journalism_is_embedded_with_only_its_listed_files() {
        let pack = resolve("journalism").unwrap();
        assert_eq!(pack.manifest.types, ["Body", "NewsItem"]);
        let paths: Vec<&str> = pack.files.iter().map(|file| file.path.as_str()).collect();
        assert!(paths.contains(&"specs/newsitem.md"), "{paths:?}");
        assert!(paths.contains(&"ninjs-mapping.json"), "{paths:?}");
        assert!(
            !paths.iter().any(|path| path.starts_with("examples/")),
            "{paths:?}"
        );
        assert!(!paths.contains(&"pack.json") && !paths.contains(&"index.md"));
        let mut sorted = paths.clone();
        sorted.sort_unstable();
        assert_eq!(paths, sorted);
    }

    #[test]
    fn install_previews_writes_and_is_idempotent() {
        let pack = resolve("journalism").unwrap();
        let dest = temp_dir("install");
        let preview = install(&pack, &dest, false).unwrap();
        assert_eq!(preview.planned.len(), pack.files.len());
        assert!(preview.written.is_empty());
        let written = install(&pack, &dest, true).unwrap();
        assert_eq!(written.written, written.planned);
        let again = install(&pack, &dest, true).unwrap();
        assert!(again.planned.is_empty() && again.written.is_empty());
        assert_eq!(again.unchanged.len(), pack.files.len());
        fs::remove_dir_all(dest).unwrap();
    }

    #[test]
    fn a_collision_writes_nothing() {
        let pack = resolve("journalism").unwrap();
        let dest = temp_dir("collision");
        fs::create_dir_all(dest.join("specs")).unwrap();
        fs::write(dest.join("specs/newsitem.md"), "authored\n").unwrap();
        let report = install(&pack, &dest, true).unwrap();
        assert_eq!(report.collisions, ["specs/newsitem.md"]);
        assert!(report.written.is_empty());
        assert!(!dest.join("specs/body.md").exists());
        fs::remove_dir_all(dest).unwrap();
    }

    #[test]
    fn a_directory_pack_honors_its_manifest() {
        let dir = temp_dir("local");
        fs::create_dir_all(dir.join("specs")).unwrap();
        fs::write(
            dir.join(MANIFEST),
            r#"{"name":"local","version":"1","description":"d","types":["T"],"files":["specs"]}"#,
        )
        .unwrap();
        fs::write(
            dir.join("specs/t.md"),
            "---\ntype: ConceptSpecification\n---\n",
        )
        .unwrap();
        fs::write(dir.join("notes.md"), "not installed\n").unwrap();
        let pack = resolve(dir.to_str().unwrap()).unwrap();
        assert_eq!(pack.manifest.name, "local");
        assert_eq!(pack.files.len(), 1);
        assert_eq!(pack.files[0].path, "specs/t.md");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn manifests_reject_escaping_and_unmatched_entries() {
        for files in [r#"["../x"]"#, r#"["/etc"]"#, r#"["missing"]"#] {
            let manifest = format!(
                r#"{{"name":"x","version":"1","description":"d","types":[],"files":{files}}}"#
            );
            let error = assemble("x", &manifest, Vec::new()).unwrap_err();
            assert!(
                matches!(error, PackError::Manifest { .. }),
                "{files}: {error}"
            );
        }
        let unknown = assemble("x", r#"{"name":"x","extra":1}"#, Vec::new()).unwrap_err();
        assert!(matches!(unknown, PackError::Manifest { .. }));
    }

    #[test]
    fn an_unknown_name_lists_the_embedded_packs() {
        let error = resolve("no-such-pack").unwrap_err();
        assert!(error.to_string().contains("journalism"), "{error}");
        assert!(error.is_request());
    }

    #[test]
    fn targets_cannot_escape_the_destination() {
        let dest = temp_dir("escape");
        assert!(safe_target(&dest, "../x").is_err());
        assert!(safe_target(&dest, "a/./b").is_err());
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(std::env::temp_dir(), dest.join("out")).unwrap();
            assert!(matches!(
                safe_target(&dest, "out/file.md"),
                Err(PackError::Escape { .. })
            ));
        }
        assert!(safe_target(&dest, "specs/a.md").is_ok());
        assert!(safe_target(&dest.join("not-yet"), "specs/a.md").is_ok());
        fs::remove_dir_all(dest).unwrap();
    }
}
