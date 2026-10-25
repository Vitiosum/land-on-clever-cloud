#!/usr/bin/env bash
# prove.sh — evidence that a Clever Cloud deployment really works, not just "200 OK".
#
# Usage:
#   bash prove.sh <url> [--expect TEXT] [--asset-expect TEXT] [--check "METHOD PATH CODE [MARKER]"]...
#                 [--data BODY] [--alias ALIAS] [--log-expect TEXT] [--timeout SECONDS] [--json]
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
#
# Verdict PROVEN only if every check passes AND at least one check beyond "/ answers" ran
# (--expect, --asset-expect, --check, --socketio-event or --log-expect): a blank page or a 400
# also answers. Error-looking log lines are printed (masked) and give "PROVEN with warnings":
# read them — an init-order error (e.g. `relation "votes" does not exist` before the worker
# created the table on its first start) is harmless only if later lines show success.
# Read-only on the app except the POST checks you ask for. Never prints a body; masks secrets.
# Exit 0 PROVEN, 1 NOT PROVEN, 2 usage.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

URL=""; EXPECT=""; ASSET=""; DATA=""; ALIAS=""; LOGX=""; BUILDX=""; CRONX=""; CRONWAIT=0; LOGSINCE=""; SIOEV=""; SIOX=""; AUTHENV=""; BUDGET=180; JSON=0
UPLOAD=""; UPFIELD="file"; DELIVERY=""; UPFORM=""
HEXP=(); FFIELDS=()
ACHECKS=()
CHECKS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) loc_help "$0"; exit 0 ;;
    --expect) loc_need "$@"; EXPECT="$2"; shift 2 ;;
    --asset-expect) loc_need "$@"; ASSET="$2"; shift 2 ;;
    --check) loc_need "$@"; CHECKS+=("$2"); shift 2 ;;
    --path) loc_need "$@"; CHECKS+=("GET $2 2xx"); shift 2 ;;
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
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
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
START=$SECONDS; FAIL=0; WARN=0; DEEP=0; RES="$TMP/results"; : > "$RES"
left() { echo $(( BUDGET - (SECONDS - START) )); }
rec() { printf '%s\t%s\n' "$1" "$2" >> "$RES"; [ "$JSON" -eq 1 ] || printf '%-5s %s\n' "$1" "$2"; [ "$1" = "FAIL" ] && FAIL=1; [ "$1" = "WARN" ] && WARN=1; return 0; }
code_ok() {  # $1 got, $2 expected (200, 2xx, 3xx…)
  case "$2" in *xx) [ "${1#${2%xx}}" != "$1" ] ;; *) [ "$1" = "$2" ] ;; esac
}

# 1. root page, with patience for a fresh deploy (503 for ~20 s is routing propagation)
CODE=000
while :; do
  T=$(( $(left) < 20 ? $(left) : 20 )); [ "$T" -lt 1 ] && break
  OUT=$(curl -sS -L --max-time "$T" -D "$TMP/headers" -o "$TMP/body" -w '%{http_code} %{time_total}' "$URL" 2>"$TMP/err") || OUT="000 0"
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
    if curl -sS -L --max-time 15 "$u" 2>/dev/null | grep -qF -- "$ASSET"; then FOUND="$p"; break; fi
  done < "$TMP/js"
  if [ -n "$FOUND" ]; then rec PASS "bundle $FOUND contains \"$ASSET\" (build-time env inlined)"
  elif [ "$N" -eq 0 ]; then rec FAIL "the page references no JS bundle: --asset-expect does not apply here (use --expect / --check)"
  else rec FAIL "\"$ASSET\" in none of $N bundles: build-time variable missing at BUILD — set it, then rebuild"; fi
fi

# 4. real code paths
one_check() {   # one_check "METHOD PATH CODE [MARKER]" with-auth(0|1)
  local c="$1" withauth="$2" M P X MK T C tag="" H=()
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
    C=$(curl -sS --max-time "$T" -o "$TMP/cbody" -w '%{http_code}' ${H[@]+"${H[@]}"} -X POST --data "$DATA" "$ORIGIN$P" 2>/dev/null) || C=000
  else
    C=$(curl -sS --max-time "$T" -o "$TMP/cbody" -w '%{http_code}' ${H[@]+"${H[@]}"} -X "$M" "$ORIGIN$P" 2>/dev/null) || C=000
  fi
  if ! code_ok "$C" "$X"; then rec FAIL "$M $P$tag -> $C (expected $X)"
  elif [ -n "$MK" ] && ! grep -qF -- "$MK" "$TMP/cbody" 2>/dev/null; then rec FAIL "$M $P$tag -> $C but no \"$MK\" in the answer"
  else rec PASS "$M $P$tag -> $C${MK:+, contains \"$MK\"}"; fi
}
for c in ${CHECKS[@]+"${CHECKS[@]}"}; do DEEP=1; one_check "$c" 0; done
for c in ${ACHECKS[@]+"${ACHECKS[@]}"}; do DEEP=1; one_check "$c" 1; done

