#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Measured survey of a Supabase project, before migration.
#
#   SUPABASE_DB_URL_FILE=~/sb-db-url ./survey-supabase.sh > survey.json   (600 file outside the repo:
#                                     the documented way — the URL never sits on a command line)
#   SUPABASE_DB_URL='postgresql://...' ./survey-supabase.sh > survey.json (a human's own shell)
#   ./survey-supabase.sh --queries > survey.sql          # MCP path, no password
#   SUPABASE_DB_URL='...' ./survey-supabase.sh --objects > objects.json
#   ./survey-supabase.sh --objects-query > objects.sql   # objects list, MCP path
#   ./survey-supabase.sh --extract mcp-output.txt > survey.json          # MCP path
#   ./survey-supabase.sh --extract objects-output.txt objects > objects.json
#
# It concludes nothing: it measures. The migration plan comes from its output,
# never from reading the repository. Every query is READ-ONLY.
#
# Two paths:
#   - psql: SUPABASE_DB_URL is set (the "Session pooler" string from the
#     console; the direct connection is IPv6 and fails on an IPv4 network);
#   - Supabase MCP: no password and no psql. `--queries` prints ONE SINGLE
#     block (begin read only; one jsonb select; rollback) to pass as is to
#     `execute_sql`: its result is survey.json. Save the tool output AS IS
#     (raw copy, do not retype or reformat it) to a file, then `--extract`
#     takes the JSON out of the column, validates it and adds the HTTP probes.
#     Queries on a schema that may be missing (cron, vault, net, storage) are
#     guarded: null, no error. If the block still fails (permissions),
#     `--queries-detail` prints one query per key; the one that fails is
#     null: note it, do not invent it.
#
# List of the Storage objects (input of migrate-storage.py): `--objects`
# (psql) or `--objects-query` (MCP). Never in survey.json, which keeps only
# counts and volumes.
#
# Extra HTTP probes (optional, publishable key only):
#   SUPABASE_URL=https://<ref>.supabase.co SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
#
# Password hashes are NEVER printed: only their count per algorithm.
# Portable to bash 3.2.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=lib-pg.sh
. "$HERE/lib-pg.sh"
trap 'c=$?; pg_cleanup; [[ -n "${KEY_HEADER:-}" ]] && rm -f "$KEY_HEADER"; exit $c' EXIT

# Schemas of the Supabase platform, excluded from the "application" ones.
PLATFORM="'pg_catalog','information_schema','pg_toast','auth','storage','realtime','vault',
  'extensions','graphql','graphql_public','net','cron','pgsodium','pgsodium_masks',
  'supabase_migrations','supabase_functions','pgmq','pgmq_public','pgbouncer','_realtime','_analytics'"

KEYS=(); QUERIES=(); GUARDS=()
r()  { KEYS+=("$1"); GUARDS+=("");   QUERIES+=("$2"); }
# rg <key> <relation> <query>: query on a schema that may be missing. A
# missing relation makes the PARSING of the whole query fail (coalesce does
# not catch anything): so the query goes through dynamic SQL (query_to_xml),
# which is parsed only if to_regclass finds the relation. Base64: the JSON
# crosses the XML without escaping.
rg() { KEYS+=("$1"); GUARDS+=("$2"); QUERIES+=("$3"); }

# sql_key <index> -> jsonb expression of the key (null if the guarded relation is missing)
sql_key() {
  local q="${QUERIES[$1]}" g="${GUARDS[$1]}"
  if [[ -z "$g" ]]; then printf '(%s)' "$q"; return; fi
  printf "(case when to_regclass('%s') is null then null else convert_from(decode((xpath('/row/j/text()', query_to_xml(\$g\$ select encode(convert_to((%s)::text, 'UTF8'), 'base64') as j \$g\$, false, true, '')))[1]::text, 'base64'), 'UTF8')::jsonb end)" "$g" "$q"
}

r server "
  select jsonb_build_object(
    'version', current_setting('server_version'),
    'database', current_database(),
    'size', pg_size_pretty(pg_database_size(current_database())),
    'size_bytes', pg_database_size(current_database()),
    'statement_timeout', current_setting('statement_timeout'),
    'maintenance_work_mem', current_setting('maintenance_work_mem'),
    'work_mem', current_setting('work_mem'),
    'max_connections', current_setting('max_connections'))"

