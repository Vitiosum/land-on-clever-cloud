#!/usr/bin/env bash
# env-safe.sh — handle Clever Cloud environment variables without ever printing a value.
#
#   bash env-safe.sh names [--alias A] [--json]
#       Names of the app's variables (values never shown).
#   bash env-safe.sh push <dotenv-file> NAME1,NAME2,... [--alias A]
#       Copy the listed variables from a local dotenv file (parsed in Python, never sourced, never
#       exported) to the app, one `clever env set NAME VALUE` per variable, then VERIFY each name.
#       Names: ^[A-Z_][A-Z0-9_]*$ only; PATH, LD_*, DYLD_*, BASH_ENV, NODE_OPTIONS, PYTHON*, GIT_*,
#       CLEVER_*… and this script's own variables are refused (exit 2).
#   bash env-safe.sh generate NAME [--bytes 48] [--format urlsafe|laravel] [--alias A]
#       Generate a random secret (e.g. DJANGO_SECRET_KEY, APP_KEY), set it, verify it;
#       the value lives only in this process and is never printed.
#   bash env-safe.sh verify NAME1,NAME2,... [--alias A]
#       Fail loudly if a name is missing on the app; report stray one-letter or `_` variables.
#   bash env-safe.sh diff <dotenv-file> [--alias A]
#       Names present locally but missing on the app, and the reverse.
#   bash env-safe.sh env-file --out FILE (--addon ADDON_ID_OR_NAME [--org ORG] | --alias A) [--only P1,P2]
#       Write the variables of an add-on (`clever addon env <addon_id> --format json`) or of an app
#       (`clever env --format json`) to FILE, mode 600, as KEY=VALUE without quotes — the file that
#       migrate-sqlite.py --pg-env-file, migrate-files.py --cellar-env-file and psql scripts read.
#       An add-on NAME is resolved to its addon_… id with `clever addon list --org ORG` (ORG
#       required then). --only keeps the names starting with these prefixes (e.g. POSTGRESQL_ADDON_).
#       Prints the NAMES written, never a value. Never read `clever env` / `clever addon env` by
#       hand (sed, cut…): the values are quoted, a sed slips, and a secret ends in the transcript.
#
# Never `clever env import` (it DELETES every variable absent from its input) and never
# `clever env import-vars` (broken in clever-tools 5.2.0, issue CleverCloud/clever-tools#1195:
# it creates one empty variable per character of the name and does not set the variable).
# Values are never printed, never logged, never written to the ledger. Known limit: clever-tools
# 5.2.0 has no stdin input for `clever env set`, so each value is on THAT command line for the time
# of the call (visible to `ps` by another user of this machine): run it on a single-user machine.
# Exit: 0 ok, 2 usage or refused name, 3 name missing in file, 4 clever failed, 5 verification failed.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

CMD="${1:-}"; [ $# -gt 0 ] && shift
case "$CMD" in ""|-h|--help) loc_help "$0"; exit 0 ;; esac
ALIAS_ARGS=(); ARGS=(); BYTES=48; JSON=0; FORMAT=urlsafe; E_ADDON=""; E_ORG=""; E_OUT=""; E_ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --alias) loc_need "$@"; ALIAS_ARGS=(--alias "$2"); shift 2 ;;
    --bytes) loc_need "$@"; BYTES="$2"; shift 2 ;;
    --format) loc_need "$@"; FORMAT="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    --addon) loc_need "$@"; E_ADDON="$2"; shift 2 ;;
    --org) loc_need "$@"; E_ORG="$2"; shift 2 ;;
    --out) loc_need "$@"; E_OUT="$2"; shift 2 ;;
    --only) loc_need "$@"; E_ONLY="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

