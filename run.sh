#!/usr/bin/env bash
# Entry point for the job-search pipeline.
#
# Two jobs: make sure the virtualenv exists and has the local package installed,
# then hand off to scrape.py with whatever flags were passed through.
#
# Usage:
#   ./run.sh                      # scrape using the profile in resume.md
#   ./run.sh --dry-run            # print the derived queries, scrape nothing
#   ./run.sh --site indeed,linkedin --results 50
set -euo pipefail

# Run from the repo root regardless of where the script was invoked, so the
# relative paths below (resume.md, saved/, scrape.py) always resolve.
cd "$(dirname "$0")"

PY=".venv/bin/python"

# Bootstrap on demand: create the venv and install this package in editable mode
# if either is missing. Editable (-e) means edits to jobspy/ and scrape.py take
# effect without reinstalling.
if [[ ! -x "$PY" ]] || ! "$PY" -c "import jobspy" >/dev/null 2>&1; then
  echo ">> setting up .venv"
  uv venv --clear
  uv pip install -e .
fi

# exec replaces this shell with the python process, so signals and the exit code
# pass straight through.
exec "$PY" scrape.py "$@"
