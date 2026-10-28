#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Creates the Clever Cloud target of a Supabase migration, for the TRACK
# chosen in phase 0, from the SURVEY (not from the reference case).
#
#   TRACK=non-hds ./deploy-target.sh <prefix> <organisation> [survey.json]
#   TRACK=hds     ./deploy-target.sh <prefix> <organisation> [survey.json]
#   … [survey.json] --approve <token>
#
# DRY RUN BY DEFAULT: without --approve, nothing is created or changed. The
# script reads what exists (read-only calls), prints the plan (resources to
# create or to reuse, plans, zone, variables by NAME, sizing), the price read
# live from the public grid (<skill>/scripts/price.py), and a TOKEN. With
# --approve <token>, it does the same reads again, recomputes the token and
# runs only if it is identical.
#
# THE TOKEN IS A CONSISTENCY CHECK, NOT A PROOF OF HUMAN CONSENT: it ensures
# that what runs is exactly what was shown (plan, price, the skill's own code
# as hashed by <skill>/scripts/lib/approval.py — this script, migrate-storage.py,
# price.py… —, state of the reused resources, content of .env.target). CONSENT
# comes from an explicit answer of the developer that names the plan and the
# price; an agent never re-runs with --approve without that answer.
#
# <organisation> is REQUIRED, as an ID (orga_… ; user_… for the personal
# space): without --org, clever-tools creates in the default space, which is
# not necessarily the client's. List the IDs:
#   clever curl https://api.clever-cloud.com/v2/organisations
#
# Run after GATE 2 (architecture and budget approved). Paid add-ons are
# created with --yes, ONLY after --approve. Deploys nothing: it PROVISIONS and LINKS. The
# code goes out afterwards with `clever deploy` from the app folder. Run
# from that folder: `clever create` writes .clever.json there (kept out of git
# by .git/info/exclude), and the script reads its package.json.
#
# RESUME: each step first checks what exists (add-on with the same name in
# the organisation, app linked in .clever.json or with the same name, bucket
# present, add-on already linked) and skips it. After a stop, run the dry run
# again then --approve: execution resumes at the next step, with no duplicate.
#
# CLEVER RATE LIMIT: "You have performed that request too much". On app
# creations it lasted more than 8 h (observed on 2026-10-25), while
# `clever addon create` and reads went through. Hence the order: add-ons and
# buckets BEFORE the app. The script retries with a growing, bounded wait
# (MAX_WAIT seconds per command), then stops with code 75: run the dry run
# again later (the state has changed, so has the token) and get it approved
# again.
#
# SECRETS: the values of .env.target go on the command line of
# `clever env set`, for the duration of the call: clever-tools 5.2.0 has no
# standard input for values, so they are visible to `ps` on this machine
# during the call. Do not run on a shared machine. (Feature request to bring
# to Clever: let `clever env set` read the value from stdin — an option that
# does not exist yet.)
#
# FROM THE DEVELOPER, NEVER FROM THE REPOSITORY: a .env.target or .clever.json tracked by git is
# refused; .env.target must be mode 600; reserved names (PATH, LD_*, NODE_OPTIONS…) are refused;
# names that run a command on the app (CC_*HOOK*, CC_RUN_COMMAND, CC_WORKER_COMMAND*, CC_DOCKER*)
# get an ALERT line in the plan; a .clever.json entry without org_id is refused. A saved price
# grid (LOC_PRICE_GRID_FILE, tests only) is labelled OFFLINE and never approved (LOC_SELFTEST=1
# in the selftest only, honoured only with the selftest's fake clever on PATH).
#
# Exit codes: 0 target ready, or plan printed (dry run) · 1 usage,
# configuration or token refused · 2 a command failed, or price grid
# unavailable, or the price incomplete (an item or the autoscaling ceiling
# is not in the grid: no token) · 6 a variable's value starts with "-"
# (names only: clever-tools 5.2.0 would read it as options and lose it; set
# it in the Console) · 75 rate limit, resume later.
#
# Variables (all optional except TRACK and PG_PLAN):
#   ZONE          non-hds: par (default) | hds: par (default: the POC runs in par with SYNTHETIC
#                 data, on the HDS-ready rules, until the HDS contract is signed), parhds once
#                 HDS_CONTRACT=signed (the only HDS zone this skill proposes; a client who needs
#                 another HDS zone sets ZONE himself, with HDS_CONTRACT=signed too)
#   HDS_CONTRACT  hds track: `signed` when the client declares the HDS contract signed (the
#                 script cannot check it: [declared]); then the default zone is parhds
#   APP_TYPE      node (default) — type for `clever create --type`
#   PG_PLAN       REQUIRED, no default: the PostgreSQL plan the go named, the one
#                 plan.json priced (provision.addons, provider postgresql-addon,
#                 "plan"; screen 4 shows it). The script never picks one: a default
#                 would create a plan other than the one priced and approved. `dev`
#                 refused in HDS, and outside HDS only with ACCEPT_NO_ENCRYPTION=1
#                 (throwaway test).
#   PG_VERSION    major; default = the survey's, else 18. `dev` forces 18.
#   ENCRYPTION    yes (default) — --option encryption=true, dedicated plans only
#   CELLAR        yes | no ; default = yes if the survey has at least one bucket
#   BUCKET_PREFIX "<prefix>-" by default: Cellar bucket = BUCKET_PREFIX +
#                 Supabase bucket id (GLOBAL names); sets CELLAR_BUCKET_PREFIX
#   MAX_WAIT      900 (default) — maximum total wait on the rate limit
#   MIGRATE_STORAGE  path of migrate-storage.py (default: ../scripts/migrate-storage.py)
#   MIN_FLAVOR / MAX_FLAVOR / MIN_INST / MAX_INST / BUILD_FLAVOR
#                 nano / XS / 1 / 2 / S by default
#
# It does NOT create the containers of the Supabase stack: the managed
# PostgreSQL add-on opens by default neither superuser, nor role creation,
# nor logical replication. See modules/supabase/reference/clever-cloud.md §0.
#
# Portable bash 3.2 (macOS): no associative array, empty arrays guarded
# under `set -u`. No secret value is printed.
# ---------------------------------------------------------------------------
set -euo pipefail
umask 077                         # temporary files (CLI outputs) in 600

