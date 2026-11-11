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

# Put `data360` on PATH so skills can call it from any project folder.
BIN="$HOME/.local/bin"
if ! command -v data360 >/dev/null 2>&1 || [ "$(command -v data360)" = "$BIN/data360" ]; then
    mkdir -p "$BIN"
    ln -sf "$PWD/$VENV/bin/data360" "$BIN/data360"
    echo "Linked data360 into $BIN (add it to PATH if it isn't already)."
fi

echo "Linking Claude Code skills into ~/.claude/skills ..."
"$VENV/bin/data360" install-skills

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

then open Claude Code in an empty folder for your project and ask:

    set up data360 analyst for this project, org <alias>

(or do it by hand: `data360 init-project . --org <alias>` then
`data360 intake --org <alias> --output-dir .`)
EOF