r extensions "
  select jsonb_agg(jsonb_build_object('name', extname, 'version', extversion, 'schema', n.nspname) order by extname)
    from pg_extension e join pg_namespace n on n.oid = e.extnamespace"

r transparent_encryption "
  select jsonb_agg(jsonb_build_object('object', objoid::regclass::text, 'column', objsubid, 'label', label))
    from pg_seclabel where provider = 'pgsodium'"

r roles "
  select jsonb_agg(jsonb_build_object(
    'name', rolname, 'bypassrls', rolbypassrls, 'superuser', rolsuper,
    'login', rolcanlogin, 'member_of',
    (select coalesce(jsonb_agg(g.rolname), '[]'::jsonb) from pg_auth_members m
      join pg_roles g on g.oid = m.roleid where m.member = r.oid)) order by rolname)
    from pg_roles r where rolname not like 'pg\\_%'"

r schemas "
  select jsonb_agg(jsonb_build_object('name', nspname,
    'tables', (select count(*) from pg_tables where schemaname = nspname)) order by nspname)
    from pg_namespace
   where nspname not like 'pg\\_%' and nspname <> 'information_schema'"

# Exact count(*), not reltuples (which is -1 on a table never analyzed).
r tables "
  select jsonb_agg(jsonb_build_object(
    'name', t.schemaname || '.' || t.tablename,
    'exact_rows', (xpath('/row/n/text()', query_to_xml(
        format('select count(*) as n from %I.%I', t.schemaname, t.tablename), false, true, '')))[1]::text::bigint,
    'size', pg_size_pretty(pg_total_relation_size(c.oid)),
    'size_bytes', pg_total_relation_size(c.oid),
    'rls', c.relrowsecurity, 'force_rls', c.relforcerowsecurity)
    order by pg_total_relation_size(c.oid) desc)
    from pg_tables t
    join pg_namespace n on n.nspname = t.schemaname
    join pg_class c on c.relnamespace = n.oid and c.relname = t.tablename
   where t.schemaname not in ($PLATFORM)"

# DDL of the application tables: columns, identity, defaults, constraints (CHECK, FK, PK, UNIQUE).
r business_ddl "
  select jsonb_object_agg(n.nspname||'.'||c.relname, jsonb_build_object(
    'columns', (select jsonb_agg(jsonb_build_object(
        'name', a.attname, 'type', format_type(a.atttypid, a.atttypmod),
        'not_null', a.attnotnull, 'identity', nullif(a.attidentity, ''),
        'default', pg_get_expr(d.adbin, d.adrelid)) order by a.attnum)
        from pg_attribute a left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
       where a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped),
    'constraints', (select jsonb_agg(conname || ' ' || pg_get_constraintdef(k.oid) order by conname)
        from pg_constraint k where k.conrelid = c.oid)))
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where c.relkind in ('r','p') and n.nspname not in ($PLATFORM)"

r policies "
  select jsonb_build_object('total', count(*),
    'detail', jsonb_agg(jsonb_build_object('table', schemaname||'.'||tablename, 'name', policyname,
      'command', cmd, 'roles', roles, 'permissive', permissive,
      'using', qual, 'with_check', with_check) order by schemaname, tablename, policyname))
    from pg_policies where schemaname not in ('pg_catalog')"

r business_grants "
  select jsonb_object_agg(t, g) from (
    select table_schema||'.'||table_name t,
           jsonb_object_agg(grantee, privs) g
      from (select table_schema, table_name, grantee, string_agg(privilege_type, ',' order by privilege_type) privs
              from information_schema.role_table_grants
             where table_schema not in ($PLATFORM)
             group by 1,2,3) x group by 1) y"

r business_sequences "
  select jsonb_object_agg(schemaname||'.'||sequencename, last_value)
    from pg_sequences where schemaname not in ($PLATFORM)"

r business_functions "
  select jsonb_agg(jsonb_build_object('name', n.nspname||'.'||p.proname,
    'security_definer', p.prosecdef, 'search_path_pinned', (p.proconfig is not null),
    'calls_auth_uid', (pg_get_functiondef(p.oid) ilike '%auth.uid()%')) order by n.nspname, p.proname)
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname not in ($PLATFORM) and p.prokind in ('f','p')"

