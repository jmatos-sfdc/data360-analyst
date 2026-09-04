#!/usr/bin/env python3
"""Render a validated Data360 provenance config into the packaged HTML shell."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from data360_analyst.provenance_config import ValidationResult, format_issues, validate_config


CONFIG_TOKEN = "__PROVENANCE_CONFIG__"
DEFAULT_TEMPLATE = (
    Path(__file__).parent.parent.parent
    / ".claude"
    / "skills"
    / "data360-provenance-report"
    / "assets"
    / "provenance-report.html"
)


class RenderError(ValueError):
    """Raised when a report cannot be rendered safely."""


def load_config(path: Path) -> Any:
    """Load a JSON or YAML provenance configuration."""
    text = path.read_text()
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    raise RenderError("config must use .json, .yaml, or .yml")


def normalize_config(config: dict[str, Any]) -> str:
    """Return deterministic, human-readable normalized JSON."""
    return json.dumps(
        config,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ) + "\n"


def embed_config(template: str, config: dict[str, Any]) -> str:
    """Embed validated config into the shell's non-executable JSON element."""
    if template.count(CONFIG_TOKEN) != 1:
        raise RenderError(f"template must contain exactly one {CONFIG_TOKEN} token")
    payload = json.dumps(
        config,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    # Phase 2 rejects this sequence recursively. Keep a renderer-level defense so
    # future validator changes cannot silently make application/json embedding unsafe.
    if "</script" in payload.lower():
        raise RenderError("serialized config contains a closing script sequence")
    return template.replace(CONFIG_TOKEN, payload)


def render_config(
    config: Any,
    *,
    template_path: Path = DEFAULT_TEMPLATE,
) -> tuple[str, ValidationResult]:
    """Validate and render a config without writing any files."""
    validation = validate_config(config)
    if not validation.valid:
        raise RenderError(format_issues(validation.errors))
    if not isinstance(config, dict):  # Narrow the type after validation for callers.
        raise RenderError("config must be an object")
    try:
        template = template_path.read_text()
    except OSError as exc:
        raise RenderError(f"could not read template: {exc}") from exc
    return embed_config(template, config), validation


def render_file(
    config_path: Path,
    output_path: Path,
    *,
    template_path: Path = DEFAULT_TEMPLATE,
    normalized_config_path: Path | None = None,
) -> ValidationResult:
    """Load, validate, render, and atomically write a report and optional config."""
    try:
        config = load_config(config_path)
        rendered, validation = render_config(config, template_path=template_path)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise RenderError(str(exc)) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(f".{output_path.name}.tmp")
    temp_normalized = None
    try:
        temp_output.write_text(rendered)
        if normalized_config_path is not None:
            normalized_config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_normalized = normalized_config_path.with_name(
                f".{normalized_config_path.name}.tmp"
            )
            temp_normalized.write_text(normalize_config(config))
        temp_output.replace(output_path)
        if temp_normalized is not None:
            temp_normalized.replace(normalized_config_path)
    except OSError:
        temp_output.unlink(missing_ok=True)
        if temp_normalized is not None:
            temp_normalized.unlink(missing_ok=True)
        raise
    return validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON or YAML config")
    parser.add_argument("--output", required=True, type=Path, help="Rendered HTML path")
    parser.add_argument(
        "--normalized-config",
        type=Path,
        help="Optional path for deterministic normalized JSON",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        validation = render_file(
            args.config,
            args.output,
            template_path=args.template,
            normalized_config_path=args.normalized_config,
        )
    except (RenderError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if validation.warnings:
        print(format_issues(validation.warnings), file=sys.stderr)
    print(f"WROTE: {args.output}")
    if args.normalized_config:
        print(f"WROTE: {args.normalized_config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
