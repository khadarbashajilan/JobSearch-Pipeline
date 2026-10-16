#!/usr/bin/env bash
# Entry point for the job-search pipeline.
#
# Three jobs:
#   1. Make sure the virtualenv exists and the local package is installed.
#   2. Run the upgraded CLI. The original `./run.sh` invocation still works
#      and now means "search with default sites".
#   3. Forward any extra flags to the search subcommand.
#
# Usage:
#   ./run.sh                      # discover + rank jobs
#   ./run.sh --dry-run search     # print profile and derived queries
#   ./run.sh search --site indeed,linkedin --results 50
#   ./run.sh list --status new
#   ./run.sh status <job-id> applied
set -euo pipefail

cd "$(dirname "$0")"

PY=".venv/bin/python"

if [[ ! -x "$PY" ]] || ! "$PY" -c "import jobspy" >/dev/null 2>&1; then
  echo ">> setting up .venv"
  uv venv --clear
  uv pip install -e .
  uv pip install pytest pytest-mock responses
fi

# Default behaviour: if no subcommand is given, treat the call as a search
# so old `./run.sh --site indeed --results 50` invocations keep working.
if [[ $# -eq 0 ]]; then
  exec "$PY" -m jobsearch search "$@"
fi

# If the first argument is a recognised flag (starts with --), inject the
# `search` subcommand so v1 ergonomics are preserved.
first="$1"
if [[ "$first" == --* ]]; then
  exec "$PY" -m jobsearch search "$@"
fi

exec "$PY" -m jobsearch "$@"