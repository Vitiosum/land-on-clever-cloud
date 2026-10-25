#!/usr/bin/env bash
# ledger.sh — remember exactly what was created on Clever Cloud, so teardown is exact.
#
#   bash ledger.sh init                       create .clever-landing/, exclude it and .clever.json via .git/info/exclude
#   bash ledger.sh add <app|addon|domain|bucket> <id> <name> [note] [org]
#                                             bucket: id = name = the Cellar bucket (created by migrate-files.py / migrate-storage.py)
#                                             id = the real app_… / addon_… id (provision.sh captures it)
#   bash ledger.sh show [--json]
#   bash ledger.sh teardown-plan              PRINTS the delete commands; never executes them
#
# The ledger lives in <repo>/.clever-landing/ledger.tsv. It is excluded through
# .git/info/exclude (never .gitignore: tracked files are not modified, and a later
# `git add .` by the developer cannot commit it). Refuses a repository whose toplevel is $HOME,
# and a .clever-landing/ or .clever.json TRACKED by git (exit 10: the repository's word, not ours).
# Every row is validated on write and again before teardown-plan prints it into a command.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

case "${1:-}" in ""|-h|--help) loc_help "$0"; exit 0 ;; esac
loc_git_guard "."
dir="$(loc_landing_dir "$LOC_TOP")"; led="$dir/ledger.tsv"
# The ledger is OUR record: a .clever-landing/ or .clever.json tracked by git comes from the repository
# (.git/info/exclude does not cover tracked files) and is never trusted.
TRACKED="$(git -C "$LOC_TOP" ls-files -- .clever-landing .clever.json | tr '\n' ' ')"
[ -n "$TRACKED" ] && { printf 'REFUSED: tracked by git: %s— the local state of this skill never comes from the repository — someone committed it: do NOT just untrack it (git rm --cached leaves the forged file on disk). Read it, DELETE it (rm -rf -- .clever-landing .clever.json), commit the deletion on clever-poc, then run the skill again from the scan.\n' "$TRACKED" >&2; exit 10; }
ID_OK='^((app|addon)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[a-z0-9][a-z0-9-]{0,62})$'   # real id, or the plan's slug name
DOMAIN_OK='^[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,63}$'
BUCKET_OK='^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$'
valid_row() {  # kind id name org: every field that teardown-plan prints into a command
  case "$1" in app|addon) printf '%s' "$2" | grep -Eq "$ID_OK" ;; domain) printf '%s' "$2" | grep -Eq "$DOMAIN_OK" ;; bucket) printf '%s' "$2" | grep -Eq "$BUCKET_OK" ;; *) return 1 ;; esac || return 1
  printf '%s' "$3" | grep -Eq '^[a-z0-9][a-z0-9.-]{0,252}$' || return 1
  [ -z "$4" ] || printf '%s' "$4" | grep -Eq '^[A-Za-z0-9_.-]{1,80}$'
}
case "$1" in
  init)
    mkdir -p "$dir"
    ex="$(git -C "$LOC_TOP" rev-parse --git-path info/exclude)"
    case "$ex" in /*) ;; *) ex="$LOC_TOP/$ex" ;; esac
    mkdir -p "$(dirname "$ex")"
    grep -qx '.clever-landing/' "$ex" 2>/dev/null || echo '.clever-landing/' >> "$ex"
    grep -qx '.clever.json' "$ex" 2>/dev/null || echo '.clever.json' >> "$ex"   # local CLI link file: never committed by a later git add
    [ -f "$led" ] || printf 'created_at\tkind\tid\tname\tnote\torg\n' > "$led"
    echo "ledger: $led (git-excluded via $ex)" ;;
  add)
    shift; [ $# -ge 3 ] || loc_die "usage: add kind id name [note]"
    [ -f "$led" ] || loc_die "run 'ledger.sh init' first"
    TAB="$(printf '\t')"; NL="
"
    for x in "$@"; do case "$x" in *"$TAB"*|*"$NL"*) loc_die "tab/newline refused in ledger fields" ;; esac; done
    valid_row "$1" "$2" "$3" "${5:-}" || loc_die "refused ledger row: kind app|addon|domain|bucket, id app_<uuid>/addon_<uuid> (or the plan's slug name), name a slug, org a token"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" "${4:-}" "${5:-}" >> "$led" ;;
  show)
    [ -f "$led" ] || { echo "empty"; exit 0; }
    if [ "${2:-}" = "--json" ]; then
      python3 -I -c 'import csv,json,sys;print(json.dumps(list(csv.DictReader(open(sys.argv[1]),delimiter="\t")),indent=2))' "$led"
    else cat "$led"; fi ;;
  teardown-plan)
    [ -f "$led" ] || { echo "nothing recorded"; exit 0; }
    PREFIX="$(python3 -I -c 'import json,sys
try: print(json.load(open(sys.argv[1]))["provision"]["name_prefix"])
except Exception: pass' "$dir/plan.json" 2>/dev/null)"
    echo "# Each command is irreversible: review, then run it yourself or ask the agent to run a named one."
    echo "# 'clever stop' only stops compute: add-ons keep billing until they are deleted."
    VALID="$(mktemp)"; trap 'rm -f "$VALID"' EXIT
    python3 -I - "$led" > "$VALID" <<'PYV'
import re, sys
ID = re.compile(r"\A((app|addon)_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[a-z0-9][a-z0-9-]{0,62})\Z")
DOM = re.compile(r"\A[a-z0-9]([a-z0-9-]{0,62}\.)+[a-z]{2,63}\Z")
BUCKET = re.compile(r"\A[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
NAME = re.compile(r"\A[a-z0-9][a-z0-9.-]{0,252}\Z")
ORG = re.compile(r"\A[A-Za-z0-9_.-]{1,80}\Z")
for line in open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()[1:]:
    c = (line.split("\t") + [""] * 6)[:6]
    ok = (c[1] in ("app", "addon") and ID.match(c[2])) or (c[1] == "domain" and DOM.match(c[2])) or (c[1] == "bucket" and BUCKET.match(c[2]))
    if ok and NAME.match(c[3]) and (not c[5] or ORG.match(c[5])):
        c[4] = re.sub(r"[\x00-\x1f\x7f]", " ", c[4])
        print("\t".join(c))
    else:
        print("# skipped an invalid ledger row (never printed as a command): kind=%s" % re.sub(r"[^a-z]", "?", c[1][:10]), file=sys.stderr)
PYV
    echo "# --- created by this skill ---"
    awk -F'\t' '$2=="addon" && $5 !~ /^reused/ {o=($6!="")?" --org "$6:""; printf "clever addon delete %s%s --yes   # %s (%s)\n", $3, o, $4, $5}' "$VALID"
    awk -F'\t' '$2=="app"   && $5 !~ /^reused/ {printf "clever delete --app %s --yes   # %s (%s)\n", $3, $4, $5}' "$VALID"
    if awk -F'\t' '$2=="bucket" {f=1} END {exit !f}' "$VALID"; then
      echo "# --- Cellar buckets created by the migration scripts: deleted WITH their Cellar add-on; if that add-on is kept (reused), empty and delete each one with an S3 client ---"
      awk -F'\t' '$2=="bucket" {printf "# bucket %s (%s): aws s3 rb s3://%s --force --endpoint-url https://<CELLAR_ADDON_HOST>   (credentials from env-safe.sh env-file)\n", $3, $5, $3}' "$VALID"
    fi
    if awk -F'\t' '$5 ~ /^reused/ {f=1} END {exit !f}' "$VALID"; then
      echo "# --- REUSED: they existed before this run. Delete them only if they come from an earlier POC with the same prefix${PREFIX:+ ($PREFIX)}; otherwise leave them ---"
      awk -F'\t' '$2=="addon" && $5 ~ /^reused/ {o=($6!="")?" --org "$6:""; printf "clever addon delete %s%s --yes   # %s (%s)\n", $3, o, $4, $5}' "$VALID"
      awk -F'\t' '$2=="app"   && $5 ~ /^reused/ {printf "clever delete --app %s --yes   # %s (%s)\n", $3, $4, $5}' "$VALID"
    fi
    echo "# --- pause instead (compute only; clever stop takes --app or --alias, never --org) ---"
    awk -F'\t' '$2=="app" {printf "clever stop --app %s   # %s\n", $3, $4}' "$VALID"
    echo "# --- local repository ---"
    python3 -I - "$LOC_TOP/.clever.json" <<'PYL' | while IFS= read -r al; do
import json, re, sys
try:
    for a in json.load(open(sys.argv[1])).get("apps", []):
        if re.match(r"\A[a-z0-9][a-z0-9-]{0,62}\Z", str(a.get("alias", ""))):
            print(a["alias"])
except Exception:
    pass
PYL
      git -C "$LOC_TOP" remote | grep -qxF -- "$al" && echo "git remote remove $al   # git remote added for the app (clever link / deploy)"
    done
    git -C "$LOC_TOP" for-each-ref --format='%(refname:short)' 'refs/heads/clever-backup/*' | while IFS= read -r b; do echo "git branch -D $b   # backup of an app's remote head before a forced deploy"; done
    git -C "$LOC_TOP" rev-parse --verify -q clever-poc-deploy >/dev/null && echo "git branch -D clever-poc-deploy   # shallow-clone snapshot branch"
    git -C "$LOC_TOP" rev-parse --verify -q clever-poc >/dev/null && echo "git branch -D clever-poc          # POC branch (after switching back to your branch)"
    echo "# .clever.json and .clever-landing/ are git-excluded local files: delete them when you no longer need the ledger"
    echo "# Then check nothing is left: clever applications list ; clever addon list" ;;
  *) loc_die "usage: ledger.sh init|add|show|teardown-plan" ;;
esac
