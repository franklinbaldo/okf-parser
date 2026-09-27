//! Format every Markdown file below a directory and report churn: a
//! development aid for judging the canonical form against a real corpus.
use okf_engine::format::format_markdown;

fn main() {
    let root = std::env::args()
        .nth(1)
        .expect("usage: format_corpus DIR [--show]");
    if std::path::Path::new(&root).is_file() {
        let text = std::fs::read_to_string(&root).expect("read");
        print!("{}", format_markdown(&text).expect("format"));
        return;
    }
    let show = std::env::args().any(|a| a == "--show");
    let paths = okf_engine::discover(std::path::Path::new(&root), &[]).expect("discover");
    let (mut changed, mut refused, mut unstable) = (0, 0, 0);
    for path in &paths {
        let Ok(text) = std::fs::read_to_string(path) else {
            continue;
        };
        match format_markdown(&text) {
            Ok(out) if out != text => {
                changed += 1;
                if format_markdown(&out).as_deref() != Ok(out.as_str()) {
                    unstable += 1;
                    println!("UNSTABLE {}", path.display());
                }
                if show {
                    println!("=== {}", path.display());
                    let before: std::collections::HashSet<&str> = text.lines().collect();
                    for line in out.lines().filter(|l| !before.contains(l)).take(6) {
                        println!("+ {line}");
                    }
                }
            }
            Ok(_) => {}
            Err(error) => {
                refused += 1;
                println!("REFUSED {}: {error}", path.display());
            }
        }
    }
    println!(
        "files {} changed {changed} refused {refused} unstable {unstable}",
        paths.len()
    );
}
