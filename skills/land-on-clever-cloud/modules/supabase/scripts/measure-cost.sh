#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Times what costs during a cutover. JSON output.
#
#   SUPABASE_DB_URL_FILE=~/sb-db-url ./measure-cost.sh [--keep FOLDER]     (600 file, outside the repo)
#   SUPABASE_DB_URL='postgresql://...' ./measure-cost.sh                   (a human's own shell only)
#   TARGET_DB_URL='postgresql://...'   ./measure-cost.sh --target-index
#
# SOURCE (default) — READ-ONLY, nothing is written to the database:
#   1. pg_dump of the schema, of the data, of auth+storage: duration and size;
#   2. the ratio dump size / database size, which is NOT 1;
#   3. the inventory of the vector indexes (size), without touching them.
# The dumps hold the password hashes, the session tokens and all the
# application data: they are NOT written, only counted (pg_dump | wc -c).
# --keep FOLDER writes them (folder 700, files 600), outside the current
# repository, and the operator must delete them.
#
# TARGET (--target-index), after the data migration, BEFORE the cutover:
# rebuild duration of each vector index (ivfflat, hnsw; INDEX_PATTERN for
# another type) at the maintenance_work_mem of the plan, then at 256MB. Each
# measure runs in ONE rolled-back transaction (drop + create + rollback): a
# cut never leaves the database without its index. The table is locked
# during the measure: never on a database in service. Refuses a Supabase host.
#
# Requirements: native pg_dump and psql. Password in a 600 PGPASSFILE
# (lib-pg.sh), never in the argv of psql or pg_dump. On a small plan, any
# concurrent load skews the durations by a factor of 5: measure on an idle
# database.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-pg.sh
. "$HERE/lib-pg.sh"
WORK_DIR="$(mktemp -d)"
trap 'c=$?; pg_cleanup; rm -rf "$WORK_DIR"; exit $c' EXIT

MODE=source; KEEP=""
case "${1:-}" in
  --target-index) MODE=target ;;
  --keep)         KEEP="${2:-}"; [[ -n "$KEEP" ]] || { echo "--keep FOLDER" >&2; exit 1; } ;;
  "")             ;;
  *)              echo "unknown argument: $1 (--help)" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------
if [[ "$MODE" == "target" ]]; then
  VAR=TARGET_DB_URL
  [[ -n "${TARGET_DB_URL:-}" ]] || VAR=POSTGRESQL_ADDON_URI
  [[ -n "${!VAR:-}" ]] || { echo "TARGET_DB_URL (or POSTGRESQL_ADDON_URI) is not set" >&2; exit 1; }
  pg_prepare "$VAR" || exit 1
  unset TARGET_DB_URL POSTGRESQL_ADDON_URI
  case "$PGHOST" in
    *supabase.co|*supabase.com|*supabase.co.|*supabase.com.)
      echo "REFUSED: $PGHOST is a Supabase host. Indexes are measured on the TARGET, the source stays read-only." >&2
      exit 1 ;;
  esac
  INDEX_JSON=$(psql -qtAX -v ON_ERROR_STOP=1 -v pattern="${INDEX_PATTERN:-ivfflat|hnsw}" <<'SQL'
select coalesce(jsonb_agg(jsonb_build_object(
  'index', format('%I.%I', schemaname, indexname),
  'drop', format('drop index %I.%I', schemaname, indexname),
  'definition', indexdef,
  'reindex_rest', (indexname like '%\_ccnew%'),
  'size_bytes', pg_relation_size(format('%I.%I', schemaname, indexname)::regclass))), '[]'::jsonb)::text
  from pg_indexes where indexdef ~* :'pattern' and schemaname not in ('pg_catalog', 'information_schema');
SQL
)
  python3 -I - "$INDEX_JSON" <<'PY'
