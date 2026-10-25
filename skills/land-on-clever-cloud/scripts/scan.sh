#!/usr/bin/env bash
# scan.sh — read-only survey of a repository, exactly as `clever deploy` will see it.
#
# Usage: bash scan.sh [app-folder] [--json] [--out FILE]
#   app-folder   folder of the app (default: current directory); a sub-folder of a repo is fine
#   --json       print the machine-readable JSON on stdout (instead of the human summary)
#   --out FILE   also write the JSON to FILE (e.g. .clever-landing/scan.json)
#
# Guarantees: reads COMMITTED files only (git ls-tree / cat-file on HEAD), never the content
# of an untracked file (an untracked .env is reported by name only), no network, no write
# (except --out), every snippet masked. Refuses a repository whose toplevel is $HOME.
# Output: provenances (detected from rules/*.json), runtime roots, compose services,
# Procfile, app.json, env var NAMES (build-time vs runtime), hard-coded service hosts,
# secrets hygiene, Supabase/compose counters. Exit 0 ok, 2 usage, 3-5 git guard.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

APP="."; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --json) ARGS+=(--json); shift ;;
    --out) [ $# -ge 2 ] || loc_die "--out needs a file"; ARGS+=(--out "$2"); shift 2 ;;
    -*) loc_die "unknown option $1 (see --help)" ;;
    *) APP="$1"; shift ;;
  esac
done
command -v python3 >/dev/null 2>&1 || loc_die "python3 is required"
loc_git_guard "$APP"
exec python3 -I "$LOC_SCRIPTS_DIR/lib/scan.py" --top "$LOC_TOP" --prefix "$LOC_PREFIX" ${ARGS[@]+"${ARGS[@]}"}
