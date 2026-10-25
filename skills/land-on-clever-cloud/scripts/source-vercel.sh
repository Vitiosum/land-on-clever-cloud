#!/usr/bin/env bash
# source-vercel.sh — the ONLY way the skill reads a Vercel source (read-only, values never shown).
#
# Usage:
#   bash source-vercel.sh names  [--environment production] [--cwd DIR] [--project NAME]
#       variable NAMES only (vercel env pull to a private temp file, names printed, file deleted)
#   bash source-vercel.sh secret NAME --out FILE [--environment production] [--cwd DIR]
#       keep only NAME (e.g. BLOB_READ_WRITE_TOKEN) in FILE (mode 600, NAME=value); the full
#       pulled file is deleted; the value is never printed
#   bash source-vercel.sh blobs --token-file FILE --out LISTING.json [--prefix P] [--cwd DIR]
#       `vercel blob list --json`, all pages, token passed in the child's environment only;
#       LISTING.json ({"blobs": [...]}) is the --source of migrate-files.py
#
# Never run `vercel env ls` (it prints a truncated prefix of Config values) nor `vercel env pull`
# yourself (it writes ALL variables): use this script. Nothing is written on Vercel.
# Exit: 0 ok, 2 usage, 3 variable absent, 4 vercel failed.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
case "${1:-}" in ""|-h|--help) loc_help "$0"; exit 0 ;; esac
command -v vercel >/dev/null 2>&1 || loc_die "the Vercel CLI is not installed (the developer installs it and runs vercel login / vercel link)"
exec python3 -I "$LOC_SCRIPTS_DIR/lib/source_vercel.py" "$@"