# Shape only (name, table, at, events, function): the full definition
# (pg_get_triggerdef) carries the arguments, and a Database Webhook puts its
# headers there (Authorization: Bearer <service_role>).
r triggers "
  select jsonb_agg(jsonb_build_object('name', tgname, 'table', tgrelid::regclass::text,
    'at', case when tgtype & 2 = 2 then 'before' when tgtype & 64 = 64 then 'instead of' else 'after' end,
    'events', array_remove(array[case when tgtype & 4 = 4 then 'insert' end, case when tgtype & 16 = 16 then 'update' end,
                                     case when tgtype & 8 = 8 then 'delete' end, case when tgtype & 32 = 32 then 'truncate' end], null),
    'per_row', (tgtype & 1 = 1), 'function', tgfoid::regproc::text, 'arg_count', tgnargs))
    from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_namespace n on n.oid = c.relnamespace
   where not tgisinternal and (n.nspname not in ($PLATFORM) or tgrelid = 'auth.users'::regclass)"

r vector_indexes "
  select jsonb_agg(jsonb_build_object('name', i.indexname, 'table', i.schemaname||'.'||i.tablename,
    'definition', i.indexdef,
    'valid', x.indisvalid,
    'reindex_rest', (i.indexname like '%\\_ccnew%'),
    'size', pg_size_pretty(pg_relation_size(c.oid))))
    from pg_indexes i
    join pg_class c on c.relname = i.indexname
      and c.relnamespace = i.schemaname::text::regnamespace
    join pg_index x on x.indexrelid = c.oid
    where i.indexdef ~* 'ivfflat|hnsw'"

# Masked command (JWT, sb_secret_, token after Bearer): a job that calls an
# Edge Function through net.http_post often carries the service_role key.
# The whole document then goes through the shared JSON masker (common.py mask-json).
rg cron_jobs cron.job "
  select jsonb_agg(jsonb_build_object('name', jobname, 'schedule', schedule,
    'active', active, 'called_url', substring(command from 'https?://[^''\"[:space:]?]+'),
    'command', regexp_replace(regexp_replace(regexp_replace(left(command, 200),
        'eyJ[A-Za-z0-9_-]+[.][A-Za-z0-9_-]+[.][A-Za-z0-9_-]+', '***', 'g'),
        'sb_secret_[A-Za-z0-9_-]+', 'sb_secret_***', 'g'),
        '([Bb]earer[[:space:]]+)[A-Za-z0-9._~+/=-]{8,}', '\\1***', 'g')) order by jobname) from cron.job"

# Names and lengths only: never the value of a secret.
rg vault_secrets vault.decrypted_secrets "
  select jsonb_agg(jsonb_build_object('name', name, 'length', length(coalesce(decrypted_secret,'')))
    order by name) from vault.decrypted_secrets"

# Storage: allowed_mime_types and file_size_limit are read from the DATABASE
# (the config.toml of the repository can lie). The global limit of the project
# is not in the database: read it in the console (Storage > Settings).
# owner_id (text) replaced owner (uuid): read through to_jsonb to work on both versions.
rg buckets storage.buckets "
  select jsonb_agg(jsonb_build_object('id', id, 'name', name, 'public', public,
    'limit_bytes', file_size_limit, 'mime_types', allowed_mime_types,
    'objects', (select count(*) from storage.objects o where o.bucket_id = b.id),
    'volume_bytes', (select coalesce(sum((metadata->>'size')::bigint),0) from storage.objects o where o.bucket_id = b.id),
    'largest_bytes', (select max((metadata->>'size')::bigint) from storage.objects o where o.bucket_id = b.id))
    order by id) from storage.buckets b"

