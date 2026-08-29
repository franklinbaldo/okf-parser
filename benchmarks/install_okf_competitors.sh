#!/usr/bin/env bash
set -euo pipefail

root=${1:?destination directory required}
mkdir -p "$root/bin"

okfcli_version=$(go list -m -f '{{.Version}}' github.com/okfcli/okf@latest)
skosovsky_version=$(go list -m -f '{{.Version}}' github.com/skosovsky/okf@latest)

GOBIN="$root/okfcli" go install github.com/okfcli/okf/cmd/okf@latest
cp "$root/okfcli/okf" "$root/bin/okfcli-bench"

GOBIN="$root/skosovsky" go install github.com/skosovsky/okf/cmd/okf@latest
cp "$root/skosovsky/okf" "$root/bin/skosovsky-okf-bench"

uv venv "$root/okf-generator"
uv pip install --python "$root/okf-generator/bin/python" okf-generator
cp "$root/okf-generator/bin/okf" "$root/bin/okf-generator-bench"
generator_version=$("$root/okf-generator/bin/python" -c 'import importlib.metadata; print(importlib.metadata.version("okf-generator"))')

cat >"$root/versions.env" <<EOF
export OKFCLI_PROVENANCE=github.com/okfcli/okf@${okfcli_version}
export SKOSOVSKY_OKF_PROVENANCE=github.com/skosovsky/okf@${skosovsky_version}
export OKF_GENERATOR_PROVENANCE=pypi:okf-generator==${generator_version}
EOF

"$root/bin/okfcli-bench" version || true
"$root/bin/skosovsky-okf-bench" version || true
"$root/bin/okf-generator-bench" --version || true
cat "$root/versions.env"
