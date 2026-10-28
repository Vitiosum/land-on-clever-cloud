#!/usr/bin/env bash
# prove.sh — evidence that a Clever Cloud deployment really works, not just "200 OK".
#
# Usage:
#   bash prove.sh <url> [--expect TEXT] [--asset-expect TEXT] [--check "METHOD PATH CODE [MARKER]"]...
#                 [--data BODY] [--alias ALIAS] [--log-expect TEXT] [--timeout SECONDS] [--json]
#                 [--pg-expect-tables N] [--pg-expect-table NAME]... [--pg-addon ID_OR_NAME [--pg-org ORG] | --pg-env-file FILE] [--health-path PATH]
#                 [--upload-check "ROUTE FILE" [--upload-field NAME] [--delivery TEMPLATE]] [--socketio-event EV [--socketio-expect TEXT]]
#                 [--expect-closed URL]...
#
#   --expect TEXT        TEXT must appear in the page at <url> (the page is the app, not an error page)
#   --asset-expect TEXT  TEXT must appear in a same-origin JS bundle: proves build-time variables
#                        (NEXT_PUBLIC_*, VITE_*) were inlined. Use a NON-secret marker (a hostname).
#   --check "GET /db/ 200 Greeting"
#                        the REAL code path the app uses (a DB-backed page, an API route, a
#                        socket.io handshake…): method, path, expected status, optional marker.
#                        Repeatable. POST checks send --data (form-encoded).
#                        Checks run in order: "POST then GET" is a chained data-path check.
#                        Redirects are NOT followed: the code is the FIRST answer's (a logout's 303,
#                        a login's 302 — following a 303 with POST gave false 405s); expect 3xx there.
#   --header-expect "NAME: VALUE"
#                        a response header of GET <url> (final answer) must contain VALUE, NAME case-
#                        insensitive (e.g. "x-served-by: edge-middleware" set by a middleware). Repeatable.
#   --path PATH          shorthand for --check "GET PATH 2xx"
#   --expect-closed URL  a route that must NOT answer, on ANOTHER domain of the project than <url> (refused otherwise): e.g. the env route
#                        on a scheduler app's default domain, https://app-<its app id without "app_">.cleverapps.io/env (the app_… id:
#                        .clever.json, ledger.sh show — the domain stays this one after its removal). GET URL, no redirect followed, must answer 404 (the router's answer once
#                        the domain is removed, or the route gone from the code); anything else is a FAIL — it answers, or nothing proves it
#                        closed. A plain http(s) host and path (no placeholder left, no query); nothing is read from the app, no secret
#                        sent. Repeatable. A guard, not a proof that the app works: it never counts as the check beyond "/ answers"
#   --check-auth "METHOD PATH CODE [MARKER]"
#                        like --check, but sends "Authorization: Bearer <value of --auth-env NAME>";
#                        plain --check never sends it, so one run proves 401 without and 200 with
#   --auth-env NAME      the variable holding the secret: read by THIS script from the app
#                        (`clever env --alias ALIAS`, surrounding quotes removed) when it is not
#                        set in the shell; the value is never printed nor on a command line
#   --upload-form PAGE   with --upload-check: the route is an HTML FORM (multipart, answers 2xx or a
#                        redirect, no JSON): PAGE (e.g. /) is then read and every same-origin
#                        img/a/source link on it is fetched (max 20) until one has FILE's SHA-256
#   --form-field NAME=VALUE  an extra field of that form (e.g. titre=Test); repeatable
#   --upload-check "ROUTE FILE"
#                        one upload + round trip: POST FILE as multipart (field --upload-field,
#                        default "file") to ROUTE, read the stored location from the JSON answer
#                        (url / downloadUrl / pathname / key …; --delivery "/api/blob?pathname={}"
#                        turns a key into a URL), GET it back and compare SHA-256 with FILE
#   --socketio-event EV  data path over WebSockets' polling transport: open a socket.io session
#                        (EIO=4, polling), send the connect packet "40", then read packets until
#                        event EV arrives (e.g. "scores"); --socketio-expect TEXT must be in it.
#                        String payloads holding JSON are DECODED before matching: '"b":1' matches
#                        42["scores","{\"a\":0,\"b\":1}"]. Real proof: POST a vote on the front
#                        app first, then expect the new count here.
#   --alias ALIAS        also read the logs, bounded: clever logs --since <window> --until now
#   --log-since D        log window; default: since the start of the app's LATEST deployment
#                        (clever activity), so errors of an older failed build are not counted
#   --cron-expect TEXT   a cron.json job ran: a CMDOUT line of the logs contains TEXT
#   --cron-wait SECONDS  keep re-reading the logs (every 20 s) until that line appears, bounded
#                        (use with a temporary */2 schedule on the POC branch: runtimes-hooks.md).
#                        It first waits for the latest deployment to finish and only counts CMDOUT
#                        lines logged AFTER that: during a zero-downtime deploy the old and the new
#                        instance both carry INSTANCE_NUMBER 0 and both run the job.
#   --log-expect TEXT    TEXT must appear in those logs (e.g. a worker's "Processing vote")
#   --build-expect TEXT  TEXT must appear in those logs too, as build evidence (e.g. "Applying"
#                        for Django migrations run by CC_PYTHON_MANAGE_TASKS) → [observed]
#   --timeout SECONDS    total time budget (default 180); every request is bounded too
#   --pg-expect-tables N the data lands in PostgreSQL, not in a silent SQLite fallback (a Django app without DATABASE_URL runs on its
#                        SQLite default with the build, the health check and HTTP 200 all green, test T2): at least N tables of the
#                        app must be in the add-on — base tables of schema public, PostGIS's own (spatial_ref_sys…) and any table
#                        that belongs to an extension not counted. READ-ONLY: one session with default_transaction_read_only = on
#                        and one SELECT count(*). It runs after the --check requests (a table created by the first request counts).
#                        Asked for and not run (no credentials, no psql, add-on unreadable, connection refused) is a FAIL, never a
#                        PASS and never skipped: NOT PROVEN. The plan requires it whenever a PostgreSQL add-on is linked to an app.
#   --pg-expect-table NAME  repeatable: the table NAME of the app must be in the add-on (schema public, not a view, not an extension's). A count alone
#                        passes on a reused add-on that already holds other tables; a name does not. NAME: a plain identifier (letters, digits, _,
#                        63 at most, case kept), sent to psql as a variable (-v t1=NAME, the SQL on stdin holds :'t1'), never written into the SQL.
#                        Without --pg-expect-tables, N is the number of names.
#   --pg-addon ID_OR_NAME  the add-on whose POSTGRESQL_ADDON_* variables are read: prove.sh runs `env-safe.sh env-file` itself, into a
#                        600 file of its own 700 temporary folder, removed at exit (a NAME needs --pg-org orga_… to be resolved)
#   --pg-env-file FILE   or: a 600 file already written by `env-safe.sh env-file --addon … --only POSTGRESQL_ADDON_` (it stays: it is
#                        yours). Either way the password reaches psql only through a 600 PGPASSFILE in a 700 folder that is removed
#                        as soon as psql returns: never on a command line, never in psql's environment, never printed.
#   --health-path PATH   the route that will become CC_HEALTH_CHECK_PATH (never before it is proven). GET PATH must answer 2xx
#                        (the same request as a --check on it, run once). When it does and nothing failed, the line "proven: now
#                        set CC_HEALTH_CHECK_PATH" and the two commands of the step after the proof are PRINTED — `clever env set
#                        CC_HEALTH_CHECK_PATH PATH --alias ALIAS` then `clever restart --alias ALIAS` (ALIAS: --alias, else
#                        <alias>); a failing health check prints them nowhere. This script never runs them.
#
# Verdict PROVEN only if every check passes AND at least one check beyond "/ answers" ran
# (--expect, --asset-expect, --check, --socketio-event, --log-expect, --pg-expect-tables or --health-path): a blank page or a 400
# also answers. Error-looking log lines are printed (masked) and give "PROVEN with warnings":
# read them — an init-order error (e.g. `relation "votes" does not exist` before the worker
# created the table on its first start) is harmless only if later lines show success.
# Read-only on the app except the POST checks you ask for. Never prints a body; masks secrets.
# Exit 0 PROVEN, 1 NOT PROVEN, 2 usage.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