# What decides the file migration (reference/storage.md): volume, active types
# (HTML/SVG/XML = stored XSS once served from the app), policies.
rg storage storage.objects "
  select jsonb_build_object(
    'buckets', (select count(*) from storage.buckets),
    'public_buckets', (select count(*) from storage.buckets where public),
    'objects', count(*),
    'volume_bytes', coalesce(sum((metadata->>'size')::bigint), 0),
    'largest_bytes', max((metadata->>'size')::bigint),
    'without_size', count(*) filter (where metadata->>'size' is null),
    'owners', count(distinct coalesce(to_jsonb(o)->>'owner_id', to_jsonb(o)->>'owner')),
    'without_owner', count(*) filter (where coalesce(to_jsonb(o)->>'owner_id', to_jsonb(o)->>'owner') is null),
    'multipart_etag', count(*) filter (where metadata->>'eTag' like '%-%'),
    'mime_types', (select jsonb_object_agg(coalesce(m, 'unknown'), n) from
        (select metadata->>'mimetype' m, count(*) n from storage.objects group by 1) t),
    'active_types', count(*) filter (where lower(coalesce(metadata->>'mimetype', '')) ~ '(html|svg|xml|javascript)'
        or name ~* '[.](html?|xhtml|svgz?|xml|js|mjs)$'),
    'policies_objects', (select count(*) from pg_policies where schemaname = 'storage' and tablename = 'objects'),
    'insert_policies_bucket_only', (select coalesce(jsonb_agg(policyname order by policyname), '[]'::jsonb) from pg_policies
        where schemaname = 'storage' and tablename = 'objects' and cmd in ('INSERT', 'ALL')
          and coalesce(with_check, '') !~* 'auth[.]uid[(][)]|owner|foldername|auth[.]jwt'),
    'policies_by_command', (select jsonb_object_agg(cmd, n) from
        (select cmd, count(*) n from pg_policies where schemaname = 'storage' and tablename = 'objects' group by 1) p),
    'policies_by_operation', (select count(*) from pg_policies where schemaname = 'storage'
        and (coalesce(qual, '') || coalesce(with_check, '')) ~ 'allow_any_operation|allow_only_operation|storage[.]operation'))
    from storage.objects o"

r realtime "
  select jsonb_build_object('publication', pubname,
    'tables', jsonb_agg(schemaname||'.'||tablename order by tablename))
    from pg_publication_tables where pubname = 'supabase_realtime' group by pubname"

r webhooks "
  select jsonb_build_object(
    'schema_supabase_functions', exists(select 1 from pg_namespace where nspname='supabase_functions'),
    'triggers', (select coalesce(jsonb_agg(jsonb_build_object('name', tgname, 'table', tgrelid::regclass::text)), '[]'::jsonb)
                   from pg_trigger t
                  where not tgisinternal
                    and pg_get_triggerdef(t.oid) ilike '%supabase_functions.http_request%'))"

# Hashes: count per algorithm prefix ($2a$ = bcrypt), never the values.
r auth "
  select jsonb_build_object(
    'users', (select count(*) from auth.users),
    'confirmed', (select count(*) from auth.users where email_confirmed_at is not null),
    'anonymous', (select count(*) from auth.users where is_anonymous),
    'banned', (select count(*) from auth.users where banned_until > now()),
    'case_duplicate_emails', (select count(*) - count(distinct lower(email)) from auth.users where email is not null),
    'password_hash', (select jsonb_object_agg(coalesce(p, 'none'), n) from
                   (select left(nullif(encrypted_password, ''), 4) p, count(*) n from auth.users group by 1) h),
    'password_hash_cost', (select jsonb_object_agg(c, n) from
                   (select substr(encrypted_password, 5, 2) c, count(*) n from auth.users
                     where encrypted_password ~ '^[$]2[aby][$][0-9]{2}[$]' group by 1) k),
    'providers', (select jsonb_object_agg(provider, n) from
                   (select provider, count(*) n from auth.identities group by 1) f),
    'identities', (select count(*) from auth.identities),
    'sessions', (select count(*) from auth.sessions),
    'refresh_tokens', (select count(*) from auth.refresh_tokens),
    'mfa_factors', (select count(*) from auth.mfa_factors))"

rg recent_outbound_calls net._http_response "
  select jsonb_build_object('responses', count(*),
    'last_codes', jsonb_agg(distinct status_code)) from net._http_response"

# ---------------------------------------------------------------------------
# Objects list (input of migrate-storage.py): bucket, name, owner, type,
# size, md5 (eTag, except multipart upload: "md5-N"). Stable order.
# Large volume through MCP: add "where (bucket_id, name) > ('<last>', '<last>')"
# and "limit 5000" in the subquery, and concatenate the pages.
OBJECTS_SQL="
  select coalesce(jsonb_agg(jsonb_build_object(
    'bucket', bucket_id, 'name', name,
    'owner', coalesce(to_jsonb(o)->>'owner_id', to_jsonb(o)->>'owner'),
    'mimetype', metadata->>'mimetype', 'size', (metadata->>'size')::bigint,
    'etag', trim(both '\"' from metadata->>'eTag'), 'cache_control', metadata->>'cacheControl')
    order by bucket_id, name), '[]'::jsonb)
    from (select * from storage.objects order by bucket_id, name) o"

