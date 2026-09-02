#!/usr/bin/env bash
set -euo pipefail

harness="${1:?usage: install_harness.sh <harness-id>}"

install_ori() {
  curl -fsSL https://openrouter.ai/labs/ori/install.sh | bash
  printf '%s\n' "$HOME/.local/bin" >> "$GITHUB_PATH"
  export PATH="$HOME/.local/bin:$PATH"
  ori --version
}

case "$harness" in
  cline)
    npm install -g cline@3.0.60
    cline --version
    ;;
  kilo)
    npm install -g @kilocode/cli@7.5.9
    kilo --version
    ;;
  ori-claude)
    npm install -g @anthropic-ai/claude-code@2.1.257
    claude --version
    install_ori
    ;;
  ori-codex)
    npm install -g @openai/codex@0.152.0
    codex --version
    install_ori
    ;;
  *)
    echo "unknown harness: $harness" >&2
    exit 2
    ;;
esac