URL=""; EXPECT=""; ASSET=""; DATA=""; ALIAS=""; LOGX=""; BUILDX=""; CRONX=""; CRONWAIT=0; LOGSINCE=""; SIOEV=""; SIOX=""; AUTHENV=""; BUDGET=180; JSON=0
UPLOAD=""; UPFIELD="file"; DELIVERY=""; UPFORM=""
PGN=""; PGADDON=""; PGORG=""; PGENVFILE=""; HPATH=""; HPSET=0; PGNAMES=()
HEXP=(); FFIELDS=()
ACHECKS=()
CHECKS=()
CLOSED=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --expect) loc_need "$@"; EXPECT="$2"; shift 2 ;;
    --asset-expect) loc_need "$@"; ASSET="$2"; shift 2 ;;
    --check) loc_need "$@"; CHECKS+=("$2"); shift 2 ;;
    --path) loc_need "$@"; CHECKS+=("GET $2 2xx"); shift 2 ;;
    --expect-closed) loc_need "$@"; CLOSED+=("$2"); shift 2 ;;
    --data) loc_need "$@"; DATA="$2"; shift 2 ;;
    --alias) loc_need "$@"; ALIAS="$2"; shift 2 ;;
    --log-expect) loc_need "$@"; LOGX="$2"; shift 2 ;;
    --build-expect) loc_need "$@"; BUILDX="$2"; shift 2 ;;
    --log-since) loc_need "$@"; LOGSINCE="$2"; shift 2 ;;
    --socketio-event) loc_need "$@"; SIOEV="$2"; shift 2 ;;
    --socketio-expect) loc_need "$@"; SIOX="$2"; shift 2 ;;
    --auth-env) loc_need "$@"; AUTHENV="$2"; shift 2 ;;
    --check-auth) loc_need "$@"; ACHECKS+=("$2"); shift 2 ;;
    --upload-check) loc_need "$@"; UPLOAD="$2"; shift 2 ;;
    --upload-field) loc_need "$@"; UPFIELD="$2"; shift 2 ;;
    --delivery) loc_need "$@"; DELIVERY="$2"; shift 2 ;;
    --upload-form) loc_need "$@"; UPFORM="$2"; shift 2 ;;
    --form-field) loc_need "$@"; FFIELDS+=("$2"); shift 2 ;;
    --header-expect) loc_need "$@"; HEXP+=("$2"); shift 2 ;;
    --cron-wait) loc_need "$@"; CRONWAIT="$2"; shift 2 ;;
    --cron-expect) loc_need "$@"; CRONX="$2"; shift 2 ;;
    --pg-expect-tables) loc_need "$@"; PGN="$2"; shift 2 ;;
    --pg-expect-table) [ $# -ge 2 ] || loc_die "--pg-expect-table takes a plain table name"; PGNAMES+=("$2"); shift 2 ;;
    --pg-addon) loc_need "$@"; PGADDON="$2"; shift 2 ;;
    --pg-org) loc_need "$@"; PGORG="$2"; shift 2 ;;
    --pg-env-file) loc_need "$@"; PGENVFILE="$2"; shift 2 ;;
    --health-path) loc_need "$@"; HPATH="$2"; HPSET=1; shift 2 ;;
    --timeout) loc_need "$@"; BUDGET="$2"; shift 2 ;;
    --json) JSON=1; shift ;;
    -*) loc_die "unknown option $1" ;;
    *) URL="$1"; shift ;;
  esac
done
[ -n "$URL" ] || { loc_help "$0"; exit 2; }
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
URL="${URL%/}"
printf '%s' "$URL" | grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/.*)?$' || loc_die "invalid URL: $URL"
ORIGIN=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]+).*#\1#')
# RT D5: table names come from the repository (a SQLite file, Prisma models, the data job's manifest): a plain identifier only, at most 50
PGSEEN=" "
for pn in ${PGNAMES[@]+"${PGNAMES[@]}"}; do
  case "$pn" in *$'\n'*|*$'\r'*) loc_die "--pg-expect-table takes a plain table name (letters, digits and _, not starting with a digit, 63 at most; no schema, no quote)" ;; esac
  printf '%s' "$pn" | LC_ALL=C grep -Eq '^[A-Za-z_][A-Za-z0-9_]{0,62}$' || loc_die "--pg-expect-table takes a plain table name (letters, digits and _, not starting with a digit, 63 at most; no schema, no quote)"
  case "$PGSEEN" in *" $pn "*) loc_die "--pg-expect-table: $pn is given twice" ;; esac
  PGSEEN="$PGSEEN$pn "