# HTTP probes, publishable key only, passed to curl through a 600 file
# (-H @file) and not as an argument. Valid HTTP code, otherwise null (never
# "000null": invalid JSON).
# Names accepted (#4): SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL; SUPABASE_PUBLISHABLE_KEY, else
# NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_ANON_KEY. Without
# them the probes are null AND a warning says so (never a silent null).
SUPABASE_URL="${SUPABASE_URL:-${NEXT_PUBLIC_SUPABASE_URL:-}}"
SUPABASE_PUBLISHABLE_KEY="${SUPABASE_PUBLISHABLE_KEY:-${NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY:-${NEXT_PUBLIC_SUPABASE_ANON_KEY:-${SUPABASE_ANON_KEY:-}}}}"
PROBE_WARNED=""
probe_warn() {
  if [[ -z "$PROBE_WARNED" && ( -z "$SUPABASE_URL" || -z "$SUPABASE_PUBLISHABLE_KEY" ) ]]; then
    echo "survey: WARNING — HTTP probes skipped (null): no SUPABASE_URL / NEXT_PUBLIC_SUPABASE_URL or no publishable/anon key in the environment" >&2
    PROBE_WARNED=1
  fi
}
KEY_HEADER=""
key_header() {
  if [[ -z "$KEY_HEADER" ]]; then
    KEY_HEADER="$(umask 077; mktemp)"
    printf 'apikey: %s\n' "$SUPABASE_PUBLISHABLE_KEY" > "$KEY_HEADER"
  fi
}
http() {   # http <path> -> HTTP code, or null
  local c=""
  if [[ -n "${SUPABASE_URL:-}" && -n "${SUPABASE_PUBLISHABLE_KEY:-}" ]]; then
    key_header
    c="$(curl -q -s -o /dev/null -w '%{http_code}' -H @"$KEY_HEADER" "${SUPABASE_URL%/}$1" 2>/dev/null || true)"          # -q first: no ~/.curlrc
  fi
  if [[ "$c" =~ ^[1-5][0-9][0-9]$ ]]; then echo "$c"; else echo null; fi
}
auth_settings() {
  local r=""
  if [[ -n "${SUPABASE_URL:-}" && -n "${SUPABASE_PUBLISHABLE_KEY:-}" ]]; then
    key_header
    r="$(curl -q -sf -H @"$KEY_HEADER" "${SUPABASE_URL%/}/auth/v1/settings" 2>/dev/null || true)"
  fi
  if printf '%s' "$r" | python3 -I -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null; then
    printf '%s\n' "$r"
  else
    echo null
  fi
}

case "${1:-}" in
  --extract)
    # Raw output of execute_sql (text envelope included) -> JSON of the
    # column `survey` (default) or `objects`, validated; HTTP probes added.
    [[ -f "${2:-}" ]] || { echo "usage: $0 --extract <mcp-output.txt> [survey|objects]" >&2; exit 1; }
    COL="${3:-survey}"
    PROBES="{}"
    if [[ "$COL" == "survey" ]]; then
      probe_warn
      PROBES="{\"exposed_api\": {\"graphql_v1\": $(http /graphql/v1), \"rest_v1\": $(http /rest/v1/)}, \"public_auth_settings\": $(auth_settings)}"
    fi
    EXTRACTED="$(python3 -I - "$2" "$COL" "$PROBES" <<'EXTRACT'
import json, sys
text, col = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
try:
    probes = json.loads(sys.argv[3])
except ValueError:
    probes = {}
dec, found, i = json.JSONDecoder(), None, 0
while found is None:
    pos = [p for p in (text.find("[", i), text.find("{", i)) if p >= 0]
    if not pos:
        break
    i = min(pos)
    try:
        v, _ = dec.raw_decode(text, i)
    except ValueError:
        i += 1
        continue
    for row in (v if isinstance(v, list) else [v]):
        if isinstance(row, dict) and col in row:
            found = row[col]
            break
    i += 1
