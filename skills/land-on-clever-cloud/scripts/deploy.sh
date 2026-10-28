#!/usr/bin/env bash
# deploy.sh — clever deploy for a POC app, with a deterministic non-fast-forward rule.
#
# Usage: bash deploy.sh --alias ALIAS [--repo DIR] [--plan .clever-landing/plan.json]
#                       [--branch BRANCH] [--same-commit-policy rebuild] [--force-without-backup] [--force TOKEN]
#
# Runs `clever deploy --alias ALIAS …` (returns at the end of the deployment, logs streamed and
# masked). If the push is refused as NON-FAST-FORWARD (the app's remote holds commits this branch
# lacks, e.g. an earlier attempt):
#   - it forces ONLY if the app's name starts with the plan's prefix AND the app is in the ledger
#     (created or reused by this skill); before forcing it backs the remote head up to a local
#     branch clever-backup/<alias>-<time> and prints it;
#   - otherwise it stops with a question (exit 13) that prints a token: on the developer's yes only,
#     re-run with --force <token> (it binds the app, its deployed head and the local commit: a wrong
#     or stale token is refused, exit 10); the remote head is still backed up first;
#   - without a possible backup it also stops, unless --force-without-backup (only after a yes).
# Exit: 0 deployed, 2 usage, 10 refused, 11 deploy failed, 13 a question for the developer.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
REPO="."; PLAN=""; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --repo) loc_need "$@"; REPO="$2"; shift 2 ;;
    --plan) loc_need "$@"; PLAN="$2"; shift 2 ;;
    --alias|--branch|--same-commit-policy|--force) loc_need "$@"; ARGS+=("$1" "$2"); shift 2 ;;
    --force-without-backup) ARGS+=("$1"); shift ;;
    *) loc_die "unknown option $1 (see --help)" ;;
  esac
done
loc_git_guard "$REPO"
[ -n "$PLAN" ] || PLAN="$(loc_landing_dir "$LOC_TOP")/plan.json"
command -v clever >/dev/null 2>&1 || loc_die "clever-tools not installed"
exec python3 -I "$LOC_SCRIPTS_DIR/lib/deploy.py" --repo "$LOC_TOP" --plan "$PLAN" ${ARGS[@]+"${ARGS[@]}"}