done
[ "${#PGNAMES[@]}" -le 50 ] || loc_die "--pg-expect-table: 50 names at most"
[ -z "$PGN" ] && [ "${#PGNAMES[@]}" -gt 0 ] && PGN="${#PGNAMES[@]}"
if [ -n "$PGN" ]; then
  case "$PGN" in ''|*[!0-9]*|0*) loc_die "--pg-expect-tables takes a positive whole number (no sign, no leading zero)" ;; esac
  [ "${#PGN}" -le 6 ] || loc_die "--pg-expect-tables takes a positive whole number (at most 6 digits)"
  [ -z "$PGADDON" ] || [ -z "$PGENVFILE" ] || loc_die "--pg-addon or --pg-env-file, not both"
  [ -z "$PGORG" ] || [ -n "$PGADDON" ] || loc_die "--pg-org goes with --pg-addon"
  # the organisation of a NAME is an id (provision.sh takes "personal" or a name, env-safe.sh does not): the personal space has no orga_… id, its add-ons are given by their addon_… id
  if [ -n "$PGORG" ]; then
    printf '%s' "$PGORG" | grep -Eq '^(orga|user)_[0-9a-fA-F-]{8,64}$' || loc_die "--pg-org takes an organisation id (orga_… or user_…), not a name nor \"personal\": for the personal space give --pg-addon the addon_… id (ledger.sh show, or clever addon list) and drop --pg-org"
  fi
  if [ -n "$PGADDON" ] && [ -z "$PGORG" ]; then
    printf '%s' "$PGADDON" | grep -Eq '^addon_[0-9a-f-]{8,64}$' || loc_die "--pg-addon NAME needs --pg-org orga_… or user_…: or give --pg-addon the addon_… id (ledger.sh show, or clever addon list) and drop --pg-org"
  fi
elif [ -n "$PGADDON$PGORG$PGENVFILE" ]; then
  loc_die "--pg-addon, --pg-org and --pg-env-file go with --pg-expect-tables N or --pg-expect-table NAME"
fi
UHOST=$(printf '%s' "$ORIGIN" | sed -E 's#^https?://##; s#:[0-9]+$##; s#[.]+$##' | tr 'A-Z' 'a-z')          # web.example. and web.example are one host
for cu in ${CLOSED[@]+"${CLOSED[@]}"}; do       # ANOTHER domain of the project: a plain http(s) URL, the placeholder replaced
  printf '%s' "$cu" | LC_ALL=C grep -Eq '^https?://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9/_.-]*$' \
    || loc_die "--expect-closed takes https://<host>/<path>, a plain host and path (letters, digits, / _ . -; no query): replace every <…> placeholder first — a scheduler app's default domain is app-<its app id without \"app_\">.cleverapps.io (the app_… id: .clever.json, ledger.sh show)"
  CHOST=$(printf '%s' "$cu" | sed -E 's#^https?://##; s#[:/].*$##; s#[.]+$##' | tr 'A-Z' 'a-z')
  # the web app's own host would answer 404 for a route its closure removed, while the scheduler app's domain still serves it: a false PASS
  [ "$CHOST" != "$UHOST" ] || loc_die "--expect-closed must name ANOTHER domain than <url> ($UHOST): the scheduler app's default domain, app-<its app id without \"app_\">.cleverapps.io"