import json, subprocess, sys, time
out = []
for idx in json.loads(sys.argv[1]):
    entry = {"index": idx["index"], "size_bytes": idx["size_bytes"]}
    if idx["reindex_rest"]:
        entry.update(TO_CLEAN_UP=True, note="leftover of a failed REINDEX CONCURRENTLY: drop it, do not measure it")
        out.append(entry)
        continue

    def measure(mwm):
        # One single transaction, always rolled back: the index is never lost.
        script = "begin;\nset local statement_timeout = '30min';\n"
        if mwm:
            script += "set local maintenance_work_mem = '%s';\n" % mwm
        script += idx["drop"] + ";\n" + idx["definition"] + ";\nrollback;\n"
        t = time.time()
        p = subprocess.run(["psql", "-qX", "-v", "ON_ERROR_STOP=1", "-f", "-"], input=script,
                           capture_output=True, text=True, timeout=2000)
        return p.returncode, (p.stderr or "").strip(), round(time.time() - t, 1)

    rc, err, d = measure(None)
    if rc == 0:
        entry.update(default_sufficient=True, duration_s=d)
    else:
        entry.update(default_sufficient=False, default_error=(err.splitlines() or [""])[0][:160])
        rc, err, d = measure("256MB")
        if rc == 0:
            entry.update(duration_s=d, maintenance_work_mem_required="256MB")
        else:
            entry.update(error=(err.splitlines() or ["failed"])[0][:160])
    out.append(entry)
print(json.dumps({"mode": "target-index", "transactions": "rolled back (rollback)", "vector_indexes": out}, indent=1))
PY
  exit 0
fi

# ---------------------------------------------------------------------------
# SOURCE: read-only.
[[ -n "${SUPABASE_DB_URL:-}${SUPABASE_DB_URL_FILE:-}" ]] || { echo "SUPABASE_DB_URL_FILE (600 file) or SUPABASE_DB_URL is not set" >&2; exit 1; }
if [[ -n "$KEEP" ]]; then
  # Outside ANY repository: a dump in a repository ends up committed. The check is made on the
  # target folder itself (git -C), not on the current directory: `--keep ~/other-project/dumps`
  # launched from elsewhere is refused too. A home folder that is itself a repository is not
  # counted (the repository of the dotfiles), but a project repository inside it is.
  mkdir -p "$KEEP"; chmod 700 "$KEEP"
  KEEP="$(cd "$KEEP" && pwd -P)"
  if REPO="$(git -C "$KEEP" rev-parse --show-toplevel 2>/dev/null)" && [[ "$(cd "$REPO" && pwd -P)" != "$(cd ~ && pwd -P)" ]]; then
    echo "REFUSED: $KEEP is inside the repository $REPO. Choose a folder outside any repository." >&2; exit 1
  fi
fi
pg_prepare SUPABASE_DB_URL || exit 1
unset SUPABASE_DB_URL
PSQL=(psql -qtAX -v ON_ERROR_STOP=1)

# timed <error_file> <output|-> <command...> -> "<duration>;<code>;<bytes>"
# The dump is counted (wc -c) without being written, except with --keep. Exit
# code and stderr are kept: a failing pg_dump otherwise gave a credible and wrong figure.
timed() {
  local err="$1" file="$2" start end code bytes
  shift 2
  start=$(date +%s)
  set +e
  if [[ "$file" == "-" ]]; then
    bytes=$("$@" 2>"$err" | wc -c | tr -d ' '); code=${PIPESTATUS[0]}
  else
    "$@" -f "$file" 2>"$err"; code=$?
    bytes=$(wc -c < "$file" 2>/dev/null | tr -d ' ' || echo 0)
  fi
  set -e
  end=$(date +%s)
  echo "$((end - start));$code;${bytes:-0}"
}
duration() { echo "$1" | cut -d';' -f1; }
code()     { echo "$1" | cut -d';' -f2; }
bytes()    { echo "$1" | cut -d';' -f3; }
warn_fail() {                    # warn_fail <name> <result> <error_file>
  if [[ "$(code "$2")" != "0" ]]; then
    echo "FAILED $1 (code $(code "$2")):" >&2
    tail -3 "$3" >&2
  fi
}
dest() { if [[ -n "$KEEP" ]]; then echo "$KEEP/$1"; else echo -; fi; }

# Local pg_dump >= server version, otherwise it refuses (or produces an
# incomplete dump). Checked BEFORE timing.
SERVER_V=$("${PSQL[@]}" -c "show server_version_num") \
  || { echo "cannot connect (psql)" >&2; exit 1; }
