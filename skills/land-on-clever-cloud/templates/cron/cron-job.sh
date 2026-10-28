#!/bin/bash
# ---------------------------------------------------------------------------
# cron-job.sh — run one scheduled HTTP call of the app, on ONE instance only.
#
#   cron-job.sh METHOD PATH        e.g.  cron-job.sh GET /api/cron     cron-job.sh POST /tasks/purge-log
#   cron-job.sh run COMMAND [ARG…] e.g.  cron-job.sh run python purge.py   (a script of the app, run from
#                                  the app folder with the variables of clevercloud/.cron-env exported)
#
# Copy to clevercloud/cron-job.sh, make it executable BEFORE the commit, and reference it from
# clevercloud/cron.json (a JSON array of strings, $ROOT substituted by Clever, UTC):
#   ["0 6 * * * $ROOT/clevercloud/cron-job.sh GET /api/cron"]
#
# THE TRAPS IT SOLVES
# - Clever crons run on EVERY instance: with three instances the job runs three times. The only
#   documented deduplication is a test on INSTANCE_NUMBER: the job runs on instance 0 only.
# - The job's environment is EMPTY: the variables come from clevercloud/.cron-env, written at
#   start by cron-env.sh (CC_PRE_RUN_HOOK). Without that file, or without INSTANCE_NUMBER in it,
#   the job stops with an error (visible in `clever logs --until now`) rather than run N times.
# - clevercloud/cron.json is part of the code, so it is deployed to EVERY app built from the repository (a beat / scheduler app
#   split from the web app, a second service…): the jobs run only on the app that carries CRON_RUNS_HERE=1 (set on that one app,
#   copied to clevercloud/.cron-env by cron-env.sh). Elsewhere the job exits 0 with one line saying so — no error, nothing run.
#   Without the marker nothing runs anywhere: set it on the designated app. The instance-0 rule below still applies on it.
# - Zero-downtime deploys: for a few minutes the old and the new instance carry the same
#   INSTANCE_NUMBER, so a job can run twice: make the task idempotent or locked
#   (pg_try_advisory_lock on the API side), or disable zero-downtime.
#
# Target: CRON_BASE_URL (e.g. an internal API URL) if set, else http://127.0.0.1:$PORT (the app
# itself). Secret: the variable NAMED by CRON_TOKEN_VAR (default CRON_SECRET) is REQUIRED for the
# HTTP mode (list it in CRON_VARIABLES): the job stops without it, so an unprotected route never
# goes unnoticed. A route that is public on purpose: CRON_NO_TOKEN=1 (also copied by cron-env.sh).
# The `run` mode calls no route and needs no token. The token goes in an
# `Authorization: Bearer` header written to a 600 temp file read with curl -H @file — never on
# curl's command line. The route compares it in constant time (reference/runtimes-hooks.md).
# `@reboot` does not exist and paths must be absolute ($ROOT); no cron on a Docker app. Output in
# `clever logs --until now` (CMD / CMDOUT / CMDEND lines). Shared by the whole skill.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
VARS="$HERE/.cron-env"
if [[ ! -r "$VARS" ]]; then
  echo "$VARS missing: is CC_PRE_RUN_HOOK=\"bash clevercloud/cron-env.sh\" set?" >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$VARS"

if [[ "${CRON_RUNS_HERE:-}" != "1" ]]; then
  echo "job skipped: this app is not the one that runs the cron jobs (CRON_RUNS_HERE=1 is set on that one app only)"
  exit 0
fi
if [[ -z "${INSTANCE_NUMBER:-}" ]]; then
  echo "INSTANCE_NUMBER unknown: no deduplication possible, job refused (plan a lock in the database)" >&2
  exit 1
fi
if [[ "$INSTANCE_NUMBER" != "0" ]]; then
  echo "instance ${INSTANCE_NUMBER}: job skipped (deduplication)"
  exit 0
fi

if [[ "${1:-}" == "run" ]]; then
  shift
  [[ $# -ge 1 ]] || { echo "usage: $0 run COMMAND [ARG…]" >&2; exit 1; }
  cd "$HERE/.."
  command -v "$1" >/dev/null 2>&1 || { echo "$1: not found with the PATH of clevercloud/.cron-env (is cron-env.sh run by CC_PRE_RUN_HOOK?)" >&2; exit 127; }
  echo "$(date -u +%FT%TZ) start: $1 ($(command -v "$1"))"
  exec "$@"                                        # argv as written in cron.json (the app's own command)
fi

METHOD="${1:-}"; ROUTE="${2:-}"
[[ "$METHOD" =~ ^(GET|POST)$ && "$ROUTE" =~ ^/[A-Za-z0-9/_.-]*$ ]] || { echo "usage: $0 GET|POST /path" >&2; exit 1; }
BASE="${CRON_BASE_URL:-http://127.0.0.1:${PORT:-8080}}"
TOKEN_VAR="${CRON_TOKEN_VAR:-CRON_SECRET}"
[[ "$TOKEN_VAR" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "CRON_TOKEN_VAR must be a variable name" >&2; exit 1; }

echo "$(date -u +%FT%TZ) start $METHOD $ROUTE"
umask 077
OUT="$(mktemp)"; HDR="$(mktemp)"                   # no shared /tmp/<job>.out between runs
trap 'rm -f "$OUT" "$HDR"' EXIT
ARGS=(-sS -o "$OUT" -w '%{http_code}' -X "$METHOD" --max-time 300)
if [[ -n "${!TOKEN_VAR:-}" ]]; then
  printf 'Authorization: Bearer %s\n' "${!TOKEN_VAR}" > "$HDR"   # a 600 file, never curl's argv
  ARGS+=(-H "@$HDR")
elif [[ "${CRON_NO_TOKEN:-}" != "1" ]]; then
  echo "$TOKEN_VAR is empty or missing from clevercloud/.cron-env (list it in CRON_VARIABLES): job refused. A public route on purpose: CRON_NO_TOKEN=1" >&2
  exit 1
fi
[[ "$METHOD" == POST ]] && ARGS+=(-H 'Content-Type: application/json' -d '{}')
CODE="$(curl -q "${ARGS[@]}" "$BASE$ROUTE")" || CODE=000          # -q first: no ~/.curlrc (a --location there would change the answer)

echo "$(date -u +%FT%TZ) $METHOD $ROUTE -> HTTP $CODE"
head -c 500 "$OUT"; echo
[[ "$CODE" =~ ^2 ]] || exit 1                       # a failed job must show in the logs