valid_name() { printf '%s' "$1" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$'; }

dotenv_names() {
  grep -E '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$1" | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=.*//' | sort -u
}

app_names() {  # names only: values are cut in the pipe and never displayed
  local out
  out="$(clever env ${ALIAS_ARGS[@]+"${ALIAS_ARGS[@]}"} 2>/dev/null)" || { echo "could not read clever env (not linked? not logged in?)" >&2; return 4; }
  printf '%s\n' "$out" | grep -Eo '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' | sort -u
  out=""
}

# Names that push/generate refuse: they would change how this script or clever itself runs
# (PATH, loader and interpreter hooks, clever's own credentials) or are this script's own variables.
RESERVED_RX='^(PATH|HOME|SHELL|IFS|ENV|BASH_ENV|BASH_.*|LD_.*|DYLD_.*|NODE_OPTIONS|NODE_PATH|NPM_CONFIG_.*|PYTHON.*|PERL5?LIB|RUBYOPT|GIT_.*|CLEVER_.*|SSH_.*|TMPDIR|PS[0-9]|PROMPT_COMMAND|CDPATH|LOC_.*|CMD|ARGS|ALIAS_ARGS|BYTES|JSON|FILE|LIST|NAME|OLDIFS|N|V|FMT|FORMAT)$'
push_name() {  # push_name NAME: the only names a value may be set for
  printf '%s' "$1" | grep -Eq '^[A-Z_][A-Z0-9_]*$' || { echo "refused name (only ^[A-Z_][A-Z0-9_]*\$): $1" >&2; return 2; }
  printf '%s' "$1" | grep -Eq "$RESERVED_RX" && { echo "refused name (reserved: it would alter this script or clever itself): $1" >&2; return 2; }
  return 0
}

# set_values MODE FILE-or-BYTES NAME...: parse (push) or generate the values IN Python and call
# `clever env set NAME VALUE` there, argv only: no value is ever exported, expanded or printed by bash.
set_values() {
  python3 -I - "$@" ${ALIAS_ARGS[@]+"${ALIAS_ARGS[@]}"} <<'PYS'
import re, secrets, subprocess, sys
mode, src = sys.argv[1], sys.argv[2]
rest = sys.argv[3:]
alias = rest[rest.index("--alias"):] if "--alias" in rest else []
names = rest[:rest.index("--alias")] if alias else rest
vals = {}
if mode == "push":
    for line in open(src, encoding="utf-8", errors="replace").read().splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and m.group(1) in names:
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            vals[m.group(1)] = v          # last occurrence wins, as before
    missing = [n for n in names if n not in vals]
    if missing:
        print("missing in %s: %s" % (src, " ".join(missing))); sys.exit(3)
else:
    import base64, os
    if os.environ.get("FMT") == "laravel":          # Laravel APP_KEY: "base64:" + 32 random bytes
        vals = {n: "base64:" + base64.b64encode(secrets.token_bytes(32)).decode() for n in names}
    else:
        vals = {n: secrets.token_urlsafe(int(src)) for n in names}
for n in names:
    r = subprocess.run(["clever", "env", "set", n, vals[n]] + alias, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vals[n] = None
    if r.returncode != 0:
        print("clever env set %s failed (value not shown)" % n); sys.exit(4)
PYS
}

verify_names() {  # verify_names NAME... : every name present, no stray variables
  local have missing="" stray
  have="$(app_names)" || return 4
  for n in "$@"; do printf '%s\n' "$have" | grep -qx "$n" || missing="$missing $n"; done
  stray="$(printf '%s\n' "$have" | grep -Ex '[A-Za-z_]' | tr '\n' ' ')"
  [ -n "$stray" ] && echo "WARNING stray one-letter variables on the app: $stray(symptom of clever env import-vars, issue #1195): remove them with clever env rm"
  if [ -n "$missing" ]; then echo "VERIFY FAILED: missing on the app:$missing"; return 5; fi
  echo "verified on the app (names only): $*"
}

# env_file: the values are read, unquoted and written by Python — never by the shell, never shown
env_file() {
  python3 -I - "$E_OUT" "$E_ADDON" "$E_ORG" "$E_ONLY" ${ALIAS_ARGS[@]+"${ALIAS_ARGS[@]}"} <<'PYE'
import json, os, re, subprocess, sys
out, addon, org, only = sys.argv[1:5]
alias = sys.argv[5:]
NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
def run(argv):
    r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return r.returncode, r.stdout.decode("utf-8", "replace")
if addon:
    if not re.fullmatch(r"addon_[0-9a-f-]{8,64}", addon):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", addon):
            print("env-file: refused add-on name"); sys.exit(2)
        if not org or not re.fullmatch(r"(orga|user)_[0-9a-fA-F-]{8,64}", org):
            print("env-file: an add-on NAME needs --org orga_… (clever addon list without --org does not say which organisation)"); sys.exit(2)
        rc, txt = run(["clever", "addon", "list", "--org", org, "--format", "json"])
        try:
            ids = [str(x.get("addonId") or x.get("id")) for x in json.loads(txt) if isinstance(x, dict) and x.get("name") == addon]
        except ValueError:
            ids = []
        if len(ids) != 1 or not re.fullmatch(r"addon_[0-9a-f-]{8,64}", ids[0]):
            print("env-file: add-on %s found %d time(s) in %s: give its addon_… id" % (addon, len(ids), org)); sys.exit(4)
        addon = ids[0]
    rc, txt = run(["clever", "addon", "env", addon, "--format", "json"])
    what = "add-on " + addon
else:
    rc, txt = run(["clever", "env", "--format", "json"] + alias)
    what = "app " + (alias[1] if alias else "(linked)")
if rc != 0:
    print("env-file: could not read the variables of %s (not logged in? wrong id?)" % what); sys.exit(4)
try:
    d = json.loads(txt)
except ValueError:
    print("env-file: unexpected output (not JSON) for %s: nothing written" % what); sys.exit(4)
txt = None
if isinstance(d, list):
    d = {x.get("name"): x.get("value") for x in d if isinstance(x, dict)}
if not isinstance(d, dict):
    print("env-file: unexpected JSON for %s: nothing written" % what); sys.exit(4)
prefixes = [p for p in only.split(",") if p] if only else []
vals = {k: ("" if v is None else str(v)) for k, v in d.items() if isinstance(k, str) and NAME.match(k) and (not prefixes or any(k.startswith(p) for p in prefixes))}
bad = [k for k, v in vals.items() if "\n" in v or "\r" in v]
if bad:
    print("env-file: multi-line value(s) not written (names): %s" % " ".join(sorted(bad)))
    for k in bad:
        vals.pop(k)
if not vals:
    print("env-file: no variable to write for %s%s" % (what, (" with prefix " + only) if only else "")); sys.exit(3)
import tempfile
d = os.path.dirname(os.path.abspath(out))
if os.stat(d).st_mode & 0o022:
    print("env-file: %s is writable by group/others: write credentials in a folder only you can write (chmod 700)" % d); sys.exit(2)
old = os.umask(0o077)
fd, tmp = tempfile.mkstemp(prefix=".env-file-", dir=d)          # O_EXCL, 0600, unpredictable name: never a planted link
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for k in sorted(vals):
            f.write("%s=%s\n" % (k, vals[k]))
    os.replace(tmp, out)
    tmp = None
finally:
    os.umask(old)
    if tmp and os.path.lexists(tmp):
        os.unlink(tmp)
print("env-file: %s written (mode 600) from %s — names only: %s" % (out, what, " ".join(sorted(vals))))
PYE
}

case "$CMD" in
  env-file)
    [ -n "$E_OUT" ] || loc_die "usage: env-file --out FILE (--addon ID_OR_NAME [--org ORG] | --alias A) [--only PREFIX,…]"
    [ -n "$E_ADDON" ] && [ ${#ALIAS_ARGS[@]} -gt 0 ] && loc_die "env-file: --addon or --alias, not both"
    case "$E_OUT" in */*) [ -d "$(dirname "$E_OUT")" ] || loc_die "env-file: folder of $E_OUT missing" ;; esac
    if T_TOP="$(git -C "$(dirname "$E_OUT")" rev-parse --show-toplevel 2>/dev/null)" && [ "$(cd "$T_TOP" && pwd -P)" != "$(cd ~ && pwd -P)" ]; then
      case "$(cd "$(dirname "$E_OUT")" && pwd -P)/" in "$(cd "$T_TOP" && pwd -P)/.clever-landing/"*)
          # the .clever-landing/ exception holds only if it is OURS: a real folder, git-excluded, nothing tracked
          [ -L "$T_TOP/.clever-landing" ] && loc_die "env-file: $T_TOP/.clever-landing is a symbolic link: refused"
          [ -n "$(git -C "$T_TOP" ls-files -- .clever-landing .clever.json)" ] && loc_die "env-file: .clever-landing/ or .clever.json is tracked by git in $T_TOP: refused (delete it, see ledger.sh)"
          git -C "$T_TOP" check-ignore -q -- "$E_OUT" || loc_die "env-file: $E_OUT is not git-excluded (run ledger.sh init first)" ;;
        *) loc_die "env-file: $E_OUT is inside the repository $T_TOP: write credentials outside it (or in its .clever-landing/, git-excluded)" ;;
      esac
    fi
    env_file; exit $?
    ;;
  names)
    N="$(app_names)" || exit 4
    if [ "$JSON" -eq 1 ]; then printf '%s\n' "$N" | python3 -I -c 'import json,sys;print(json.dumps({"names":[l for l in sys.stdin.read().split() if l]}))'
    else printf '%s\n' "$N"; fi
    ;;
  push)
    FILE="${ARGS[0]:-}"; LIST="${ARGS[1]:-}"
    [ -f "$FILE" ] && [ -n "$LIST" ] || loc_die "usage: push <dotenv-file> NAME1,NAME2"
    OLDIFS="$IFS"; IFS=','; set -f; set -- $LIST; set +f; IFS="$OLDIFS"
    for n in "$@"; do push_name "$n" || exit 2; done
    set_values push "$FILE" "$@"; rc=$?; [ $rc -eq 0 ] || exit $rc
    verify_names "$@" || exit 5
    echo "reminder: runtime variables need 'clever restart'; build-time ones (NEXT_PUBLIC_*, VITE_*…) need a rebuild."
    ;;
  generate)
    NAME="${ARGS[0]:-}"
    [ -n "$NAME" ] || loc_die "usage: generate NAME [--bytes N]"
    push_name "$NAME" || exit 2
    case "$BYTES" in ''|*[!0-9]*) loc_die "--bytes must be a number" ;; esac
    case "$FORMAT" in urlsafe|laravel) ;; *) loc_die "--format must be urlsafe or laravel" ;; esac
    FMT="$FORMAT" set_values generate "$BYTES" "$NAME"; rc=$?; [ $rc -eq 0 ] || exit $rc
    verify_names "$NAME" || exit 5
    echo "generated and set on the app (value never shown): $NAME"
    ;;
  verify)
    LIST="${ARGS[0]:-}"; [ -n "$LIST" ] || loc_die "usage: verify NAME1,NAME2"
    OLDIFS="$IFS"; IFS=','; set -f; set -- $LIST; set +f; IFS="$OLDIFS"
    for n in "$@"; do valid_name "$n" || loc_die "invalid variable name: $n"; done
    verify_names "$@" || exit 5
    ;;
  diff)
    FILE="${ARGS[0]:-}"; [ -f "$FILE" ] || loc_die "usage: diff <dotenv-file>"
    L=$(dotenv_names "$FILE"); R=$(app_names) || exit 4
    echo "only local (to push or to drop): $(comm -23 <(echo "$L") <(echo "$R") | tr '\n' ' ')"
    echo "only on app (add-on or platform): $(comm -13 <(echo "$L") <(echo "$R") | tr '\n' ' ')"
    ;;
  *) loc_die "unknown command $CMD (see --help)" ;;
esac