if found is None:
    sys.exit(f"column '{col}' not found: save the RAW output of execute_sql, without editing it")
if isinstance(found, str):            # column returned as JSON text
    found = json.loads(found)
if col == "survey":
    if not isinstance(found, dict) or "method" not in found:
        sys.exit("incomplete survey (key 'method' missing): truncated output? run it again, or use --queries-detail")
    for k, v in probes.items():
        found.setdefault(k, v)
    nulls = sorted(k for k, v in found.items() if v is None)
    if nulls:
        print("null keys (note them, do not invent them): " + ", ".join(nulls), file=sys.stderr)
elif not isinstance(found, list):
    sys.exit(f"'{col}': a list is expected")
json.dump(found, sys.stdout, indent=1, ensure_ascii=False)
print()
EXTRACT
)"
    # The whole document goes through the shared JSON masker before it is printed.
    printf '%s\n' "$EXTRACTED" | python3 -I "$SKILL_ROOT/scripts/lib/common.py" mask-json
    exit $? ;;
  --queries)
    printf -- '-- Full survey in one single execute_sql call (read-only).\n'
    printf -- '-- The result (column survey) is survey.json.\nbegin read only;\nselect jsonb_build_object(\n'
    printf "  'timestamp', to_char(now() at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),\n"
    printf "  'method', 'MCP execute_sql (survey-supabase.sh --queries)'"
    i=0
    while [[ $i -lt ${#KEYS[@]} ]]; do
      printf ",\n  -- %s\n  '%s', coalesce(%s, 'null'::jsonb)" "${KEYS[$i]}" "${KEYS[$i]}" "$(sql_key $i)"
      i=$((i + 1))
    done
    printf '\n) as survey;\nrollback;\n'
    exit 0 ;;
  --queries-detail)
    i=0
    while [[ $i -lt ${#KEYS[@]} ]]; do
      printf -- '-- %s\nselect coalesce(%s, %s::jsonb) as %s;\n\n' \
        "${KEYS[$i]}" "$(sql_key $i)" "'null'" "${KEYS[$i]}"
      i=$((i + 1))
    done
    exit 0 ;;
  --objects-query)
    printf -- '-- List of the Storage objects (read-only): the result is objects.json.\n'
    printf 'begin read only;\nselect (%s) as objects;\nrollback;\n' "$OBJECTS_SQL"
    exit 0 ;;
esac

[[ -n "${SUPABASE_DB_URL:-}${SUPABASE_DB_URL_FILE:-}" ]] || { echo "SUPABASE_DB_URL_FILE (600 file) or SUPABASE_DB_URL is not set (without a password: --queries, MCP path)" >&2; exit 1; }
# Password in a 600 PGPASSFILE, never in the argv of psql (lib-pg.sh).
pg_prepare SUPABASE_DB_URL || exit 1
unset SUPABASE_DB_URL
PSQL=(psql -qtAX -v ON_ERROR_STOP=1)

if [[ "${1:-}" == "--objects" ]]; then
  "${PSQL[@]}" -c "begin read only; select ($OBJECTS_SQL)::text; rollback"
  exit 0
fi

# Returns JSON, or null if the query fails (missing extension, missing
# permissions): a partial survey is better than a survey that stops.
j() {
  "${PSQL[@]}" -c "begin read only; select coalesce((${1}), 'null'::jsonb)::text; rollback" 2>/dev/null \
    || echo null
}

# The whole document goes through the shared JSON masker before it reaches survey.json.
{
echo "{"
echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
echo "  \"method\": \"psql (survey-supabase.sh)\","
i=0
while [[ $i -lt ${#KEYS[@]} ]]; do
  echo "  \"${KEYS[$i]}\": $(j "$(sql_key $i)"),"
  i=$((i + 1))
done
# /graphql/v1 answers even without pg_graphql: the HTTP code alone proves
# nothing, cross it with the list of extensions and the application code.
probe_warn
echo "  \"exposed_api\": { \"graphql_v1\": $(http /graphql/v1), \"rest_v1\": $(http /rest/v1/) },"
echo "  \"public_auth_settings\": $(auth_settings)"
echo "}"
} | python3 -I "$SKILL_ROOT/scripts/lib/common.py" mask-json
