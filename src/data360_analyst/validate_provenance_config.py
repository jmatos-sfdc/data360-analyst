#!/usr/bin/env python3
"""Validate a Data360 provenance report JSON or YAML configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from data360_analyst.provenance_config import format_issues, validate_config
from data360_analyst.render_provenance_report import load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Provenance report config")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    result = validate_config(config)
    if result.issues:
        print(format_issues(result.issues))
    if result.valid:
        print(f"VALID: {args.config}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
