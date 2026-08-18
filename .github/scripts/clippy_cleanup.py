from pathlib import Path

path = Path("okf-engine/src/engine.rs")
text = path.read_text(encoding="utf-8")

old_if = '''    if file.is_file() {
        if let Some(error) = builder.add(file) {
            return Err(error.to_string());
        }
    }
'''
new_if = '''    if file.is_file()
        && let Some(error) = builder.add(file)
    {
        return Err(error.to_string());
    }
'''
if text.count(old_if) != 1:
    raise SystemExit("unexpected exclusions() shape while applying Clippy cleanup")
text = text.replace(old_if, new_if)

old_optional = 'fn optional(text: &str) -> Result<(Option<Map<String, Value>>, &str), String> {\n'
new_optional = '''type OptionalFrontmatter<'a> = (Option<Map<String, Value>>, &'a str);
fn optional(text: &str) -> Result<OptionalFrontmatter<'_>, String> {
'''
if text.count(old_optional) != 1:
    raise SystemExit("unexpected optional() signature while applying Clippy cleanup")
text = text.replace(old_optional, new_optional)

path.write_text(text, encoding="utf-8")
