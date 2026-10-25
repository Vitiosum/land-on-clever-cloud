#!/usr/bin/env python3
"""migrate-accounts.py — copy the Supabase accounts (auth.users: ids, e-mails, bcrypt hashes) from the
source to the target PostgreSQL WITHOUT the rows passing through the agent's context.

  python3 -I migrate-accounts.py --db-url-file <600 file> --target-env-file <600 file> [--dry-run]

--db-url-file FILE       the source Session pooler URL, one line, in a 600 file OUTSIDE any repository,
                         written by the developer: `(umask 077; pbpaste > ~/sb-db-url)`. This is the
                         documented way: the URL (with the database password) never sits on an agent's
                         command line, in a transcript or in `ps`. A human may instead export
                         SUPABASE_DB_URL (or the variable named by --source-env) in their own shell.

Why a script: with the MCP connector only, `select … from auth.users` puts every bcrypt hash in the
model's context (and its transcript). Here psql reads the source in a READ ONLY session (checked:
`transaction_read_only` must answer `on`, else nothing is read), writes the rows to a 600 file in a 700 temp folder, and loads them into the
target's auth.users in ONE transaction; the temp folder is removed at the end. Nothing of a row is
printed: counts and an md5 fingerprint of (id, email, hash) on both sides only — and a psql error is
reduced to its SQLSTATE and the names of the table / column / constraint (never DETAIL, never a row).
The target connection requires TLS (sslmode=require; only a loopback test host may go without).

--target-env-file FILE   KEY=VALUE, mode 600, with POSTGRESQL_ADDON_HOST, _PORT, _DB, _USER, _PASSWORD
                         (written by `<skill>/scripts/env-safe.sh env-file --addon <id> --only
                         POSTGRESQL_ADDON_ --out FILE`)
--dry-run                counts on the source, checks that the target auth.users exists and is
                         empty; copies nothing
Columns: those of the list below present on BOTH sides (id, email and encrypted_password required).
The target auth.users must exist (minimal auth schema, reference/data-migration.md §1) and be empty:
a second run is refused. Create the sign-up trigger (handle_new_user) AFTER this import.
Exit: 0 counts and fingerprints equal, 1 a difference or a refusal, 2 usage.
"""
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse

COLUMNS = ["id", "email", "encrypted_password", "email_confirmed_at", "banned_until", "raw_app_meta_data",
           "raw_user_meta_data", "created_at", "updated_at", "last_sign_in_at", "phone", "is_anonymous"]
PG_KEYS = ("POSTGRESQL_ADDON_HOST", "POSTGRESQL_ADDON_PORT", "POSTGRESQL_ADDON_DB", "POSTGRESQL_ADDON_USER", "POSTGRESQL_ADDON_PASSWORD")
FINGERPRINT = "select count(*) || ' ' || coalesce(md5(string_agg(id::text || '|' || coalesce(email, '') || '|' || coalesce(encrypted_password, ''), ',' order by id)), '-') from auth.users"


def check_private(path, what):
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit("%s %s is readable by group/others: chmod 600 it first" % (what, path))
    d = os.path.dirname(os.path.abspath(path))
    top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    home = os.path.realpath(os.path.expanduser("~"))
    if top and os.path.realpath(top) != home and not os.path.realpath(path).startswith(os.path.join(os.path.realpath(top), ".clever-landing") + os.sep):
        raise SystemExit("%s %s is inside the repository %s: keep credentials outside it" % (what, path, top))


