"""Expose the commands the native binary has not taken over yet, through Cyclopts.

The binary (``rust-core/src/main.rs``) answers every command but ``packs``
and ``add-pack`` itself and passes those command lines here (RFC 0024).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import version as _package_version

from cyclopts import App

from okf_parser.type_packs import install_type_pack, list_type_packs

type JsonPayload = dict[str, object]


@dataclass(frozen=True, slots=True)
class CliResult[PayloadT]:
    """A JSON payload or plain text string and its intended process exit code."""

    payload: PayloadT
    exit_code: int = 0


def _render_cli_result(result: object) -> None:
    """Render stable JSON/text and preserve command-specific exit status."""
    if not isinstance(result, CliResult):
        return
    if isinstance(result.payload, str):
        text = result.payload
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        sys.stdout.write(
            json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    if result.exit_code:
        raise SystemExit(result.exit_code)


app = App(
    name="okf-parser",
    help="Validate, query and edit OKF bundles.",
    version=_package_version("okf-parser"),
    result_action=_render_cli_result,
)


@app.command
def packs() -> CliResult[JsonPayload]:
    """List opt-in OKF type packs registered by installed package metadata."""
    return CliResult({"packs": list_type_packs()})


@app.command(name="add-pack")
def add_pack(
    name: str,
    path: str = ".",
    *,
    write: bool = False,
) -> CliResult[JsonPayload]:
    """Preview or install an opt-in type pack into an ordinary OKF bundle."""
    payload = install_type_pack(name, path, write=write)
    return CliResult(payload, exit_code=1 if payload["collisions"] else 0)


def main() -> None:
    """Run the Cyclopts command-line application."""
    app()


if __name__ == "__main__":
    main()
