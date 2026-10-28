#!/bin/bash
# ---------------------------------------------------------------------------
# cron-env.sh — the variables a Clever Cloud cron job needs, written at each instance start.
#
# Copy to clevercloud/cron-env.sh in the app, make it executable BEFORE the commit
# (git update-index --chmod=+x clevercloud/cron-env.sh), and set on the app:
#
#   CC_PRE_RUN_HOOK="bash clevercloud/cron-env.sh"
#   CRON_VARIABLES="CRON_SECRET"            # NAMES of the app variables the job needs, space-separated
#   CRON_RUNS_HERE=1                        # on the ONE app that runs the jobs (see cron-job.sh)
#
# THE TRAP IT SOLVES: jobs of clevercloud/cron.json run with an EMPTY environment (HOME, LC_*,
# LOGNAME, PATH, PWD, SHELL, SHLVL, USER only): no app variable, no INSTANCE_NUMBER, no PORT
# (observed with a debug job, 2026-10-25). The hook runs in the instance's environment: it copies
# INSTANCE_NUMBER, PORT, CRON_BASE_URL, CRON_TOKEN_VAR, CRON_NO_TOKEN and CRON_RUNS_HERE when set, plus every name
# listed in CRON_VARIABLES, into clevercloud/.cron-env (mode 600), which cron-job.sh sources. A
# listed name absent from the instance gives a warning (name only) in the start logs.
#
# THE SECOND TRAP: the job's PATH is the system one, so `python purge.py` runs the system python
# without the app's dependencies (ModuleNotFoundError, observed 3 times on 2026-10-25), and `node`
# or `npx` may not be the app's. The hook therefore also copies the runtime's own PATH and, when
# set, PYTHONPATH, VIRTUAL_ENV, NODE_PATH, GEM_HOME, GEM_PATH, BUNDLE_PATH, JAVA_HOME, GOPATH, and
# appends the app's node_modules/.bin (after the runtime's PATH: never shadowing system tools). It prints which python3 / node the jobs
# will use (paths only) so a wrong interpreter shows in the start logs.
#
# The file lives on the instance only: add clevercloud/.cron-env to .gitignore anyway. No value
# is printed. Another CC_PRE_RUN_HOOK already in use: one script that calls both.
# Shared by the whole skill (a Vercel cron, a pg_cron job of a Supabase migration…).
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
FILE="$HERE/.cron-env"
NAMES="INSTANCE_NUMBER PORT CRON_BASE_URL CRON_TOKEN_VAR CRON_NO_TOKEN CRON_RUNS_HERE ${CRON_VARIABLES:-}"
# the runtime of the app (interpreter and dependencies), copied when set
RUNTIME_NAMES="PATH PYTHONPATH VIRTUAL_ENV NODE_PATH GEM_HOME GEM_PATH BUNDLE_PATH JAVA_HOME GOPATH"
APP_DIR="$(cd "$HERE/.." && pwd)"
# after the runtime's PATH: a package must not shadow mktemp, date, curl, chmod… for the hook and the job
if [[ -d "$APP_DIR/node_modules/.bin" ]]; then PATH="$PATH:$APP_DIR/node_modules/.bin"; fi

umask 077
TMP="$(mktemp "$FILE.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
n=0
for name in $RUNTIME_NAMES $NAMES; do
  [[ "$name" =~ ^[A-Z_][A-Z0-9_]*$ ]] || { echo "cron-env: refused name: $name" >&2; exit 1; }
  if [[ "$name" == PATH && -n "${PATH:-}" ]]; then
    printf 'export PATH=%q"${PATH:+:$PATH}"\n' "$PATH" >> "$TMP"   # the runtime's PATH first, the job's own after it
    n=$((n + 1))
  elif [[ -n "${!name+x}" ]]; then
    printf 'export %s=%q\n' "$name" "${!name}" >> "$TMP"     # %q: read back as is by bash
    n=$((n + 1))
  elif [[ " ${CRON_VARIABLES:-} " == *" $name "* ]]; then
    echo "cron-env: WARNING: $name is listed in CRON_VARIABLES but not set on the instance" >&2   # name only
  fi
done
mv "$TMP" "$FILE"
chmod 600 "$FILE"
trap - EXIT
echo "cron-env: $n variable(s) written for the scheduled jobs (values not shown)"
# diagnostic (paths only): the interpreters the jobs will find with this PATH
for tool in python3 python node; do
  if command -v "$tool" >/dev/null 2>&1; then echo "cron-env: jobs will use $tool = $(command -v "$tool")"; fi
done
