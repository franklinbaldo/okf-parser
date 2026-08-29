#!/usr/bin/env bash
set -euo pipefail

operation=${1:?operation required}
root=${2:?root required}
value=${3:-}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

case "$operation" in
  show)
    cat -- "$root/$value.md"
    ;;
  type)
    grep -RIl --include='*.md' -- "^type: $value$" "$root" | LC_ALL=C sort
    ;;
  *)
    exec python3 "$script_dir/generic_agent_baseline.py" "$operation" "$root" "$value"
    ;;
esac