DUMP_V=$(pg_dump --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')
if [[ "$DUMP_V" -lt "$((SERVER_V / 10000))" ]]; then
  echo "pg_dump $DUMP_V < server $((SERVER_V / 10000)): install a pg_dump >= $((SERVER_V / 10000))" >&2
  exit 1
fi

# The application schemas: everything except the platform ones. One name per
# line, quoted by the server (quote_ident), passed as a separate argument: a
# name with a space or a dash neither breaks nor injects anything into pg_dump.
SCHEMAS=()
while IFS= read -r s; do
  [[ -n "$s" ]] && SCHEMAS+=("--schema=$s")
done < <("${PSQL[@]}" -c "
  select quote_ident(nspname)
    from pg_namespace
   where nspname not like 'pg\\_%'
     and nspname not in ('information_schema','auth','storage','realtime','vault',
                         'extensions','graphql','graphql_public','net','cron',
                         'pgsodium','pgsodium_masks','supabase_migrations',
                         'supabase_functions','pgmq','pgmq_public')
   order by 1" || echo "__ERROR__")
[[ "${SCHEMAS[*]:-}" != *__ERROR__* ]] || { echo "cannot read the list of application schemas" >&2; exit 1; }
# Empty list = pg_dump would dump the WHOLE database, auth included: refuse.
[[ ${#SCHEMAS[@]} -gt 0 ]] || { echo "no application schema found: stopping (otherwise the whole database is dumped)" >&2; exit 1; }

echo "-- schema dump" >&2
R_SCHEMA=$(timed "$WORK_DIR/schema.err" "$(dest dump_schema.sql)" pg_dump --schema-only "${SCHEMAS[@]}")
warn_fail "schema dump" "$R_SCHEMA" "$WORK_DIR/schema.err"
echo "-- data dump (the longest)" >&2
R_DATA=$(timed "$WORK_DIR/data.err" "$(dest dump_data.sql)" pg_dump --data-only "${SCHEMAS[@]}")
warn_fail "data dump" "$R_DATA" "$WORK_DIR/data.err"
echo "-- auth and storage dump" >&2
R_AUTH=$(timed "$WORK_DIR/auth.err" "$(dest dump_auth.sql)" pg_dump --schema=auth --schema=storage)
warn_fail "auth dump" "$R_AUTH" "$WORK_DIR/auth.err"

DB_BYTES=$("${PSQL[@]}" -c "select pg_database_size(current_database())")

# Inventory only: the rebuild is measured on the TARGET.
INDEX_JSON=$("${PSQL[@]}" -c "begin read only;" -c "
  select coalesce(jsonb_agg(jsonb_build_object(
    'index', format('%I.%I', schemaname, indexname),
    'size_bytes', pg_relation_size(format('%I.%I', schemaname, indexname)::regclass))), '[]'::jsonb)::text
    from pg_indexes where indexdef ~* 'ivfflat|hnsw'" -c "rollback;" 2>/dev/null | grep -v '^\(BEGIN\|ROLLBACK\)$' || echo '[]')

if [[ -n "$KEEP" ]]; then
  chmod 600 "$KEEP"/dump_*.sql 2>/dev/null || true
  echo "WARNING: dumps kept in $KEEP (hashes, sessions, application data). Delete them after use: rm -rf \"$KEEP\"" >&2
fi

cat <<JSON
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "database_bytes": ${DB_BYTES:-null},
  "dumps": {
    "schema": { "duration_s": $(duration "$R_SCHEMA"), "code": $(code "$R_SCHEMA"), "bytes": $(bytes "$R_SCHEMA") },
    "data":   { "duration_s": $(duration "$R_DATA"),   "code": $(code "$R_DATA"),   "bytes": $(bytes "$R_DATA") },
    "auth":   { "duration_s": $(duration "$R_AUTH"),   "code": $(code "$R_AUTH"),   "bytes": $(bytes "$R_AUTH") }
  },
  "dumps_kept": $(if [[ -n "$KEEP" ]]; then echo true; else echo false; fi),
  "source_vector_indexes": ${INDEX_JSON:-[]},
  "index_rebuild": "not measured on the source (read-only): measure-cost.sh --target-index after the data migration",
  "read": [
    "MEASURE ON AN IDLE DATABASE. On a small plan, contention skews everything: the same dump took 102 s then 495 s depending on whether another load was running. Factor of 5. Announce nothing from a single measure taken while something else queries the database.",
    "The text dump inflates vectors: pg_dump writes [0.123,...] where the table stores binary float4. Extrapolate from the dump bytes, never from the table size.",
    "On the target, default_sufficient=false means the index CANNOT BE REBUILT on this plan, so VACUUM FULL cannot run either. It is a sizing criterion, not a detail.",
    "Add the transfer and restore time: these measures cover only the extraction.",
    "Announce a range, not a figure: low bound on an idle database, high bound under load."
  ]
}
JSON
