#!/usr/bin/env bash
# provision.sh — create the POC resources of a plan, idempotently, with the imposed rules enforced.
#
# Usage:
#   bash provision.sh --plan PLAN.json --org ORG|personal [--repo DIR] [--second-org ORG2]
#                     [--without COMPONENT[,..]] [--allow-dev-plan] [--json] --dry-run
#   bash provision.sh --plan PLAN.json ... --approve TOKEN        (really creates)
#
#   --dry-run       print the exact ordered commands (and an approval token); runs nothing. With no complete price
#                   (grid unreachable, an answer with no usable price, or an item the grid does not price) it prints no
#                   price and no token, and --approve is refused: no yes on an unknown or incomplete price.
#                   Read-only checks (existing apps/add-ons) are done when the CLI is available.
#   --approve TOKEN run them. TOKEN comes from the dry run: a hash of the exact commands, the price
#                   written in plan.json (grid read by plan.py, with its time), the skill's scripts
#                   and rules, and the API-verified state of reused add-ons. It is a CONSISTENCY
#                   check (what runs is what was shown), NOT a proof that a human agreed: consent is
#                   the developer's explicit yes naming plan and price. Only then is --yes passed
#                   to `clever addon create`.
#   --org ORG       REQUIRED: the organisation that owns and pays (id or name), or `personal` for the
#                   personal space — never a silent default (in HDS: the org holding the HDS contract)
#   --max-wait S    max total wait per step on the API rate limit (default 300 s, with jitter)
#   --force-retry   try again although 2 runs were already blocked by the rate limit
#   --accept-reused-mismatch  reuse add-ons whose real plan/zone/encryption (API v4, or v2 for
#                   providers without v4 such as Cellar) DIFFER from the plan — only after the
#                   developer agrees; otherwise the run is refused
#   --accept-unverified-reuse  reuse add-ons whose plan/zone/encryption could NOT be read (not a
#                   mismatch) — only after the developer agrees; never claim 'encrypted' then
#   --second-org    HDS path only: second, non-HDS organisation (zone par) for components the
#                   plan placed there (internal doctrine, to be confirmed by Clever Cloud)
#   --without X     "go without X": skip these components
#   --allow-dev-plan accept DEV database plans (unencrypted, no backups) for a throwaway trial;
#                   always refused in HDS zones
#   --no-live       do not call the CLI for read-only checks (offline dry run; the selftest uses it). Dry runs only:
#                   it skips the price and the checks on existing apps and reused add-ons, so --approve is refused
#                   with it outside the selftest
#   --cache-dir DIR catalog cache (default $XDG_CACHE_HOME or ~/.cache; refused inside a git tree)
#   LOC_BACKOFF_SCALE (environment, selftest only) shortens the rate-limit waits; --max-wait still caps them
#
# Enforced: explicit --region on every create; Paris first (par / parhds); dedicated database
# plans always get --option encryption=true (encryption cannot be added later); DEV refused by
# default and always in HDS; no add-on outside the HDS zone in the HDS org; no NODE_ENV=production
# for Node; secrets never in a plain env set (env-safe.sh generate); never `clever env import`.
# Everything created is recorded in .clever-landing/ledger.tsv (ledger.sh), git-excluded.
# Commands are argv arrays run by python with shell=False; every repo-derived value is validated
# (names ^[a-z0-9][a-z0-9-]{0,62}$, env names ^[A-Z_][A-Z0-9_]*$, no control characters).
# Exit: 0 ok, 2 usage, 10 refused by a rule, 11 a command failed (stops at the first failure),
#       12 blocked by the Clever API (creation rate limit; reads OK): stop after 2 such runs, ask support.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

PLAN=""; REPO="."; DRY=0; APPROVE=""; EMIT="human"; PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --plan) loc_need "$@"; PLAN="$2"; shift 2 ;;
    --repo) loc_need "$@"; REPO="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --approve) loc_need "$@"; APPROVE="$2"; shift 2 ;;
    --json) EMIT="json"; shift ;;
    --org|--second-org|--without|--cache-dir|--max-wait) loc_need "$@"; PASS+=("$1" "$2"); shift 2 ;;
    --allow-dev-plan|--no-live|--force-retry|--accept-reused-mismatch|--accept-unverified-reuse) PASS+=("$1"); shift ;;
    *) loc_die "unknown option $1 (see --help)" ;;
  esac
done
[ -n "$PLAN" ] && [ -f "$PLAN" ] || loc_die "--plan PLAN.json is required (output of plan.py --out)"
[ "$DRY" -eq 1 ] && [ -n "$APPROVE" ] && loc_die "--dry-run and --approve are exclusive"
[ "$DRY" -eq 0 ] && [ -z "$APPROVE" ] && loc_die "choose --dry-run (show the commands) or --approve TOKEN (after the developer's explicit yes)"
loc_git_guard "$REPO"
PY="$LOC_SCRIPTS_DIR/lib/provision.py"

if [ "$DRY" -eq 1 ]; then
  python3 -I "$PY" --plan "$PLAN" --repo "$LOC_TOP" --scripts "$LOC_SCRIPTS_DIR" --emit "$EMIT" ${PASS[@]+"${PASS[@]}"}
  exit $?
fi

command -v clever >/dev/null 2>&1 || loc_die "clever-tools not installed"
clever profile >/dev/null 2>&1 || loc_die "not logged in: the developer runs 'clever login' in their own terminal"
cd "$LOC_TOP" || exit 2
bash "$LOC_SCRIPTS_DIR/ledger.sh" init >/dev/null || loc_die "cannot initialise the ledger"
# Execution happens in python: each command is an argv list run with shell=False (no eval, no sh -c):
# names and values from the scanned repository are never parsed by a shell.
exec python3 -I "$PY" --plan "$PLAN" --repo "$LOC_TOP" --scripts "$LOC_SCRIPTS_DIR" --execute --approve "$APPROVE" ${PASS[@]+"${PASS[@]}"}
