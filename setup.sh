#!/usr/bin/env bash
# One-command setup for the Data360 Analyst toolkit.
#
# Creates a local virtualenv, installs the package in editable mode, and prints
# the next command to run. No Salesforce org or auth required to get this far —
# `data360 demo` runs entirely against the bundled examples/demo-org/ snapshot.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
    echo "Creating virtualenv in $VENV ..."
    "$PYTHON" -m venv "$VENV"
fi

echo "Installing data360-analyst (editable) ..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e .

cat <<'EOF'

Setup complete.

Try the toolkit with no org or auth — runs the full pipeline on a bundled
synthetic snapshot and opens an HTML dashboard:

    .venv/bin/data360 demo

Activate the venv to drop the .venv/bin/ prefix:

    source .venv/bin/activate
    data360 demo

To analyze a live org, authenticate with the Salesforce CLI first:

    sf org login web --alias <alias>

then see the README for `data360 intake` and the other subcommands.
EOF