RATE_LIMIT_CODE=75
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$HERE/../../.." && pwd)"          # land-on-clever-cloud: shared price.py and approval.py
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
usage() { echo "usage: TRACK=hds|non-hds $0 <prefix> <organisation orga_…> [survey.json] [--approve <token>]   (--help)" >&2; exit 1; }
APPROVED=""; POS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --approve) [[ $# -ge 2 ]] || usage; APPROVED="$2"; shift 2 ;;
    -*)        usage ;;
    *)         POS+=("$1"); shift ;;
  esac
done
PREFIX="${POS[0]:-}"
ORG="${POS[1]:-}"
SURVEY="${POS[2]:-}"
[[ ${#POS[@]} -le 3 ]] || usage
[[ -z "$APPROVED" || "$APPROVED" =~ ^[0-9a-f]{12}$ ]] || { echo "token refused: 12 hexadecimal characters" >&2; exit 1; }
[[ -n "$PREFIX" && -n "$ORG" ]] || usage
# Validated inputs: they end up in Clever and S3 resource names.
RE_PREFIX='^[a-z0-9][a-z0-9-]{0,40}$'
RE_ORG='^(orga|user)_[0-9a-fA-F-]{8,64}$'
RE_VAR='^[A-Z_][A-Z0-9_]*$'
[[ "$PREFIX" =~ $RE_PREFIX ]] || { echo "prefix refused: lowercase letters, digits, '-' (1 to 41 chars)" >&2; exit 1; }
[[ "$ORG" =~ $RE_ORG ]] || { echo "organisation: an orga_… (or user_…) ID is required, not a name" >&2; exit 1; }
[[ -z "$SURVEY" || -f "$SURVEY" ]] || { echo "survey not found: $SURVEY" >&2; exit 1; }
command -v clever >/dev/null || { echo "clever-tools missing" >&2; exit 1; }
# Local state and secrets come from the developer, never from the repository: a .env.target or a
# .clever.json tracked by git (committed by the repo under analysis) is refused — a committed
# CC_PRE_RUN_HOOK would run on the POC app, a committed .clever.json would point the steps at
# another app of the organisation.
for f in .env.target .clever.json; do
  if [[ -e "$f" ]] && git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
    echo "REFUSED: $f is tracked by git: it must come from the developer, not from the repository (git rm --cached -- $f, then check its content)." >&2; exit 1
  fi
done
if [[ -f .env.target ]] && ! python3 -I -c 'import os, sys; sys.exit(0 if os.stat(sys.argv[1]).st_mode & 0o077 == 0 else 1)' .env.target; then
  echo "REFUSED: .env.target must be readable by you only: chmod 600 .env.target" >&2; exit 1
fi
command -v python3 >/dev/null || { echo "python3 missing" >&2; exit 1; }

APP="$PREFIX-app"
APP_TYPE="${APP_TYPE:-node}"
MAX_WAIT="${MAX_WAIT:-900}"

# ---------------------------------------------------------------------------
# attempt <step> <command…>: runs the command (stdout in $OUT, stderr in
# $ERRS); on the rate limit, retries after 30 s, 60 s, 120 s… while the total
# stays under MAX_WAIT, then stops with code 75. Any other failure: code 2.
# read_cmd <step> <command…>: same, then prints stdout.
# Inside a $(…) substitution, always `|| exit $?`: the code must propagate.
# ---------------------------------------------------------------------------
rate_limit_stop() {
  cat >&2 <<END

STOP (code $RATE_LIMIT_CODE) — Clever rate limit on: $1
  "You have performed that request too much" after ${MAX_WAIT} s of waiting.
  On app creations it lasted more than 8 h (2026-10-25);
  reads and often \`clever addon create\` go through meanwhile.
  Nothing is lost: later, run the dry run again (the steps already done
  show up as REUSE), get it approved, re-run with the
  new token. If the app is what blocks, add-ons and buckets
  already exist: the data migration (phase 5) and the acceptance checks
  against a local app plugged on the add-ons (credentials written by
  \`<skill>/scripts/env-safe.sh env-file --addon <id> --out <600 file>\`) can go on.
  Lasting block: ask Clever (support) for the value of the limit.
END
  exit "$RATE_LIMIT_CODE"
}
OUT="$(mktemp)"; ERRS="$(mktemp)"; PLAN="$(mktemp)"
# The exit code is kept explicitly (bash 3.2 would return the one of rm).
trap 'c=$?; rm -f "$OUT" "$ERRS" "$PLAN"; exit $c' EXIT
attempt() {
  local step="$1" wait=30 total=0 code
  shift
  while :; do
    set +e; "$@" >"$OUT" 2>"$ERRS"; code=$?; set -e
    if grep -qi "performed that request too much" "$OUT" "$ERRS"; then
      if (( total + wait > MAX_WAIT )); then rate_limit_stop "$step"; fi
      echo "    rate limit ($step): retry in ${wait} s (total ${total} s / ${MAX_WAIT} s)" >&2
      sleep "$wait"; total=$((total + wait)); wait=$((wait * 2))
      continue
    fi
    if [[ $code -ne 0 ]]; then
      cat "$OUT" "$ERRS" | sed 's/^/    | /' >&2
      echo "FAILED (code 2): $step. Fix, then re-run the same command (resumes at this step)." >&2
      exit 2
    fi
    return 0
  done
}
read_cmd() { attempt "$@"; cat "$OUT"; }
show() { cat "$OUT" "$ERRS" | grep -v -e 'git add' -e 'git commit' | sed 's/^/    /' || true; }

# JSON queries. addon <name> -> "id region plan"; linked_app <alias> -> "id org"
# (.clever.json); named_app <name> -> "id zone"; linked <addon-id> -> code 0 if
# linked, 1 otherwise; version / buckets <survey.json>. Code 3: unexpected JSON.
pyjson() {
  python3 -I -c '
import json, re, sys
mode, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
def items():
    try:
        d = json.loads(sys.stdin.read())
    except ValueError:
        d = None
    if not isinstance(d, list):
        sys.exit(3)
    return [x for x in d if isinstance(x, dict)]
if mode == "addon":
    for a in items():
        if a.get("name") == arg:
            print(a.get("addonId") or a.get("id"), a.get("region") or "?", a.get("planSlug") or "?"); break
elif mode == "linked_app":
    try:
        d = json.load(open(".clever.json"))
    except (OSError, ValueError):
        d = {}
    for a in d.get("apps", []):
        if a.get("alias") == arg:
            print(a.get("app_id"), a.get("org_id") or "?"); break
elif mode == "named_app":
    for a in items():
        if a.get("name") == arg:
            print(a.get("id"), a.get("zone") or "?"); break
elif mode == "linked":
    l = items()
    sys.exit(0 if arg in {a.get("id") for a in l} | {a.get("realId") for a in l} else 1)
else:
    r = json.load(open(arg))
    if mode == "version":
        v = (r.get("server") or {}).get("version") or ""
        m = re.match(r"(\d+)", str(v)); print(m.group(1) if m else "")
    elif mode == "buckets":
        # Ids read from the client database: untrusted input, validated.
        ids = [str(b.get("id") or b.get("name") or "") for b in (r.get("buckets") or [])]
        bad = [i for i in ids if not re.fullmatch(r"[A-Za-z0-9._-]{1,63}", i)]
        if bad:
            sys.exit("bucket ids refused (characters outside [A-Za-z0-9._-]): %d" % len(bad))
        print(" ".join(ids))
' "$@"
}
survey() { if [[ -n "$SURVEY" ]]; then pyjson "$1" "$SURVEY"; fi; }

# ---------------------------------------------------------------------------
# 1. TRACK AND ZONE — decided in phase 0, applied here.
#
# Paris first, on both tracks (Clever Cloud recommendation: best
# performance, Cellar in particular).
# `clever create` AND `clever addon create` default to `par`: the zone is
# therefore passed EXPLICITLY to every command. `par` is not HDS; a resource
# created in the wrong zone is fixed by a migration, with downtime, and it is
# outside the certified scope in the meantime.
# ---------------------------------------------------------------------------
TRACK="${TRACK:-}"
RE_ZONE='^[a-z0-9][a-z0-9-]{1,30}$'
[[ -z "${ZONE:-}" || "$ZONE" =~ $RE_ZONE ]] || { echo "zone refused: lowercase letters, digits, '-' (a Clever zone id: par, parhds…)" >&2; exit 1; }
case "$TRACK" in
  non-hds) ZONE="${ZONE:-par}"
           case "$ZONE" in
             parhds|rbxhds|grahds) echo "HDS zone $ZONE on the non-HDS track: inconsistent, choose TRACK=hds." >&2; exit 1 ;;
             par) ;;
             *) echo "Zone $ZONE: outside Paris. Justify with a client reason (par is recommended)." >&2 ;;
           esac ;;
  hds)     HDS_CONTRACT="${HDS_CONTRACT:-}"
           [[ -z "$HDS_CONTRACT" || "$HDS_CONTRACT" == "signed" ]] || { echo "HDS_CONTRACT=signed, or unset (contract not signed yet)." >&2; exit 1; }
           if [[ "$HDS_CONTRACT" == "signed" ]]; then ZONE="${ZONE:-parhds}"; else ZONE="${ZONE:-par}"; fi
           case "$ZONE" in
             par) ;;
             parhds) ;;
             rbxhds|grahds) echo "Zone $ZONE: never proposed by this skill (parhds is); kept only because you set ZONE yourself." >&2 ;;
             *) echo "Zone $ZONE refused on the HDS track: par (the POC, synthetic data) or, with HDS_CONTRACT=signed, parhds." >&2; exit 1 ;;
           esac
           if [[ "$ZONE" != "par" && "$HDS_CONTRACT" != "signed" ]]; then
             echo "Zone $ZONE refused: an HDS zone needs the HDS contract signed with Clever Cloud (declare it with HDS_CONTRACT=signed). Until then the POC runs in par with synthetic data." >&2; exit 1
           fi
           cat <<'END'
HDS PREREQUISITES — to have BEFORE real health data:
  - HDS contract signed with Clever Cloud (the zone alone is not enough);
  - dedicated plans and encryption at rest at creation;
  - certified scope service by service: to confirm in the contract.
END
           if [[ "$ZONE" == "par" ]]; then
             echo "HDS-READY POC IN par: SYNTHETIC DATA ONLY until the HDS contract is signed (then parhds, with HDS_CONTRACT=signed): dedicated encrypted plans, only services present in parhds."
           else
             echo "HDS contract declared signed [declared]: the skill cannot check it. Synthetic data in the POC all the same; real data stays outside the skill."
           fi
           ;;
  *) echo "TRACK=hds or TRACK=non-hds (question 1 of phase 0)." >&2; exit 1 ;;
esac
echo "Track $TRACK, zone $ZONE, organisation $ORG"

# A resource of this prefix found in another zone: never linked. A run resumed from before the
# POC moved to par (TRACK=hds, resources in parhds) needs the contract declared, not a deletion.
zone_refused() {   # zone_refused <resource> <its zone>
  local way="delete it or change the prefix"
  if [[ "$2" == "parhds" && "$TRACK" == "hds" && "${HDS_CONTRACT:-}" != "signed" ]]; then
    way="if the HDS contract is signed, declare it with HDS_CONTRACT=signed (the zone becomes parhds and $1 is reused); otherwise delete it or change the prefix"
  fi
  echo "REFUSED: $1 exists in zone $2, not $ZONE. Do not link: $way." >&2; exit 1
}
# A value that starts with "-": clever-tools 5.2.0 (cliparse 0.5.0, then minimist) reads
# `clever env set NAME -abc` as options; the value is lost, or becomes `true`, and nothing
# says so. Refused, names only (as <skill>/scripts/env-safe.sh push/copy, exit 6).
dash_refused() {   # dash_refused <names…>
  echo "REFUSED (names): $* start(s) with '-': the argument parser of clever-tools 5.2.0 reads such a value as options and would lose it or set \`true\`; set it in the Console." >&2; exit 6
}

# Availability of an add-on in the zone: read live, not assumed.
addon_available() {   # addon_available <provider> [zone, default $ZONE] — called outside any `if`: set -e and codes 2/75 apply
  local catalog zones want="${2:-$ZONE}"
  catalog="$(read_cmd "catalog $1" clever addon providers show "$1")" || exit $?
  zones=$(printf '%s\n' "$catalog" | grep -m1 '^Available regions:' || true)
  zones=" $(echo "${zones#*:}" | tr -d ' ' | tr ',' ' ') "
  if [[ "$zones" == *" $want "* ]]; then AVAILABLE=yes; else AVAILABLE=no; fi
}

# ---------------------------------------------------------------------------
# 2. Database parameters, from the survey.
# ---------------------------------------------------------------------------
PG_PLAN="${PG_PLAN:-}"
if [[ -z "$PG_PLAN" ]]; then
  echo "PG_PLAN is required: the PostgreSQL plan the go named, the one plan.json priced (provision.addons, provider postgresql-addon, \"plan\"; screen 4 shows it): PG_PLAN=<that plan> TRACK=$TRACK $0 …. This script never picks one." >&2; exit 1
fi
[[ "$PG_PLAN" =~ ^[a-z0-9_]{1,30}$ ]] || { echo "PG_PLAN refused: a plan slug (lowercase letters, digits, '_')." >&2; exit 1; }
SOURCE_MAJOR="$(survey version)"
PG_VERSION="${PG_VERSION:-${SOURCE_MAJOR:-18}}"
ENCRYPTION="${ENCRYPTION:-yes}"

if [[ "$PG_PLAN" == "dev" && "${ACCEPT_NO_ENCRYPTION:-}" != "1" ]]; then
  echo "Plan dev refused by default: no encryption at rest, and encryption can only be added at creation (a migration would be needed later). For a throwaway test only: ACCEPT_NO_ENCRYPTION=1." >&2; exit 1
fi
if [[ "$PG_PLAN" == "dev" ]]; then
  [[ "$TRACK" == "hds" ]] && { echo "Plan dev refused in HDS (no backup, no encryption, no extension)." >&2; exit 1; }
  cat >&2 <<'END'
Plan dev: DEMO ONLY — 5 connections, 256 MB, no backup, no
extension (neither vector nor postgis), no encryption, PostgreSQL 18 forced.
END
  PG_VERSION=18; ENCRYPTION=no
fi
if [[ -n "$SOURCE_MAJOR" && "$SOURCE_MAJOR" != "$PG_VERSION" ]]; then
  echo "PostgreSQL version upgrade: source $SOURCE_MAJOR -> target $PG_VERSION. To test (extensions, plv8 absent in 16+)." >&2
fi
[[ "$TRACK" == "hds" && "$ENCRYPTION" != "yes" ]] && { echo "ENCRYPTION=yes is mandatory on the HDS track (choice of the skill)." >&2; exit 1; }

PG_OPTS=(--plan "$PG_PLAN" --region "$ZONE" --org "$ORG")
[[ "$PG_PLAN" != "dev" ]] && PG_OPTS+=(--addon-version "$PG_VERSION" --yes)
[[ "$ENCRYPTION" == "yes" ]] && PG_OPTS+=(--option encryption=true)

BUCKETS="$(survey buckets)" || exit 1
if [[ -z "${CELLAR:-}" ]]; then
  if [[ -n "$BUCKETS" ]]; then CELLAR=yes; else CELLAR=no; fi
fi
if [[ "$CELLAR" == "yes" ]]; then
  addon_available cellar-addon
  [[ "$AVAILABLE" == "yes" ]] || { echo "Cellar unavailable in $ZONE. Change zone or storage." >&2; exit 1; }
  if [[ "$TRACK" == "hds" && "$ZONE" == "par" ]]; then          # HDS-ready POC: only what parhds offers too
    addon_available cellar-addon parhds
    [[ "$AVAILABLE" == "yes" ]] || { echo "Cellar unavailable in parhds: the POC would not be HDS-ready. Change storage." >&2; exit 1; }
  fi
fi
BUCKET_PREFIX="${BUCKET_PREFIX:-$PREFIX-}"

# Idempotent add-on: present in the organisation -> zone checked, skipped.
existing_addon() {   # existing_addon <name> -> "id region plan" or empty
  local all code=0
  all="$(read_cmd "list of add-ons" clever addon list --org "$ORG" --format json)" || exit $?
  printf '%s' "$all" | pyjson addon "$1" || code=$?
  [[ $code -eq 0 ]] || { echo "FAILED (code 2): list of add-ons unreadable (unexpected JSON)." >&2; exit 2; }
}
ensure_addon() {    # ensure_addon <name> <provider> <options…> -> ADDON_ID
  local name="$1" provider="$2" line region
  shift 2
  line="$(existing_addon "$name")" || exit $?
  if [[ -z "$line" ]]; then
    attempt "creation of add-on $name" clever addon create "$provider" "$name" "$@"
    show
    line="$(existing_addon "$name")" || exit $?
    [[ -n "$line" ]] || { echo "FAILED (code 2): $name created but missing from the list; re-run." >&2; exit 2; }
  else
    echo "    already present ($line): skipped"
  fi
  ADDON_ID="${line%% *}"
  region="${line#* }"; region="${region%% *}"
  if [[ "$region" != "?" && "$region" != "$ZONE" ]]; then zone_refused "$name" "$region"; fi
}

# ---------------------------------------------------------------------------
# PLAN — read-only calls. Nothing is created or changed before --approve.
# ---------------------------------------------------------------------------
plan() { printf '%s\n' "$*" >> "$PLAN"; }
plan "track=$TRACK zone=$ZONE hds_contract=${HDS_CONTRACT:-not declared} organisation=$ORG prefix=$PREFIX"
L_PG="$(existing_addon "$PREFIX-pg")" || exit $?
if [[ -z "$L_PG" ]]; then
  plan "CREATE   add-on postgresql-addon $PREFIX-pg: plan $PG_PLAN, version $PG_VERSION, encryption $ENCRYPTION (paid: --yes)"
else
  r="${L_PG#* }"; r="${r%% *}"; [[ "$r" == "?" || "$r" == "$ZONE" ]] || zone_refused "$PREFIX-pg" "$r"
  plan "REUSE    add-on $PREFIX-pg: $L_PG"
fi
if [[ "$CELLAR" == "yes" ]]; then
  L_CE="$(existing_addon "$PREFIX-cellar")" || exit $?
  if [[ -z "$L_CE" ]]; then plan "CREATE   add-on cellar-addon $PREFIX-cellar: plan S (paid: --yes)"
  else
    r="${L_CE#* }"; r="${r%% *}"; [[ "$r" == "?" || "$r" == "$ZONE" ]] || zone_refused "$PREFIX-cellar" "$r"
    plan "REUSE    add-on $PREFIX-cellar: $L_CE"
  fi
  if [[ -n "$BUCKETS" ]]; then
    plan "BUCKETS  check or create (private): $(for b in $BUCKETS; do printf '%s%s ' "$BUCKET_PREFIX" "$b"; done)"
  fi
fi
P_LINKED="$(pyjson linked_app "$APP")"
if [[ -n "$P_LINKED" ]]; then
  [[ "${P_LINKED#* }" == "$ORG" ]] \
    || { echo "REFUSED: .clever.json links $APP to organisation ${P_LINKED#* } (\"?\" = no org_id), not $ORG: remove that entry or link the app again with clever link --org $ORG." >&2; exit 1; }
  plan "REUSE    app $APP linked in .clever.json: $P_LINKED"
else
  P_APPS="$(read_cmd "list of apps" clever curl "https://api.clever-cloud.com/v2/organisations/$ORG/applications")" || exit $?
  code=0; P_NAMED="$(printf '%s' "$P_APPS" | pyjson named_app "$APP")" || code=$?
  [[ $code -eq 0 ]] || { echo "FAILED (code 2): list of apps unreadable (unexpected JSON)." >&2; exit 2; }
  if [[ -n "$P_NAMED" ]]; then
    [[ "${P_NAMED#* }" == "?" || "${P_NAMED#* }" == "$ZONE" ]] || zone_refused "$APP" "${P_NAMED#* }"
    plan "LINK     existing app $APP: $P_NAMED"
  else plan "CREATE   app $APP: type $APP_TYPE"; fi
fi
plan "LINK     add-ons to $APP (if not linked yet)"
VAR_NAMES=""; DASHED=""
[[ "$CELLAR" == "yes" ]] && VAR_NAMES="CELLAR_BUCKET_PREFIX"
[[ "$CELLAR" == "yes" && "$BUCKET_PREFIX" == -* ]] && DASHED="CELLAR_BUCKET_PREFIX"
# Names that would change how the app or its runtime starts: refused (same list as env-safe.sh).
RE_RESERVED='^(PATH|HOME|SHELL|IFS|ENV|BASH_ENV|BASH_.*|LD_.*|DYLD_.*|NODE_OPTIONS|NODE_PATH|NPM_CONFIG_.*|PYTHON.*|PERL5?LIB|RUBYOPT|GIT_.*|CLEVER_.*|SSH_.*|TMPDIR|PS[0-9]|PROMPT_COMMAND|CDPATH)$'
# Names that RUN a command on the app (hooks, run/worker commands, Docker build): allowed, but
# shown with an alert line in the plan (value masked: read it in .env.target before approving).
RE_RUNS='^CC_(.*HOOK.*|RUN_COMMAND|WORKER_COMMAND.*|DOCKER.*|PRE_BUILD.*|POST_BUILD.*|PRE_RUN.*)$'
ALERTS=""
if [[ -f .env.target ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    k="${line%%=*}"
    [[ "$k" =~ $RE_VAR ]] || continue
    if [[ "$k" =~ $RE_RESERVED ]]; then
      echo "REFUSED: .env.target sets $k (reserved: it would alter the runtime of the app). Remove it." >&2; exit 1
    fi
    [[ "$k" =~ $RE_RUNS ]] && ALERTS="$ALERTS $k"
    v="${line#*=}"; v="${v%\"}"; v="${v#\"}"          # the value set_var would pass (never printed)
    [[ "$v" == -* ]] && DASHED="${DASHED:+$DASHED }$k"
    VAR_NAMES="$VAR_NAMES $k"
  done < .env.target
fi
if [[ "$APP_TYPE" == "node" && -f package.json ]] \
   && python3 -I -c 'import json,sys; sys.exit(0 if json.load(open("package.json")).get("scripts",{}).get("build") else 1)'; then
  VAR_NAMES="$VAR_NAMES CC_POST_BUILD_HOOK CC_NODE_DEV_DEPENDENCIES"
fi
[[ -z "$DASHED" ]] || dash_refused $DASHED
plan "VARIABLES (names only):${VAR_NAMES:+ $VAR_NAMES}"
for k in $ALERTS; do
  plan "ALERT    .env.target sets $k = *** : it RUNS a command on the app at build or start. Read its value in .env.target before approving."
done
MIN_FLAVOR="${MIN_FLAVOR:-nano}"; MAX_FLAVOR="${MAX_FLAVOR:-XS}"
MIN_INST="${MIN_INST:-1}";        MAX_INST="${MAX_INST:-2}"
BUILD_FLAVOR="${BUILD_FLAVOR:-S}"
plan "SCALE    $APP: $MIN_FLAVOR x$MIN_INST -> $MAX_FLAVOR x$MAX_INST, build $BUILD_FLAVOR"

# Live price. Without a grid, no token: an unknown price is not approved.
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
ITEMS=("apps:$(lower "$MIN_FLAVOR")x$MIN_INST" "apps:$(lower "$BUILD_FLAVOR")@build" "postgresql:$PG_PLAN")
[[ "$CELLAR" == "yes" ]] && ITEMS+=("cellar:storage")
PRICE_JSON="$(mktemp)"; CEILING_JSON="$(mktemp)"
trap 'c=$?; rm -f "$OUT" "$ERRS" "$PLAN" "$PRICE_JSON" "$CEILING_JSON"; exit $c' EXIT
code=0; python3 -I "$SKILL_ROOT/scripts/price.py" --zone "$ZONE" --json "${ITEMS[@]}" > "$PRICE_JSON" || code=$?
[[ $code -le 1 ]] || { echo "FAILED (code 2): price grid unavailable: no token without a price." >&2; exit 2; }
[[ $code -eq 0 ]] || { echo "FAILED (code 2): price incomplete, an item is NOT IN THE GRID (price.py exit 1): no token without a complete price. Confirm the item in the Console." >&2; exit 2; }
code=0; python3 -I "$SKILL_ROOT/scripts/price.py" --zone "$ZONE" --json "apps:$(lower "$MAX_FLAVOR")x$MAX_INST" > "$CEILING_JSON" || code=$?
[[ $code -eq 0 ]] || { echo "FAILED (code 2): the autoscaling ceiling is not priced (apps:$(lower "$MAX_FLAVOR")x$MAX_INST: price.py exit $code, not in the grid or the grid failed): no token without a complete price. Confirm the flavor in the Console." >&2; exit 2; }

# R37, R38 (e): in an HDS zone (HDS_CONTRACT=signed) the HDS price, from the same grid read — 1.4 × the resources (all in the
# HDS organisation here), per hour, 2 h and per build, + the fee per HDS organisation (pricing.py's constants) — goes into
# the PLAN, so the token binds it and the yes names it.
case "$ZONE" in
  parhds|rbxhds|grahds)
    HDS_LINE="$(python3 -I - "$SKILL_ROOT/scripts/lib" "$PRICE_JSON" "$ZONE" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1]); import pricing
q = json.load(open(sys.argv[2]))
run = build = 0.0
for l in q.get("lines") or []:
    if l.get("in_grid"):
        if l.get("build_only"):
            build += pricing.HDS_UPLIFT * float(l.get("eur_per_build") or 0)
        else:
            run += pricing.HDS_UPLIFT * float(l.get("eur_per_hour") or 0)
print("HDS price (zone %s, %g × the resources, all in the HDS organisation): %.4f EUR/h running · 2 h ≈ %.2f EUR · builds ≈ %.3f EUR each · "
      "+ €%d/month per HDS organisation — communicated by Clever Cloud on 2026-10-27, not on the public pricing page — confirm on your quote/contract"
      % (sys.argv[3], pricing.HDS_UPLIFT, run, run * 2, build, int(pricing.HDS_FEE)))
PY
)" || { echo "FAILED (code 2): the HDS price could not be computed: no token without it." >&2; exit 2; }
    plan "$HDS_LINE" ;;
esac

# Token: plan + price + content of .env.target.
# (approval.py adds the skill's code: scripts, rules, modules)
TOKEN="$(python3 -I "$SKILL_ROOT/scripts/lib/approval.py" token --file "$PLAN" --file "$PRICE_JSON" --file "$CEILING_JSON" --file .env.target)"

if [[ -n "${LOC_PRICE_GRID_FILE:-}" && -n "$APPROVED" ]] \
   && ! LOC_SELFTEST_WHAT="approval on an offline grid" python3 -I "$SKILL_ROOT/scripts/lib/common.py" selftest-mode; then
  echo "REFUSED: LOC_PRICE_GRID_FILE is set: the price comes from a saved file (OFFLINE GRID, tests only) and is never approved. Unset it and run the dry run again on the live grid." >&2
  exit 1
fi
if [[ -z "$APPROVED" ]]; then
  echo
  echo "PLAN (dry run: nothing was created or changed)"
  sed 's/^/  /' "$PLAN"
  echo
  python3 -I - "$PRICE_JSON" "$CEILING_JSON" <<'PY'
import json, sys
q, pl = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])) if open(sys.argv[2]).read().strip() else {}
if q.get("offline_grid"):
    print("PRICE (OFFLINE saved grid, TESTS ONLY — not a live price: %s, zone %s, EUR excl. VAT)" % (q["source"], q["zone"]))
else:
    print("PRICE (public grid, read live, zone %s, EUR excl. VAT)" % q["zone"])
for l in q["lines"]:
    if not l["in_grid"]:
        print("  %-22s NOT IN THE GRID: confirm in the Console" % l["item"])
    else:
        print("  %-22s %.4f EUR/h%s" % (l["item"], l["eur_per_hour"], "  (during builds)" if l["build_only"] else ""))
print("  running                %.4f EUR/h, ~%.2f EUR/month if never stopped" % (q["run_eur_per_hour"], q["run_eur_per_month_if_never_stopped"]))
if pl.get("lines"):
    if pl["lines"][0].get("in_grid"):
        print("  autoscaling ceiling    %.4f EUR/h for %s" % (pl["run_eur_per_hour"], pl["lines"][0]["item"]))
    else:
        print("  autoscaling ceiling    %s NOT IN THE GRID: confirm in the Console" % pl["lines"][0]["item"])
PY
  cat <<END

TOKEN: $TOKEN
  Show this plan and this price to the developer. Only an explicit answer
  that names the plan and the price (in an HDS zone: the HDS price) counts as consent; then re-run the same
  command with --approve $TOKEN. The token changes if the plan, the price,
  these scripts, .env.target or the state of the resources change.
END
  exit 0
fi
if [[ "$APPROVED" != "$TOKEN" ]]; then
  echo "REFUSED: token $APPROVED differs from the current plan ($TOKEN): plan, price, scripts, .env.target or state have changed. Run the dry run again and get it approved." >&2
  exit 1
fi
echo "Token $TOKEN verified: running the approved plan."

# ---------------------------------------------------------------------------
echo "==> 1/7  PostgreSQL add-on ($PG_PLAN, v$PG_VERSION, encryption=$ENCRYPTION)"
# Encryption at rest is chosen AT CREATION (--option encryption=true,
# dedicated plans). Afterwards, it is a migration done by support.
ensure_addon "$PREFIX-pg" postgresql-addon "${PG_OPTS[@]}"
PG_ID="$ADDON_ID"

echo "==> 2/7  Cellar add-on: $CELLAR (buckets in the survey: ${BUCKETS:-none, or no survey})"
CELLAR_ID=""
if [[ "$CELLAR" == "yes" ]]; then
  # Cellar has no encryption option in `providers show`: handle it on the
  # app side if the context requires it.
  ensure_addon "$PREFIX-cellar" cellar-addon --plan S --region "$ZONE" --org "$ORG" --yes
  CELLAR_ID="$ADDON_ID"
fi

echo "==> 3/7  Cellar buckets (global names: ${BUCKET_PREFIX}<bucket>)"
# Provisioning creates the add-on, NOT the bucket. Credentials read with
# `clever addon env` (no app) and passed to migrate-storage.py --buckets
# through the process environment, never printed nor kept on disk.
if [[ -n "$CELLAR_ID" && -n "$BUCKETS" ]]; then
  MIGRATOR="${MIGRATE_STORAGE:-$HERE/../scripts/migrate-storage.py}"
  [[ -f "$MIGRATOR" ]] || { echo "FAILED (code 2): $MIGRATOR not found (MIGRATE_STORAGE=...)." >&2; exit 2; }
  attempt "variables of the Cellar add-on" clever addon env "$CELLAR_ID" --format json
  code=0
  python3 -I - "$OUT" "$MIGRATOR" "$BUCKET_PREFIX" "$(echo "$BUCKETS" | tr ' ' ',')" <<'PY' || code=$?
import json, os, subprocess, sys
out, migrator, prefix, buckets = sys.argv[1:5]
try:
    d = json.load(open(out))
except ValueError:
    sys.exit("    output of `clever addon env` is not readable JSON: create the buckets with "
             "migrate-storage.py --buckets ... --create-buckets")
finally:
    open(out, "w").close()          # credentials wiped from the temporary file
if isinstance(d, list):
    d = {x.get("name"): x.get("value") for x in d if isinstance(x, dict)}
env = dict(os.environ)
for k in ("CELLAR_ADDON_HOST", "CELLAR_ADDON_KEY_ID", "CELLAR_ADDON_KEY_SECRET"):
    if not d.get(k):
        sys.exit(f"    {k} missing from `clever addon env`")
    env[k] = d[k]
r = subprocess.run([sys.executable, "-I", migrator, "--buckets", buckets, "--prefix", prefix, "--create-buckets"], env=env)
sys.exit(r.returncode)
PY
  if [[ $code -ne 0 ]]; then
    echo "FAILED (code 2): buckets. 403 = name taken by another account: a more distinctive BUCKET_PREFIX, then re-run." >&2
    exit 2
  fi
else
  echo "    nothing to create"
fi

echo "==> 4/7  App $APP ($APP_TYPE, zone $ZONE)"
LINKED="$(pyjson linked_app "$APP")"
if [[ -n "$LINKED" ]]; then
  echo "    already linked in .clever.json (${LINKED%% *}): skipped"
  [[ "${LINKED#* }" == "$ORG" ]] \
    || { echo "REFUSED: .clever.json links $APP to organisation ${LINKED#* } (\"?\" = no org_id), not $ORG." >&2; exit 1; }
else
  APPS="$(read_cmd "list of apps" clever curl "https://api.clever-cloud.com/v2/organisations/$ORG/applications")" || exit $?
  code=0; NAMED="$(printf '%s' "$APPS" | pyjson named_app "$APP")" || code=$?
  [[ $code -eq 0 ]] || { echo "FAILED (code 2): list of apps unreadable (unexpected JSON); nothing created." >&2; exit 2; }
  if [[ -n "$NAMED" ]]; then
    [[ "${NAMED#* }" == "?" || "${NAMED#* }" == "$ZONE" ]] || zone_refused "$APP" "${NAMED#* }"
    echo "    already exists (${NAMED%% *}): link only"
    attempt "link of $APP" clever link "${NAMED%% *}" --alias "$APP" --org "$ORG"
  else
    # Step most exposed to the rate limit (see the header).
    attempt "creation of app $APP" clever create --type "$APP_TYPE" "$APP" --alias "$APP" --region "$ZONE" --org "$ORG"
    show        # the "git add ." prompt of clever create is filtered out: ignore it
  fi
  LINKED="$(pyjson linked_app "$APP")"
  [[ -n "$LINKED" ]] || { echo "FAILED (code 2): $APP missing from .clever.json after creation or link." >&2; exit 2; }
fi
APP_ID="${LINKED%% *}"
# .clever.json: local link of the CLI, kept out of the repository.
if EXCLUDE="$(git rev-parse --git-path info/exclude 2>/dev/null)"; then
  grep -qx '.clever.json' "$EXCLUDE" 2>/dev/null || { mkdir -p "$(dirname "$EXCLUDE")"; echo '.clever.json' >> "$EXCLUDE"; }
fi

echo "==> 5/7  Linking the add-ons to $APP"
LINKS="$(read_cmd "linked add-ons" clever curl "https://api.clever-cloud.com/v2/organisations/$ORG/applications/$APP_ID/addons")" || exit $?
for ID in "$PG_ID" ${CELLAR_ID:+"$CELLAR_ID"}; do
  code=0; printf '%s' "$LINKS" | pyjson linked "$ID" || code=$?
  case $code in
    0) echo "    $ID already linked: skipped" ;;
    1) attempt "link of $ID" clever service link-addon "$ID" --alias "$APP"; echo "    $ID linked" ;;
    *) echo "FAILED (code 2): linked add-ons unreadable (unexpected JSON); check \`clever service --alias $APP\`." >&2; exit 2 ;;
  esac
done

echo "==> 6/7  Environment variables"
# The variables of linked add-ons are exposed automatically: do not copy
# them. ONE `clever env set` command per variable: `clever env import`
# ERASES the others, `clever env import-vars` is broken in 5.2.0. The value
# is on the command line for the duration of the call (no standard input in
# 5.2.0, see the header); it is never printed.
set_var() {
  if [[ "$2" == -* ]]; then dash_refused "$1"; fi       # the dry run refused it already: the last guard before clever env set
  attempt "variable $1" clever env set "$1" "$2" --alias "$APP"; echo "    $1"
}
if [[ -n "$CELLAR_ID" ]]; then set_var CELLAR_BUCKET_PREFIX "$BUCKET_PREFIX"; fi
if [[ -f .env.target ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    k="${line%%=*}"; v="${line#*=}"; v="${v%\"}"; v="${v#\"}"
    if [[ ! "$k" =~ $RE_VAR ]]; then
      echo "    name refused, ignored: $(printf '%s' "$k" | tr -cd '[:alnum:]_.-' | cut -c1-40)" >&2; continue
    fi
    set_var "$k" "$v"
  done < .env.target
else
  echo "    .env.target missing: app-specific variables, to derive from the SURVEY and the code (never committed)."
fi

# Node runtime: the build phase installs the dependencies, it does NOT run
# scripts.build. And devDependencies are not installed if
# NODE_ENV=production: a build that needs them (Tailwind, TypeScript…)
# fails. Hence the two variables below when a build script exists.
if [[ "$APP_TYPE" == "node" && -f package.json ]] \
   && python3 -I -c 'import json,sys; sys.exit(0 if json.load(open("package.json")).get("scripts",{}).get("build") else 1)'; then
  set_var CC_POST_BUILD_HOOK "npm run build"
  set_var CC_NODE_DEV_DEPENDENCIES install
fi

echo "==> 7/7  Sizing"
# An app is created in XS: nano if the smallest is the goal. With a small
# runtime flavor, a dedicated build instance (BUILD_FLAVOR).
attempt "sizing" clever scale --alias "$APP" \
  --min-instances "$MIN_INST" --max-instances "$MAX_INST" \
  --min-flavor "$MIN_FLAVOR" --max-flavor "$MAX_FLAVOR" \
  --build-flavor "$BUILD_FLAVOR"

# THE RULE THAT BREAKS FIRST: pool x MAX_INST <= connections of the plan
# (+ margin for psql and the acceptance checks). Figure read live:
CATALOG="$(read_cmd "postgresql catalog" clever addon providers show postgresql-addon)" || exit $?
CONNECTIONS=$(printf '%s\n' "$CATALOG" \
  | awk -v p="Plan $PG_PLAN" '$0==p{f=1;next} /^Plan /{f=0} f && /Max connection limit/{print $NF; exit}')
echo "    plan $PG_PLAN: ${CONNECTIONS:-?} connections -> max pool per instance = ${CONNECTIONS:-?} / $MAX_INST, minus the margin"

cat <<END

Target provisioned (track $TRACK, zone $ZONE, organisation $ORG).

Next:
  1. Extensions: 'vector', 'postgis', 'pgcrypto', 'uuid-ossp', 'pg_trgm'…
     are available by default (except plan dev: none). On ticket:
     'pg_cron', 'pg_net', 'http'… UNAVAILABLE, to replace in the plan:
     pgsodium, supabase_vault, pgmq, pgjwt, pg_graphql.
  2. Scheduled jobs (pg_cron replaced by Clever cron): copy
     $SKILL_ROOT/templates/cron/cron-env.sh and cron-job.sh into clevercloud/,
     clevercloud/cron.json calls e.g. "\$ROOT/clevercloud/cron-job.sh POST /tasks/<name>".
     On the app: CC_PRE_RUN_HOOK="bash clevercloud/cron-env.sh", CRON_RUNS_HERE=1,
     CRON_VARIABLES="<names the job needs>", CRON_BASE_URL (e.g. the internal
     API URL), CRON_TOKEN_VAR=<name of the token variable>. A job runs with an
     EMPTY environment, no variables and no INSTANCE_NUMBER. Scripts executable
     BEFORE the commit (git update-index --chmod=+x); times in UTC; no
     effect on a Docker app.
  3. Deploy:  clever deploy --alias $APP  (committed HEAD only; shallow
     repository or worktree refused: modules/supabase/reference/clever-cloud.md §3)
  4. Acceptance:  modules/supabase/scripts/check-http.py and check-files.py (target) — modules/supabase/GUIDE.md phase 6.
END
if [[ "$TRACK" == "hds" ]]; then
  echo "  HDS: only add add-ons available in $ZONE; never Keycloak, MongoDB,"
  echo "        Matomo, FS Bucket, Materia KV, Mailpace, Azimutt, nor CKE."
fi
