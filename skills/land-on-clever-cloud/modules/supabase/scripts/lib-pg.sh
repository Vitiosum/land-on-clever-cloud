# shellcheck shell=bash
# ---------------------------------------------------------------------------
# psql / pg_dump connection WITHOUT a password on the command line.
#
#   . "$(dirname "$0")/lib-pg.sh"
#   pg_prepare SUPABASE_DB_URL       # name of the VARIABLE that holds the URL
#   psql -qtAX -c "select 1"         # no URL as an argument any more
#
# A postgresql://user:password@host/db URL passed to psql or pg_dump is
# readable by `ps` for the whole command. pg_prepare reads the URL from the
# environment (never from argv), writes the password to a 600 PGPASSFILE in a
# 700 folder (mktemp -d, removed when the script exits), and exports
# PGHOST, PGPORT, PGUSER, PGDATABASE (and PGSSLMODE if the URL gives it):
# psql and pg_dump read them on their own.
#
# THE DOCUMENTED WAY for an agent: <NAME>_FILE (e.g. SUPABASE_DB_URL_FILE=~/sb-db-url), a 600 file
# outside any repository written by the developer — `(umask 077; pbpaste > ~/sb-db-url)` — so the URL
# (and its password) never appears on the agent's command line, in a transcript or in `ps`. The
# variable itself (SUPABASE_DB_URL=…) remains for a human's own shell.
#
# Portable to bash 3.2. Python runs with -I (the cwd does not enter sys.path).
# ---------------------------------------------------------------------------

PG_DIR=""
pg_cleanup() { if [[ -n "$PG_DIR" ]]; then rm -rf "$PG_DIR"; fi; }

pg_prepare() {   # pg_prepare <VARIABLE_NAME>
  local name="$1" line k v fname
  fname="${name}_FILE"
  [[ -n "${!name:-}" || -n "${!fname:-}" ]] || { echo "$name is not set (documented: ${fname}=<600 file outside the repository>)" >&2; return 1; }
  PG_DIR="$(umask 077; mktemp -d)"
  chmod 700 "$PG_DIR"
  # The output holds only non-secret NAME=value lines; the password goes
  # straight to the file.
  while IFS= read -r line; do
    k="${line%%=*}"; v="${line#*=}"
    case "$k" in
      PGHOST|PGPORT|PGUSER|PGDATABASE|PGSSLMODE) export "$k=$v" ;;
      *) echo "pg_prepare: unexpected output ignored" >&2 ;;
    esac
  done < <(PG_URL_VAR="$name" PG_URL_FILE="${!fname:-}" PG_PASSFILE="$PG_DIR/pgpass" python3 -I -c '
import os, stat, subprocess, sys, urllib.parse
f = os.environ.get("PG_URL_FILE")
if f:
    if os.stat(f).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        sys.exit("%s is readable by others: chmod 600 it" % f)
    top = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(f)), "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    if top and os.path.realpath(top) != os.path.realpath(os.path.expanduser("~")):
        sys.exit("%s is inside the repository %s: keep it outside" % (f, top))
    lines = [l.strip() for l in open(f, encoding="utf-8").read().splitlines() if l.strip()]
    if len(lines) != 1:
        sys.exit("%s: one line expected (the URL alone)" % f)
    url = lines[0]
else:
    url = os.environ[os.environ["PG_URL_VAR"]]
u = urllib.parse.urlsplit(url)
if u.scheme not in ("postgres", "postgresql"):
    sys.exit("postgres:// or postgresql:// URL expected")
def esc(x):                      # .pgpass format: "\" and ":" escaped
    return x.replace("\\", "\\\\").replace(":", "\\:")
pwd = urllib.parse.unquote(u.password or "")
fd = os.open(os.environ["PG_PASSFILE"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("*:*:*:*:%s\n" % esc(pwd))
def emit(k, v):
    if v and "\n" not in v:
        print("%s=%s" % (k, v))
emit("PGHOST", urllib.parse.unquote(u.hostname or ""))
emit("PGPORT", str(u.port or ""))
emit("PGUSER", urllib.parse.unquote(u.username or ""))
emit("PGDATABASE", urllib.parse.unquote(u.path.lstrip("/")))
q = urllib.parse.parse_qs(u.query)
emit("PGSSLMODE", (q.get("sslmode") or [""])[0])
')
  [[ -s "$PG_DIR/pgpass" ]] || { echo "pg_prepare: unreadable URL" >&2; return 1; }
  export PGPASSFILE="$PG_DIR/pgpass"
  unset PGPASSWORD
}