def read_env_file(path):
    """KEY=VALUE as written by env-safe.sh env-file: values taken AS THEY ARE (no strip, no unquoting)."""
    check_private(path, "credentials file")
    vals = {}
    for ln in open(path, encoding="utf-8"):
        m = re.match(r"^(?:export )?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln.rstrip("\n"))
        if m:
            vals[m.group(1)] = m.group(2)
    return vals


def url_vals(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise SystemExit("source: a postgres:// or postgresql:// URL is expected in the variable")
    q = urllib.parse.parse_qs(u.query)
    return {"POSTGRESQL_ADDON_HOST": urllib.parse.unquote(u.hostname or ""), "POSTGRESQL_ADDON_PORT": str(u.port or 5432),
            "POSTGRESQL_ADDON_DB": urllib.parse.unquote(u.path.lstrip("/")) or "postgres",
            "POSTGRESQL_ADDON_USER": urllib.parse.unquote(u.username or ""),
            "POSTGRESQL_ADDON_PASSWORD": urllib.parse.unquote(u.password or ""), "_SSLMODE": (q.get("sslmode") or ["require"])[0]}


def pg_env(vals, folder, read_only):
    missing = [k for k in PG_KEYS if not vals.get(k)]
    if missing:
        raise SystemExit("missing (names only): " + ", ".join(missing))
    pp = os.path.join(folder, "pgpass")
    esc = lambda x: x.replace("\\", "\\\\").replace(":", "\\:")  # noqa: E731
    with os.fdopen(os.open(pp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write("%s:%s:%s:%s:%s\n" % tuple(esc(vals[k]) for k in PG_KEYS))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    loopback = vals["POSTGRESQL_ADDON_HOST"] in ("127.0.0.1", "localhost", "::1")
    ssl = vals.get("_SSLMODE") or "require"
    if ssl not in ("require", "verify-ca", "verify-full") and not loopback:
        ssl = "require"                 # hashes never travel without TLS (only a loopback test host may)
    env.update({"PGHOST": vals["POSTGRESQL_ADDON_HOST"], "PGPORT": vals["POSTGRESQL_ADDON_PORT"], "PGDATABASE": vals["POSTGRESQL_ADDON_DB"],
                "PGUSER": vals["POSTGRESQL_ADDON_USER"], "PGPASSFILE": pp, "PGCONNECT_TIMEOUT": "20",
                "PGSSLMODE": ssl if not loopback else (vals.get("_SSLMODE") or "prefer")})
    if read_only:
        # not PGOPTIONS (a pooler may ignore or refuse startup options): every source session runs
        # SET SESSION CHARACTERISTICS … READ ONLY, then checks transaction_read_only = on (RO_GUARD)
        env["LOC_RO_SOURCE"] = "1"
    return env


def summarize_error(err):
    """É2: a psql error may quote account data (DETAIL: Key (lower(email))=(victim@x) already exists,
    Failing row contains (…hash…)). Only the SQLSTATE and the object NAMES are kept."""
    code = re.search(r"(?:ERROR|FATAL|PANIC):\s+([0-9A-Z]{5}):", err)
    names = []
    for k in ("SCHEMA NAME", "TABLE NAME", "COLUMN NAME", "CONSTRAINT NAME"):
        m = re.search(r"(?m)^%s:\s+([A-Za-z0-9_.]{1,63})\s*$" % k, err)
        if m:
            names.append("%s %s" % (k.split()[0].lower(), m.group(1)))
    if not code:
        conn = re.search(r"(?i)(could not connect|connection to server|password authentication failed|timeout expired|SSL)", err)
        return "connection failed (%s)" % conn.group(1).lower() if conn else "psql failed (details withheld: they may contain account data)"
    return "ERROR %s%s (details withheld: they may contain account data)" % (code.group(1), (" — " + ", ".join(names)) if names else "")


RO_GUARD = ["-c", "set session characteristics as transaction read only",
            "-c", "do $$ begin if current_setting('transaction_read_only') <> 'on' then raise exception 'source session is not read-only'; end if; end $$"]


def psql(env, args, stdin=None):
    """stderr is NEVER returned as is: summarize_error keeps the SQLSTATE and object names only."""
    pre = RO_GUARD if env.get("LOC_RO_SOURCE") == "1" else []         # the source: every session proven read-only first
    p = subprocess.run(["psql", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-v", "SHOW_CONTEXT=never"] + pre + args,
                       env=env, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
    out = p.stdout.decode("utf-8", "replace").strip()
    if pre:
        out = "\n".join(l for l in out.splitlines() if l.strip() not in ("SET", "DO"))
    return p.returncode, out.strip(), (summarize_error(p.stderr.decode("utf-8", "replace")) if p.returncode else "")


def columns(env):
    rc, out, err = psql(env, ["-c", "select column_name from information_schema.columns where table_schema = 'auth' and table_name = 'users'"])
    if rc != 0:
        raise SystemExit("cannot read auth.users columns: " + err)
    return set(out.split())


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--db-url-file", help="600 file, outside any repository, holding the source URL (the documented way)")
    ap.add_argument("--source-env", default="SUPABASE_DB_URL", help="NAME of the variable holding the source URL (Session pooler), for a human's shell")
    ap.add_argument("--target-env-file", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.db_url_file:
        check_private(a.db_url_file, "--db-url-file")
        lines = [l for l in open(a.db_url_file, encoding="utf-8").read().splitlines() if l.strip()]
        if len(lines) != 1:
            print("--db-url-file: one line expected (the URL alone)")
            return 2
        src_vals = url_vals(lines[0].strip())
    elif re.fullmatch(r"[A-Z_][A-Z0-9_]*", a.source_env) and os.environ.get(a.source_env):
        src_vals = url_vals(os.environ[a.source_env])
    else:
        print("give --db-url-file <600 file> (or export %s in your own shell): the URL is never taken from argv" % a.source_env)
        return 2
    tgt_vals = read_env_file(a.target_env_file)
    work = tempfile.mkdtemp(prefix="accounts-")
    os.chmod(work, 0o700)
    try:
        os.mkdir(os.path.join(work, "s"), 0o700)
        os.mkdir(os.path.join(work, "t"), 0o700)
        src = pg_env(src_vals, os.path.join(work, "s"), read_only=True)
        tgt = pg_env(tgt_vals, os.path.join(work, "t"), read_only=False)
        have_src, have_tgt = columns(src), columns(tgt)        # once each
        cols = [c for c in COLUMNS if c in have_src and c in have_tgt]
        if not {"id", "email", "encrypted_password"} <= set(cols):
            print("REFUSED: id, email and encrypted_password must exist in auth.users on both sides (target: minimal auth schema first)")
            return 1
        rc, s_fp, err = psql(src, ["-c", FINGERPRINT])
        if rc != 0:
            print("source unreadable: " + err)
            return 1
        rc, t_fp, err = psql(tgt, ["-c", FINGERPRINT])
        if rc != 0:
            print("target unreadable: " + err)
            return 1
        print("source auth.users: %s accounts · target: %s accounts · columns: %s" % (s_fp.split()[0], t_fp.split()[0], ", ".join(cols)))
        if t_fp.split()[0] != "0":
            print("REFUSED: the target auth.users is not empty (a second run, or accounts created by the app): nothing copied")
            return 1
        if a.dry_run:
            print("DRY RUN: nothing copied.")
            return 0
        data = os.path.join(work, "users.csv")
        collist = ", ".join(cols)
        rc, _, err = psql(src, ["-c", "\\copy (select %s from auth.users order by id) to '%s' with (format csv)" % (collist, data)])
        if rc != 0:
            print("source export failed: " + err)
            return 1
        os.chmod(data, 0o600)
        rc, _, err = psql(tgt, ["--single-transaction", "-c", "\\copy auth.users (%s) from '%s' with (format csv)" % (collist, data)])
        if rc != 0:
            print("target import failed, rolled back: " + err)
            return 1
        rc, t_fp, err = psql(tgt, ["-c", FINGERPRINT])
        same = rc == 0 and t_fp == s_fp
        print("accounts: source %s, target %s · fingerprint md5(id|email|hash) %s" % (
            s_fp.split()[0], t_fp.split()[0] if t_fp else "?", "IDENTICAL" if same else "DIFFERENT"))
        print("VERDICT: %s — no row printed; create the sign-up trigger now (after the import)" % ("PASS" if same else "FAIL"))
        return 0 if same else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