done
if [ "$HPSET" -eq 1 ]; then       # printed in two commands for an agent to run: a plain route path only
  case "$HPATH" in /*) ;; *) loc_die "--health-path takes a path starting with / (letters, digits, / _ . -)" ;; esac
  case "$HPATH" in *[!A-Za-z0-9/_.-]*|*//*) loc_die "--health-path takes a path starting with / (letters, digits, / _ . -; no //)" ;; esac
fi
TMP=$(mktemp -d "${TMPDIR:-/tmp}/prove.XXXXXXXX") || loc_die "cannot create a temporary folder in ${TMPDIR:-/tmp}"      # TMPDIR is honoured on every platform (macOS's mktemp ignores it without a template)
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"
AUTH=()
if [ -n "$AUTHENV" ]; then
  printf '%s' "$AUTHENV" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*$' || loc_die "--auth-env takes a variable NAME"
  if [ -n "${!AUTHENV:-}" ]; then
    ( umask 077; printf 'Authorization: Bearer %s\n' "${!AUTHENV}" > "$TMP/auth.h" )
  else
    [ -n "$ALIAS" ] || loc_die "--auth-env $AUTHENV is not set in this shell: give --alias so the script reads it from the app"
    # read the value from `clever env` inside python: it goes straight into the 600 header file
    ( umask 077; clever env --alias "$ALIAS" 2>/dev/null | python3 -I -c '
import re, sys
name, out = sys.argv[1], sys.argv[2]
for line in sys.stdin:
    m = re.match(r"^\s*(?:export\s+)?%s=(.*)$" % re.escape(name), line.rstrip("\n"))
    if m:
        v = m.group(1).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"\x27":
            v = v[1:-1]
        open(out, "w").write("Authorization: Bearer %s\n" % v)
        sys.exit(0)
sys.exit(1)' "$AUTHENV" "$TMP/auth.h" ) || loc_die "--auth-env $AUTHENV: not found on the app $ALIAS (names: env-safe.sh names)"
  fi
  AUTH=(-H "@$TMP/auth.h")          # header from a 600 file: the value is never on a command line
fi
START=$SECONDS; FAIL=0; WARN=0; DEEP=0; RES="$TMP/results"; : > "$RES"; CHECK_OK=0; HSEEN=0; HBAD=0; NEXT=()
left() { echo $(( BUDGET - (SECONDS - START) )); }
rec() { printf '%s\t%s\n' "$1" "$2" >> "$RES"; [ "$JSON" -eq 1 ] || printf '%-5s %s\n' "$1" "$2"; [ "$1" = "FAIL" ] && FAIL=1; [ "$1" = "WARN" ] && WARN=1; return 0; }
code_ok() {  # $1 got, $2 expected (200, 2xx, 3xx…)
  case "$2" in *xx) [ "${1#${2%xx}}" != "$1" ] ;; *) [ "$1" = "$2" ] ;; esac
}

# 1. root page, with patience for a fresh deploy (503 for ~20 s is routing propagation)
CODE=000
while :; do
  T=$(( $(left) < 20 ? $(left) : 20 )); [ "$T" -lt 1 ] && break
  OUT=$(curl -q -sS -L --max-time "$T" -D "$TMP/headers" -o "$TMP/body" -w '%{http_code} %{time_total}' "$URL" 2>"$TMP/err") || OUT="000 0"
  CODE=${OUT%% *}
  case "$CODE" in 2*) break ;; esac
  [ "$(left)" -gt 12 ] || break
  sleep 10
done
if [ "${CODE#2}" != "$CODE" ]; then rec PASS "GET $URL -> $CODE in ${OUT#* }s"
else rec FAIL "GET $URL -> $CODE $(head -c 160 "$TMP/err" 2>/dev/null | loc_mask)"; fi
[ -f "$TMP/body" ] || : > "$TMP/body"

# 1b. response headers of the final answer (a middleware's header, a security header…)
for hx in ${HEXP[@]+"${HEXP[@]}"}; do
  DEEP=1
  HN="${hx%%:*}"; HV="${hx#*:}"; HV="${HV# }"
  [ -n "$HV" ] && [ "$HN" != "$hx" ] || loc_die "--header-expect \"NAME: VALUE\" with a non-empty VALUE"
  printf '%s' "$HN" | grep -Eq '^[A-Za-z0-9-]+$' || loc_die "--header-expect \"NAME: VALUE\" (NAME: letters, digits, -)"
  # the last header block is the final answer's (curl -L writes one block per hop)
  GOTH="$(python3 -I -c 'import re,sys
t=open(sys.argv[1],encoding="latin-1").read().replace("\r","")
blocks=[b for b in re.split(r"\n\n+",t) if b.strip()]
for l in (blocks[-1].split("\n")[1:] if blocks else []):
    k,_,v=l.partition(":")
    if k.strip().lower()==sys.argv[2].lower():
        print(v.strip()); break' "$TMP/headers" "$HN" 2>/dev/null)"
  if [ -n "$GOTH" ] && printf '%s' "$GOTH" | grep -qiF -- "$HV"; then rec PASS "header $HN: contains \"$HV\""
  elif [ -n "$GOTH" ]; then rec FAIL "header $HN is \"$(printf '%s' "$GOTH" | cut -c1-60 | loc_mask)\", expected \"$HV\""
  else rec FAIL "header $HN absent from the answer of GET $URL"; fi
done

# 2. the page is the app
if [ -n "$EXPECT" ]; then
  DEEP=1
  if grep -qF -- "$EXPECT" "$TMP/body"; then rec PASS "page contains \"$EXPECT\""
  else rec FAIL "page does not contain \"$EXPECT\" ($(wc -c < "$TMP/body" | tr -d ' ') bytes)"; fi
fi

# 3. build-time variables inlined in the shipped bundles
if [ -n "$ASSET" ]; then
  DEEP=1
  grep -Eo '(src|href)="[^"]+\.m?js[^"]*"' "$TMP/body" | sed -E 's/^(src|href)="//; s/"$//' | head -n 25 > "$TMP/js"
  N=$(grep -c . "$TMP/js" | tr -d ' '); FOUND=""
  while IFS= read -r p; do
    [ "$(left)" -gt 5 ] || break
    case "$p" in http*://*) case "$p" in "$ORIGIN"*) u="$p" ;; *) continue ;; esac ;; /*) u="$ORIGIN$p" ;; *) u="$URL/$p" ;; esac
    if curl -q -sS -L --max-time 15 "$u" 2>/dev/null | grep -qF -- "$ASSET"; then FOUND="$p"; break; fi
  done < "$TMP/js"
  if [ -n "$FOUND" ]; then rec PASS "bundle $FOUND contains \"$ASSET\" (build-time env inlined)"
  elif [ "$N" -eq 0 ]; then rec FAIL "the page references no JS bundle: --asset-expect does not apply here (use --expect / --check)"
  else rec FAIL "\"$ASSET\" in none of $N bundles: build-time variable missing at BUILD — set it, then rebuild"; fi
fi

# 4. real code paths
one_check() {   # one_check "METHOD PATH CODE [MARKER]" with-auth(0|1): CHECK_OK=1 when it passed
  local c="$1" withauth="$2" M P X MK T C tag="" H=()
  CHECK_OK=0
  set -f; set -- $c; set +f          # split on spaces only, no glob expansion
  M="${1:-GET}"; P="${2:-/}"; X="${3:-2xx}"; shift 3 2>/dev/null || shift $#; MK="$*"
  case "$P" in /*) ;; *) rec FAIL "$M $P: the path must start with / (never another host)"; return ;; esac
  printf '%s' "$M" | grep -Eq '^[A-Z]{3,7}$' || { rec FAIL "$M: not an HTTP method"; return; }
  if [ "$withauth" = 1 ]; then
    [ ${#AUTH[@]} -gt 0 ] || { rec FAIL "$M $P: --check-auth needs --auth-env NAME"; return; }
    H=("${AUTH[@]}"); tag=" (with Authorization)"
  fi
  T=$(( $(left) < 20 ? $(left) : 20 ))
  if [ "$T" -lt 1 ]; then rec FAIL "$M $P: time budget exhausted"; return; fi
  if [ "$M" = "POST" ]; then
    C=$(curl -q -sS --max-time "$T" -o "$TMP/cbody" -w '%{http_code}' ${H[@]+"${H[@]}"} -X POST --data "$DATA" "$ORIGIN$P" 2>/dev/null) || C=000
  else
    C=$(curl -q -sS --max-time "$T" -o "$TMP/cbody" -w '%{http_code}' ${H[@]+"${H[@]}"} -X "$M" "$ORIGIN$P" 2>/dev/null) || C=000
  fi
  if ! code_ok "$C" "$X"; then rec FAIL "$M $P$tag -> $C (expected $X)"
  elif [ -n "$MK" ] && ! grep -qF -- "$MK" "$TMP/cbody" 2>/dev/null; then rec FAIL "$M $P$tag -> $C but no \"$MK\" in the answer"
  else rec PASS "$M $P$tag -> $C${MK:+, contains \"$MK\"}"; CHECK_OK=1; fi
}
is_health_check() {   # is_health_check "METHOD PATH CODE [MARKER]": the request --health-path stands for (GET <path> expecting a 2xx)
  local M P X
  set -f; set -- $1; set +f
  M="${1:-GET}"; P="${2:-/}"; X="${3:-2xx}"
  [ "$M" = GET ] && [ "$P" = "$HPATH" ] && case "$X" in 2*) true ;; *) false ;; esac
}
for c in ${CHECKS[@]+"${CHECKS[@]}"}; do
  DEEP=1; one_check "$c" 0
  if [ "$HPSET" -eq 1 ] && is_health_check "$c"; then HSEEN=1; [ "$CHECK_OK" -eq 1 ] || HBAD=1; fi
done
for c in ${ACHECKS[@]+"${ACHECKS[@]}"}; do DEEP=1; one_check "$c" 1; done
# 4a. routes that must stay closed on another domain of the project (a scheduler app keeps the web app's routes on its default domain until it is removed)
for cu in ${CLOSED[@]+"${CLOSED[@]}"}; do
  T=$(( $(left) < 20 ? $(left) : 20 ))
  if [ "$T" -lt 1 ]; then rec FAIL "closed route not proven: GET $cu: time budget exhausted"; continue; fi
  C=$(curl -q -sS --max-time "$T" -o /dev/null -w '%{http_code}' "$cu" 2>/dev/null) || C=000
  if [ "$C" = 404 ]; then rec PASS "closed route: GET $cu -> 404"
  elif [ "$C" = 000 ]; then rec FAIL "closed route not proven: GET $cu gave no answer (expected the router's 404)"
  else rec FAIL "closed route answers: GET $cu -> $C (expected 404) — remove the env route from the code, or that domain"; fi
done
if [ "$HPSET" -eq 1 ]; then       # the health route: no --check on it above = the same request, run here
  DEEP=1
  [ "$HSEEN" -eq 1 ] || { one_check "GET $HPATH 2xx" 0; HSEEN=1; [ "$CHECK_OK" -eq 1 ] || HBAD=1; }
fi

# 4b. one upload + SHA-256 round trip
if [ -n "$UPLOAD" ]; then
  DEEP=1
  set -f; set -- $UPLOAD; set +f
  UR="${1:-}"; UF="${2:-}"
  case "$UR" in /*) ;; *) loc_die "--upload-check: the ROUTE must start with / (never another host)" ;; esac
  case "${UPFORM:-/}" in /*) ;; *) loc_die "--upload-form: the PAGE must start with / (never another host)" ;; esac
  case "${DELIVERY:-/}" in /*) ;; *) loc_die "--delivery: must start with / (never another host)" ;; esac
  if [ -z "$UR" ] || [ ! -f "$UF" ]; then rec FAIL "--upload-check needs \"ROUTE FILE\" with an existing FILE"
  else
    printf '%s' "$UPFIELD" | grep -Eq '^[A-Za-z0-9_-]+$' || loc_die "--upload-field: letters, digits, - and _ only"
    FARGS=()
    for ff in ${FFIELDS[@]+"${FFIELDS[@]}"}; do
      printf '%s' "${ff%%=*}" | grep -Eq '^[A-Za-z0-9_-]+$' || loc_die "--form-field NAME=VALUE (NAME: letters, digits, - and _)"
      FARGS+=(--form-string "$ff")      # literal: no @file, <file, ;type= nor ;headers=@file interpretation
    done
    if [ -n "$UPFORM" ]; then
      # #26: an HTML form — no JSON, the answer is a page or a redirect (303): never followed with POST
      UC=$(curl -q -sS --max-time 60 -o "$TMP/upbody" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} ${FARGS[@]+"${FARGS[@]}"} -F "$UPFIELD=@$UF" "$ORIGIN$UR" 2>/dev/null) || UC=000
    else
      UC=$(curl -q -sS -L --max-time 60 -o "$TMP/upbody" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} ${FARGS[@]+"${FARGS[@]}"} -F "$UPFIELD=@$UF" "$ORIGIN$UR" 2>/dev/null) || UC=000
    fi
    if [ -n "$UPFORM" ] && { [ "${UC#2}" != "$UC" ] || [ "${UC#3}" != "$UC" ]; }; then
      PC=$(curl -q -sS -L --max-time 30 -o "$TMP/uppage" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} "$ORIGIN$UPFORM" 2>/dev/null) || PC=000
      S1=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$UF")
      grep -Eo '(src|href)="[^"]+"' "$TMP/uppage" 2>/dev/null | sed -E 's/^(src|href)="//; s/"$//' | grep -Ev '^(#|mailto:|javascript:|data:)' | head -n 60 > "$TMP/uplinks" || true
      MATCH=""; n=0
      while IFS= read -r lk; do
        case "$lk" in http*://*) case "$lk" in "$ORIGIN"/*) u="$lk" ;; *) continue ;; esac ;; /*) u="$ORIGIN$lk" ;; *) u="$ORIGIN/$lk" ;; esac
        case "$u" in *.css|*.js|*.mjs) continue ;; esac
        n=$((n + 1)); [ "$n" -le 20 ] && [ "$(left)" -gt 5 ] || break
        curl -q -sS --max-time 20 -o "$TMP/upcand" ${AUTH[@]+"${AUTH[@]}"} "$u" 2>/dev/null || continue
        S2=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/upcand" 2>/dev/null)
        [ "$S1" = "$S2" ] && { MATCH="$lk"; break; }
      done < "$TMP/uplinks"
      if [ -n "$MATCH" ]; then rec PASS "form upload: POST $UR -> $UC, then $UPFORM ($PC) links $MATCH, SHA-256 identical (${S1:0:12}…)"
      else rec FAIL "form upload: POST $UR -> $UC, but none of the $n same-origin links of $UPFORM ($PC) serves the file (SHA-256)"; fi
    elif [ -n "$UPFORM" ]; then rec FAIL "form upload POST $UR -> $UC"
    elif [ "${UC#2}" = "$UC" ]; then rec FAIL "upload POST $UR -> $UC"
    else
      GETURL="$(python3 -I - "$TMP/upbody" "$ORIGIN" "$DELIVERY" <<'PYU'
import json, sys, urllib.parse
body, origin, tpl = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(body))
except ValueError:
    sys.exit(1)
def find(x):
    if isinstance(x, dict):
        for k in ("deliveryUrl", "downloadUrl", "url", "location", "pathname", "key", "path"):
            if isinstance(x.get(k), str) and x[k]:
                return k, x[k]
        for v in x.values():
            r = find(v)
            if r:
                return r
    return None
r = find(d)
if not r:
    sys.exit(1)
k, v = r
if v.startswith(("http://", "https://")):
    print(v)
elif tpl and k in ("pathname", "key", "path"):
    print(origin + tpl.replace("{}", urllib.parse.quote(v, safe="")))
elif v.startswith("/"):
    print(origin + v)
else:
    sys.exit(1)
PYU
)"
      if [ -z "$GETURL" ]; then rec FAIL "upload POST $UR -> $UC but no url/pathname/key in the JSON answer (give --delivery)"
      else
        # the URL comes from the app's answer (untrusted): the Authorization header goes only to the app's own origin
        GAUTH=(); case "$GETURL" in "$ORIGIN"/*) GAUTH=(${AUTH[@]+"${AUTH[@]}"}) ;; *) echo "note: stored file served by another host: fetched WITHOUT the Authorization header" ;; esac
        GC=$(curl -q -sS -L --max-time 60 -o "$TMP/upback" -w '%{http_code}' ${GAUTH[@]+"${GAUTH[@]}"} "$GETURL" 2>/dev/null) || GC=000
        S1=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$UF")
        S2=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/upback" 2>/dev/null)
        if [ "${GC#2}" = "$GC" ]; then rec FAIL "uploaded, but GET of the stored file -> $GC"
        elif [ "$S1" = "$S2" ]; then rec PASS "upload round trip: POST $UR -> $UC, GET back -> $GC, SHA-256 identical (${S1:0:12}…)"
        else rec FAIL "upload round trip: SHA-256 differs (sent ${S1:0:12}…, got ${S2:0:12}…)"; fi
      fi
    fi
  fi
fi

# 4c. socket.io data path (polling transport: no WebSocket client needed)
if [ -n "$SIOEV" ]; then
  DEEP=1; J="$TMP/sio.cookies"; Q="/socket.io/?EIO=4&transport=polling"
  H=$(curl -q -sS --max-time 15 -c "$J" -b "$J" "$ORIGIN$Q" 2>/dev/null)
  SID=$(printf '%s' "$H" | sed -n 's/.*"sid":"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)
  if [ -z "$SID" ]; then rec FAIL "socket.io: no session id from $Q"
  else
    curl -q -sS --max-time 15 -c "$J" -b "$J" -H 'Content-Type: text/plain;charset=UTF-8' --data '40' "$ORIGIN$Q&sid=$SID" >/dev/null 2>&1
    GOT=""; i=0
    while [ $i -lt 6 ] && [ "$(left)" -gt 5 ]; do
      i=$((i + 1))
      curl -q -sS --max-time 20 -c "$J" -b "$J" "$ORIGIN$Q&sid=$SID" >> "$TMP/sio.body" 2>/dev/null
      printf '\n' >> "$TMP/sio.body"     # one poll per line: polls are never glued together
      grep -qF "\"$SIOEV\"" "$TMP/sio.body" 2>/dev/null && { GOT=1; break; }
    done
    # decode socket.io packets (42[...]) and JSON strings inside them, so markers match the real data
    python3 -I - "$TMP/sio.body" > "$TMP/sio.decoded" 2>/dev/null <<'PYD'
import json, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
def unwrap(x):
    if isinstance(x, str):
        try:
            return unwrap(json.loads(x))
        except ValueError:
            return x
    if isinstance(x, list):
        return [unwrap(i) for i in x]
    if isinstance(x, dict):
        return {k: unwrap(v) for k, v in x.items()}
    return x
dec = json.JSONDecoder()
for line in raw.replace("\x1e", "\n").splitlines():     # Engine.IO v4 record separator, and one poll per line
    i = 0
    while i < len(line):                                # tolerate packets glued without a separator
        j = i
        while j < len(line) and line[j].isdigit():
            j += 1
        if j < len(line) and line[j] in "[{" and j > i:
            try:
                obj, end = dec.raw_decode(line, j)
            except ValueError:
                i = j + 1
                continue
            if line[i:j] == "42":
                print(json.dumps(unwrap(obj), separators=(",", ":")))
            i = end
        else:
            i = j + 1 if j > i else i + 1
PYD
    cat "$TMP/sio.decoded" >> "$TMP/sio.body"
    if [ -z "$GOT" ]; then rec FAIL "socket.io session $SID: event \"$SIOEV\" never received"
    elif [ -n "$SIOX" ] && ! grep -qF -- "$SIOX" "$TMP/sio.body"; then rec FAIL "socket.io event \"$SIOEV\" received but without \"$SIOX\""
    else rec PASS "socket.io session: event \"$SIOEV\" received${SIOX:+ with \"$SIOX\"} ($(grep -o "\"$SIOEV\"[^]]*" "$TMP/sio.body" | head -n 1 | cut -c1-80 | loc_mask))"; fi
  fi
fi

# 4d. the data is in PostgreSQL, not in a silent SQLite fallback (test T2). READ-ONLY. Asked for and not run = FAIL: the proof is never skipped, never a PASS.
if [ -n "$PGN" ]; then
  DEEP=1
  PGERR="tables in PostgreSQL NOT PROVEN"
  PGENV="$PGENVFILE"; PGOUT=""
  if [ -z "$PGENV" ] && [ -z "$PGADDON" ]; then
    rec FAIL "$PGERR: no credentials — give --pg-addon <the addon_… id> (or its NAME with --pg-org <orga_… or user_… id>): prove.sh reads the add-on's variables itself; or --pg-env-file FILE (a 600 file from env-safe.sh env-file)"
  elif ! command -v psql >/dev/null 2>&1; then
    rec FAIL "$PGERR: psql is not installed (macOS: brew install libpq; Debian/Ubuntu: apt install postgresql-client) — the proof cannot run, so it is not claimed"
  else
    if [ -z "$PGENV" ]; then
      # the add-on's variables go to a 600 file of our own 700 folder (env-safe.sh prints the names only), removed with $TMP
      PGENV="$TMP/pg.env"; PGO=()
      [ -z "$PGORG" ] || PGO=(--org "$PGORG")
      if ! PGMSG="$(bash "$LOC_SCRIPTS_DIR/env-safe.sh" env-file --out "$PGENV" --addon "$PGADDON" ${PGO[@]+"${PGO[@]}"} --only POSTGRESQL_ADDON_ 2>&1)"; then
        rec FAIL "$PGERR: could not read the add-on's variables ($(printf '%s' "$PGMSG" | head -n 1 | cut -c1-200 | loc_mask))"
        PGENV=""
      fi
    fi
    if [ -n "$PGENV" ]; then
      # the file is read in Python (never sourced); the password goes to a 600 PGPASSFILE in a 700 folder, psql gets PGHOST… from the environment and the password from that file only
      python3 -I - "$PGENV" "$TMP" "$LOC_SCRIPTS_DIR/lib" ${PGNAMES[@]+"${PGNAMES[@]}"} > "$TMP/pg.out" 2>/dev/null <<'PYG'
import os, re, shutil, stat, subprocess, sys, tempfile
envf, tmp, libdir = sys.argv[1:4]
names = sys.argv[4:]                                   # checked by the shell already; checked again: a name that is not a plain identifier stops everything
if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", n) for n in names):
    print("ERR a table name is not a plain identifier")
    sys.exit(0)
sys.path.insert(0, libdir)
from common import PG_NOT_COUNTED as NOT_COUNTED      # the ONE list (plan.py states the same one in the REPORT)
KEYS = ("POSTGRESQL_ADDON_HOST", "POSTGRESQL_ADDON_PORT", "POSTGRESQL_ADDON_DB", "POSTGRESQL_ADDON_USER", "POSTGRESQL_ADDON_PASSWORD")
# the tables of the APP: ordinary and partitioned tables of schema public that belong to no extension (PostGIS ships spatial_ref_sys, the address standardizer us_gaz/us_lex/us_rules…)
# and are not one of the names of NOT_COUNTED (PostGIS objects installed without CREATE EXTENSION, and the views a fresh Clever add-on holds)
SQL = ("select count(*) from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
       "where n.nspname = 'public' and c.relkind in ('r', 'p') and c.relname not in (%s) "
       "and not exists (select 1 from pg_catalog.pg_depend d where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')"
       % ", ".join("'%s'" % n for n in NOT_COUNTED))
def out(msg):
    print(msg)
    sys.exit(0)
try:
    mode = os.stat(envf).st_mode
except OSError:
    out("ERR the credentials file does not exist")
if mode & (stat.S_IRWXG | stat.S_IRWXO):
    out("ERR the credentials file is readable by group/others: chmod 600 it (nothing was sent)")
vals = {}
for ln in open(envf, encoding="utf-8", errors="replace"):
    m = re.match(r"^(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln.rstrip("\n"))
    if m:
        vals[m.group(1)] = m.group(2)           # as written by env-safe.sh env-file: no strip, no unquoting
missing = [k for k in KEYS if not vals.get(k)]
if missing:
    out("ERR the credentials file lacks (names only): " + ", ".join(missing))
d = tempfile.mkdtemp(prefix="pg-", dir=tmp)      # mode 700
try:
    pp = os.path.join(d, "pgpass")
    fd = os.open(pp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    esc = lambda x: x.replace("\\", "\\\\").replace(":", "\\:")
    with os.fdopen(fd, "w") as f:
        f.write("%s:%s:%s:%s:%s\n" % tuple(esc(vals[k]) for k in KEYS))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    host = vals["POSTGRESQL_ADDON_HOST"]
    env.update({"PGHOST": host, "PGPORT": vals["POSTGRESQL_ADDON_PORT"], "PGDATABASE": vals["POSTGRESQL_ADDON_DB"], "PGUSER": vals["POSTGRESQL_ADDON_USER"],
                "PGPASSFILE": pp, "PGSSLMODE": "prefer" if host in ("127.0.0.1", "localhost", "::1") else "require", "PGCONNECT_TIMEOUT": "15"})
    try:
        r = subprocess.run(["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", "set default_transaction_read_only = on", "-c", SQL],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except subprocess.TimeoutExpired:
        out("ERR psql did not answer within 60 s")
    except OSError:
        out("ERR psql could not be started")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")      # may quote a host or a user: only a class of failure is kept
        why = re.search(r"(?i)(password authentication failed|no pg_hba\.conf entry|could not translate host name|connection refused|timeout expired|SSL|does not exist|permission denied)", err)
        out("ERR psql could not query the add-on (%s; the details are withheld)" % (why.group(1).lower() if why else "psql failed"))
    words = r.stdout.decode("utf-8", "replace").split()
    if not words or not words[-1].isdigit():
        out("ERR unexpected answer from psql (no number)")
    if not names:
        out("COUNT " + words[-1])
    # RT D5: the named tables. The names are psql VARIABLES (-v t1=<name>): the SQL, read from stdin (psql interpolates :'t1' as a quoted literal there, never
    # in -c), holds only t1, t2…, so a name is never written into it; the same filter as the count (schema public, tables, no extension's)
    refs = ", ".join(":'t%d'" % (i + 1) for i in range(len(names)))
    script = ("set default_transaction_read_only = on;\n"
              "select c.relname from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
              "where n.nspname = 'public' and c.relkind in ('r', 'p') and c.relname in (%s) "
              "and not exists (select 1 from pg_catalog.pg_depend d where d.classid = 'pg_catalog.pg_class'::regclass and d.objid = c.oid and d.deptype = 'e');\n" % refs)
    argv = ["psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"]
    for i, n in enumerate(names):
        argv += ["-v", "t%d=%s" % (i + 1, n)]
    try:
        r = subprocess.run(argv + ["-f", "-"], input=script.encode(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except subprocess.TimeoutExpired:
        out("ERR psql did not answer within 60 s (named tables)")
    except OSError:
        out("ERR psql could not be started")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace")
        why = re.search(r"(?i)(password authentication failed|no pg_hba\.conf entry|could not translate host name|connection refused|timeout expired|SSL|does not exist|permission denied)", err)
        out("ERR psql could not query the add-on for the named tables (%s; the details are withheld)" % (why.group(1).lower() if why else "psql failed"))
    present = set(r.stdout.decode("utf-8", "replace").split())
    missing = [n for n in names if n not in present]
    out("COUNT %s MISSING %s" % (words[-1], ",".join(missing) or "-"))
finally:
    shutil.rmtree(d, ignore_errors=True)
PYG
      PGOUT="$(cat "$TMP/pg.out" 2>/dev/null)"
      [ -n "$PGENVFILE" ] || rm -f "$PGENV"          # the variables file prove.sh made itself goes as soon as psql has returned (the developer's own file stays)
      case "$PGOUT" in
        "COUNT "*)
          PGCOUNT="${PGOUT#COUNT }"; PGCOUNT="${PGCOUNT%% *}"
          if [ "$PGCOUNT" -ge "$PGN" ]; then
            rec PASS "tables in PostgreSQL: $PGCOUNT table(s) of the app in schema public of the add-on (PostGIS's and other extensions' tables not counted), expected at least $PGN"
          elif [ "$PGCOUNT" -eq 0 ]; then
            rec FAIL "tables not in PostgreSQL (SQLite fallback?): $PGCOUNT table(s) of the app in schema public of the add-on, expected at least $PGN — the app may run on its SQLite default: Clever injects POSTGRESQL_ADDON_URI, never DATABASE_URL (map it, redeploy, run the migrations)"
          else
            rec FAIL "fewer tables in PostgreSQL than expected: $PGCOUNT table(s) of the app in schema public of the add-on, expected at least $PGN — some migrations or the data job did not run on this add-on, or the expected number is wrong (count the app's own tables)"
          fi
          if [ "${#PGNAMES[@]}" -gt 0 ]; then
            PGALL="$(printf '%s, ' "${PGNAMES[@]}")"; PGALL="${PGALL%, }"
            case "$PGOUT" in
              *" MISSING -") rec PASS "named tables in PostgreSQL: $PGALL (schema public of the add-on, tables of the app)" ;;
              *" MISSING "*) PGMISS="${PGOUT##* MISSING }"
                rec FAIL "named tables missing from PostgreSQL: $(printf '%s' "$PGMISS" | sed 's/,/, /g') (of $PGALL) — the data job or the migrations did not create them in this add-on; the count alone would also pass on other tables an add-on already holds" ;;
              *) rec FAIL "$PGERR: the named tables were not checked" ;;
            esac
          fi ;;
        "ERR "*) rec FAIL "$PGERR: ${PGOUT#ERR }" ;;
        *) rec FAIL "$PGERR: the check gave no answer" ;;
      esac
    fi
  fi
fi

# 5. runtime logs, bounded (never clever logs without --until) and masked
if [ -n "$ALIAS" ]; then
  if command -v clever >/dev/null 2>&1; then
    if [ -z "$LOGSINCE" ]; then           # scope to the latest deployment (older failed builds excluded)
      LOGSINCE="$(clever activity --alias "$ALIAS" --format json 2>/dev/null | python3 -I -c 'import json,sys,datetime
try:
    d=[x for x in json.load(sys.stdin) if isinstance(x, dict) and x.get("date")]
    t=max(int(x["date"]) for x in d)/1000.0
    print(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
except Exception:
    pass' 2>/dev/null)"
      [ -n "$LOGSINCE" ] && rec INFO "logs scoped to the latest deployment (since $LOGSINCE)" || { LOGSINCE="20m"; rec INFO "latest deployment unknown: logs of the last 20 min (may include an older build)"; }
    fi
    clever logs --alias "$ALIAS" --since "$LOGSINCE" --until now > "$TMP/logs" 2>&1 || true
    grep -Ei 'Traceback|Unhandled|Exception|Killed|EADDRINUSE|not listening|is not defined|undefined|does not exist|ECONNREFUSED|error' "$TMP/logs" \
      | grep -Eiv 'NU3042|0 errors?|error_log|errorlevel' > "$TMP/errs" || true
    ERR=$(grep -c . "$TMP/errs" | tr -d ' ')
    [ "$JSON" -eq 1 ] || { echo "---- last logs ($LOGSINCE, masked, 15 lines) ----"; tail -n 15 "$TMP/logs" | loc_mask; echo "----"; }
    if [ "${ERR:-0}" -gt 0 ]; then
      rec WARN "$ERR log line(s) look like errors (first ones below): read them before claiming success; an init-order error followed by success is harmless"
      head -n 5 "$TMP/errs" | cut -c1-200 | loc_mask | while IFS= read -r l; do rec WARN "  log: $l"; done
    fi
    if [ -n "$LOGX" ]; then
      DEEP=1
      if grep -qF -- "$LOGX" "$TMP/logs"; then rec PASS "logs contain \"$LOGX\""; else rec FAIL "logs ($LOGSINCE) do not contain \"$LOGX\""; fi
    fi
    if [ -n "$CRONX" ]; then
      DEEP=1
      WSTART=$SECONDS
      CRONSINCE="$LOGSINCE"
      if [ "$CRONWAIT" -gt 0 ]; then
        # #25: wait for the latest deployment to finish, then only count job output logged after it
        # (zero-downtime: the old and the new instance both have INSTANCE_NUMBER 0 for a while)
        while :; do
          DST="$(clever activity --alias "$ALIAS" --format json 2>/dev/null | python3 -I -c 'import json,sys
try:
    d=[x for x in json.load(sys.stdin) if isinstance(x, dict) and x.get("date")]
    print(str(max(d, key=lambda x: int(x["date"])).get("state", "")).upper())
except Exception:
    print("")' 2>/dev/null)"
          case "$DST" in WIP|IN_PROGRESS|DEPLOYING|QUEUED) [ $(( SECONDS - WSTART )) -lt "$CRONWAIT" ] || break; sleep 20 ;; *) break ;; esac
        done
        CRONSINCE="$(python3 -I -c 'import datetime;print(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))')"
        rec INFO "cron: deployment state ${DST:-unknown}; only job output after $CRONSINCE counts (older lines may come from the old instance)"
        clever logs --alias "$ALIAS" --since "$CRONSINCE" --until now > "$TMP/logs" 2>&1 || true
      fi
      while ! grep -F 'CMDOUT' "$TMP/logs" | grep -qF -- "$CRONX" && [ $(( SECONDS - WSTART )) -lt "$CRONWAIT" ]; do
        sleep 20
        clever logs --alias "$ALIAS" --since "$CRONSINCE" --until now > "$TMP/logs" 2>&1 || true
      done
      NRUN=$(grep -F 'CMDOUT' "$TMP/logs" | grep -cF -- "$CRONX" | tr -d ' ')
      if [ "${NRUN:-0}" -gt 1 ]; then rec WARN "cron output seen $NRUN times since $CRONSINCE: twice in the same minute means two instances ran it (overlap, or INSTANCE_NUMBER not deduplicated)"; fi
      if grep -F 'CMDOUT' "$TMP/logs" | grep -qF -- "$CRONX"; then rec PASS "[observed] cron job output (CMDOUT) contains \"$CRONX\""
      else rec FAIL "no cron output (CMDOUT) with \"$CRONX\" since $LOGSINCE${CRONWAIT:+ (waited ${CRONWAIT} s)} — job not proven (if the route is proven, say exactly that); jobs run with an EMPTY environment: runtimes-hooks.md"; fi
    fi
    if [ -n "$BUILDX" ]; then
      if grep -qF -- "$BUILDX" "$TMP/logs"; then rec PASS "[observed] build/run logs contain \"$BUILDX\" ($(grep -cF -- "$BUILDX" "$TMP/logs" | tr -d ' ') line(s))"
      else rec FAIL "logs ($LOGSINCE) do not contain \"$BUILDX\" — widen --log-since to cover the build"; fi
    fi
  else rec WARN "clever not installed: logs not read"; fi
fi

[ "$DEEP" -eq 1 ] || rec FAIL "only '/ answers' was checked: a blank page or a 400 also answers — add --expect or a --check on the real code path"
V="PROVEN"; [ "$WARN" -eq 1 ] && V="PROVEN with warnings"; [ "$FAIL" -eq 0 ] || V="NOT PROVEN"
# R21: the step after the proof — only when GET <path> answered 2xx AND nothing else failed (a run to fix is not the moment to restart the app); printed, never run
if [ "$HPSET" -eq 1 ] && [ "$HSEEN" -eq 1 ] && [ "$HBAD" -eq 0 ] && [ "$FAIL" -eq 0 ]; then
  if [ -n "$ALIAS" ]; then HA="$(printf '%q' "$ALIAS")"; else HA="<alias>"; fi
  NEXT=("clever env set CC_HEALTH_CHECK_PATH $(printf '%q' "$HPATH") --alias $HA" "clever restart --alias $HA")
fi
if [ "$JSON" -eq 1 ]; then
  python3 -I -c 'import json,sys
rows=[l.rstrip("\n").split("\t",1) for l in open(sys.argv[1]) if l.strip()]
print(json.dumps({"url":sys.argv[2],"verdict":sys.argv[3],"seconds":int(sys.argv[4]),"checks":[{"status":a,"detail":b} for a,b in rows],"next_steps":sys.argv[5:]},indent=2))' "$RES" "$URL" "$V" "$((SECONDS - START))" ${NEXT[@]+"${NEXT[@]}"}
else
  if [ ${#NEXT[@]} -gt 0 ]; then
    echo; printf '%-5s %s\n' NEXT "proven: now set CC_HEALTH_CHECK_PATH — GET $HPATH answers 2xx on the deployed app. Run these two commands, in this order (prove.sh only prints them; the restart is needed for a variable change):"
    for ns in "${NEXT[@]}"; do printf '      %s\n' "$ns"; done
  fi
  echo; echo "VERDICT: $V ($((SECONDS - START)) s)"; [ "$FAIL" -eq 0 ] || echo "Do not tell the developer it works."
fi
exit "$FAIL"