# 4a. one upload + SHA-256 round trip
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
      UC=$(curl -sS --max-time 60 -o "$TMP/upbody" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} ${FARGS[@]+"${FARGS[@]}"} -F "$UPFIELD=@$UF" "$ORIGIN$UR" 2>/dev/null) || UC=000
    else
      UC=$(curl -sS -L --max-time 60 -o "$TMP/upbody" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} ${FARGS[@]+"${FARGS[@]}"} -F "$UPFIELD=@$UF" "$ORIGIN$UR" 2>/dev/null) || UC=000
    fi
    if [ -n "$UPFORM" ] && { [ "${UC#2}" != "$UC" ] || [ "${UC#3}" != "$UC" ]; }; then
      PC=$(curl -sS -L --max-time 30 -o "$TMP/uppage" -w '%{http_code}' ${AUTH[@]+"${AUTH[@]}"} "$ORIGIN$UPFORM" 2>/dev/null) || PC=000
      S1=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$UF")
      grep -Eo '(src|href)="[^"]+"' "$TMP/uppage" 2>/dev/null | sed -E 's/^(src|href)="//; s/"$//' | grep -Ev '^(#|mailto:|javascript:|data:)' | head -n 60 > "$TMP/uplinks" || true
      MATCH=""; n=0
      while IFS= read -r lk; do
        case "$lk" in http*://*) case "$lk" in "$ORIGIN"/*) u="$lk" ;; *) continue ;; esac ;; /*) u="$ORIGIN$lk" ;; *) u="$ORIGIN/$lk" ;; esac
        case "$u" in *.css|*.js|*.mjs) continue ;; esac
        n=$((n + 1)); [ "$n" -le 20 ] && [ "$(left)" -gt 5 ] || break
        curl -sS --max-time 20 -o "$TMP/upcand" ${AUTH[@]+"${AUTH[@]}"} "$u" 2>/dev/null || continue
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
        GC=$(curl -sS -L --max-time 60 -o "$TMP/upback" -w '%{http_code}' ${GAUTH[@]+"${GAUTH[@]}"} "$GETURL" 2>/dev/null) || GC=000
        S1=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$UF")
        S2=$(python3 -I -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/upback" 2>/dev/null)
        if [ "${GC#2}" = "$GC" ]; then rec FAIL "uploaded, but GET of the stored file -> $GC"
        elif [ "$S1" = "$S2" ]; then rec PASS "upload round trip: POST $UR -> $UC, GET back -> $GC, SHA-256 identical (${S1:0:12}…)"
        else rec FAIL "upload round trip: SHA-256 differs (sent ${S1:0:12}…, got ${S2:0:12}…)"; fi
      fi
    fi
  fi
fi

# 4b. socket.io data path (polling transport: no WebSocket client needed)
if [ -n "$SIOEV" ]; then
  DEEP=1; J="$TMP/sio.cookies"; Q="/socket.io/?EIO=4&transport=polling"
  H=$(curl -sS --max-time 15 -c "$J" -b "$J" "$ORIGIN$Q" 2>/dev/null)
  SID=$(printf '%s' "$H" | sed -n 's/.*"sid":"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)
  if [ -z "$SID" ]; then rec FAIL "socket.io: no session id from $Q"
  else
    curl -sS --max-time 15 -c "$J" -b "$J" -H 'Content-Type: text/plain;charset=UTF-8' --data '40' "$ORIGIN$Q&sid=$SID" >/dev/null 2>&1
    GOT=""; i=0
    while [ $i -lt 6 ] && [ "$(left)" -gt 5 ]; do
      i=$((i + 1))
      curl -sS --max-time 20 -c "$J" -b "$J" "$ORIGIN$Q&sid=$SID" >> "$TMP/sio.body" 2>/dev/null
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
if [ "$JSON" -eq 1 ]; then
  python3 -I -c 'import json,sys
rows=[l.rstrip("\n").split("\t",1) for l in open(sys.argv[1]) if l.strip()]
print(json.dumps({"url":sys.argv[2],"verdict":sys.argv[3],"seconds":int(sys.argv[4]),"checks":[{"status":a,"detail":b} for a,b in rows]},indent=2))' "$RES" "$URL" "$V" "$((SECONDS - START))"
else
  echo; echo "VERDICT: $V ($((SECONDS - START)) s)"; [ "$FAIL" -eq 0 ] || echo "Do not tell the developer it works."
fi
exit "$FAIL"
