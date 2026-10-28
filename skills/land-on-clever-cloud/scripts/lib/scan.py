"""scan.py — read-only survey of the COMMITTED tree (HEAD), as `clever deploy` will see it.

Called by scripts/scan.sh (which performs the git/$HOME guards). Never reads an untracked
file's content, never calls the network, never prints a secret value.
"""
import argparse
import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RULES_DIR, Repo, dollar_refs, head_text, load_json, load_rules, mask, run, services, untrusted, yaml_lite, SECRETISH_NAME  # noqa: E402

VERSION = 1
MANIFESTS = [
    ("node", r"^package\.json$"), ("python", r"^(requirements\.txt|pyproject\.toml|Pipfile|uv\.lock|setup\.py)$"),
    ("php", r"^composer\.json$"), ("go", r"^go\.mod$"), ("rust", r"^Cargo\.toml$"), ("ruby", r"^Gemfile$"),
    ("java", r"^(pom\.xml|build\.gradle(\.kts)?)$"), ("dotnet", r"\.(cs|fs|vb)proj$"), ("elixir", r"^mix\.exs$"),
    ("docker", r"^Dockerfile$"),
]
CODE_EXT = re.compile(r"\.(js|mjs|cjs|ts|tsx|jsx|vue|svelte|py|rb|php|go|rs|cs|fs|java|kt|scala|ex|exs|sh)$")
ENV_NAME_RX = re.compile(
    r"process\.env\.([A-Z_][A-Z0-9_]+)|process\.env\[['\"]([A-Z_][A-Z0-9_]+)|import\.meta\.env\.([A-Z_][A-Z0-9_]+)"
    r"|os\.environ(?:\.get)?[\[(]\s*['\"]([A-Z_][A-Z0-9_]+)|os\.getenv\(\s*['\"]([A-Z_][A-Z0-9_]+)"
    r"|getenv\(\s*['\"]([A-Z_][A-Z0-9_]+)|ENV(?:\.fetch)?[\[(]\s*['\"]([A-Z_][A-Z0-9_]+)"
    r"|Getenv\(\"([A-Z_][A-Z0-9_]+)|env::var\(\"([A-Z_][A-Z0-9_]+)|GetEnvironmentVariable\(\"([A-Z_][A-Z0-9_]+)"
    r"|\benv\(\s*['\"]([A-Z_][A-Z0-9_]+)|Deno\.env\.get\(['\"]([A-Z_][A-Z0-9_]+)|\benv=['\"]([A-Z_][A-Z0-9_]+)")
PUBLIC_PREFIX = re.compile(r"^(NEXT_PUBLIC_|VITE_|REACT_APP_|NUXT_PUBLIC_|PUBLIC_|EXPO_PUBLIC_|GATSBY_)")
NODE_BUILD_TOOLS = {"typescript", "vite", "next", "webpack", "esbuild", "tailwindcss", "@nestjs/cli", "rollup",
                    "parcel", "@sveltejs/kit", "nuxt", "astro", "react-scripts", "@angular/cli", "tsup", "turbo"}
NODE_FRAMEWORKS = ["next", "nuxt", "@remix-run/node", "@sveltejs/kit", "astro", "vite", "express", "fastify",
                   "@nestjs/core", "hono", "koa", "socket.io", "react-scripts", "@angular/core", "strapi"]
# client-side routers: a single-page app that uses one answers its deep links (/login, /settings/team) from index.html, which a static server only does with a fallback
CLIENT_ROUTERS = {"react-router", "react-router-dom", "@tanstack/react-router", "@reach/router", "wouter", "preact-router", "@solidjs/router", "vue-router",
                  "@angular/router", "@remix-run/router"}
PY_FRAMEWORKS = ["django", "flask", "fastapi", "starlette", "gunicorn", "uvicorn", "daphne", "celery", "channels",
                 "redis", "psycopg", "psycopg2", "psycopg2-binary", "dj-database-url", "whitenoise", "sqlalchemy",
                 "pymongo", "boto3", "supabase"]


# a committed key-shaped string (private key header, provider keys, JWT, Laravel's base64: key)
KEY_LIKE = (r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(sk|rk)_live_[A-Za-z0-9]{8,}|\bsb_secret_|\bAKIA[A-Z0-9]{12,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
            r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|base64:[A-Za-z0-9+/]{40,}={0,2}")
# what a template value looks like (.env.example): xxxx, ****, ..., <your-key>, your_, example, change-me, replace, placeholder, dummy, fake, redacted, todo, here, 00000000
PLACEHOLDER = re.compile(r"(?i)x{4,}|\*{3,}|\.{3,}|<[^<>\s]+>|(?<![a-z])(?:your|my)(?![a-z])|example|sample|change[-_]?me|replace|placeholder|dummy|fake|redacted|(?<![a-z])todo"
                         r"|(?<![a-z])here(?![a-z])|0{8,}")
# a folder whose README says, at the top, that THE FOLDER simulates a DATABASE (podinfo's deploy/bases/database: "manifests to simulate a database setup").
# The verb must take a database as its object (or "simulator"/"simulation" be attached to one): "terraform plan simulates the changes", "simulate 500 users",
# "the simulation API and its PostgreSQL" do not say it. A database image in the folder's manifests overrides the README (see holds_database_image).
DB_WORD = r"(?:data\s?base|db|postgres(?:ql)?|mysql|mariadb|mongo(?:db)?|redis)"
SIMULATES_DB = re.compile(r"(?i)\bsimulat(?:e|es|ed|ing)\s+(?:(?:an?|the|some|our|this|that|these|those)\s+)?(?:[\w-]+\s+){0,2}?" + DB_WORD + r"\b"
                          r"|\b" + DB_WORD + r"\s+simulat(?:or|ion)s?\b"
                          r"|\bsimulat(?:or|ion)s?\s+(?:of|for)\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,2}?" + DB_WORD + r"\b")
DB_IMAGE_KINDS = ("postgresql", "mysql", "redis", "mongodb")           # the kinds of rules/_services.json whose images are databases
README_NAME = re.compile(r"(?i)\Areadme(?:\.(?:md|markdown|rst|txt))?\Z")
# a static server mounted at the ROOT, before the routes: app.use(express.static(…)) or app.use('/', express.static(…)). A mount on a sub-path
# (app.use('/assets', express.static(…))) does not answer GET /: the route runs.
STATIC_SERVER = re.compile(r"\.use\(\s*(?:['\"]/['\"]\s*,\s*)?(?:express\.static|serveStatic)\s*\(")
NEXT_IMAGE_IMPORT = re.compile(r"(?:\bfrom\s+|\brequire\(\s*|\bimport\s*\(\s*|\bimport\s+)['\"]next/(?:legacy/|future/)?image['\"]")
# health routes: ONLY the ones registered on the app object itself, with a literal path (leading slash, trailing slash exactly as written), whose path is therefore
# the one served. A blueprint or router (url_prefix, include_router(prefix=), app.use('/api', router)), a wrapper around the path (s.prefixedPath("/healthz")), a
# Django path("health/") (no leading slash; APPEND_SLASH), a Spring class-level @RequestMapping, a file with several app objects (a sub-application mounted under a
# prefix), chi's Route / Mount are NOT read: a wrong path would fail every deploy once it is CC_HEALTH_CHECK_PATH (strict 2xx). The plan then proposes a fresh /health
# at the app root.
HEALTH_NAME = r"(?:health(?:z|check|-check|_check)?|_health|ping|readyz?|livez)"
HEALTH_PATH = r"(/(?:api/)?" + HEALTH_NAME + r"/?)"
# the objects that ARE the app (not a Blueprint, an APIRouter, an express.Router()): found in the file where they are built
PY_APP = re.compile(r"(?m)^[ \t]*(\w+)\s*(?::[^=\n]+)?=\s*(?:Flask|FastAPI|Starlette|Sanic|Quart|Bottle|Litestar|create_app)\(")
JS_APP = re.compile(r"(?m)\b(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:new\s+(?:Hono|Elysia)\s*\(|(?:express|fastify|Fastify)\s*\(|require\(\s*['\"](?:fastify|express)['\"]\s*\)\s*\()")
GO_APP = re.compile(r"(?m)\b(\w+)\s*:?=\s*(?:http\.NewServeMux|gin\.(?:Default|New)|echo\.New|chi\.NewRouter|mux\.NewRouter|fiber\.New)\(")
HEALTH_FILE = re.compile(r"\A(?:src/)?(?:app|pages)/(?:api/)?(health|healthz|ping|ready|readyz|livez)(?:/route)?\.(?:ts|js|tsx|jsx|mjs)\Z")      # relative to the root
# a database access in the body of a handler
TOUCHES_DB = re.compile(r"(?i)\bselect\s+1\b|\.execute\(|\.cursor\(|\.query\(|\$queryRaw|\.ping\(|\bprisma\.|\.authenticate\(\)|\bsequelize\b|\bknex\b|\bpool\.|\bdb\.|"
                        r"\bconnection\.|\.raw\(|get_?connection|\.command\(|\bDbContext\b|\bpsycopg|\bsqlalchemy\b|session\.exec|\.CanConnect|\.Ping\(|\bdb_session\b")
# DATABASE_URL read by name through a call (decouple's config('DATABASE_URL'), env.str('DATABASE_URL'), settings.get('DATABASE_URL')): not a log line, not a help text
DATABASE_URL_CALL = re.compile(r"(?<![\w$])(?:[\w.]*\.)?(?:config|env|getenv|get|str|db|db_url)\(\s*['\"]DATABASE_URL['\"]")
# …or as the alias of a settings field (pydantic: Field('sqlite:///./app.db', validation_alias='DATABASE_URL'), alias=, env=, AliasChoices(…)): the field reads the variable
DATABASE_URL_FIELD = re.compile(r"\b(?:alias|validation_alias|env)\s*=\s*(?:AliasChoices\([^)]*)?['\"]DATABASE_URL['\"]")
# a mount, a prefix, a basePath, a StripPrefix seen in the file of a health route: the path may be served under a prefix, it is not certain any more
MOUNTED = {
    "py": re.compile(r"\.mount\(|\bprefix\s*=|\broot_path\s*=|\burl_prefix\b|DispatcherMiddleware|APPLICATION_ROOT|SCRIPT_NAME"),
    "js": re.compile(r"\.basePath\(|\bprefix\s*:\s*['\"]|\.mount\(|\.use\(\s*['\"]/[^'\"]*['\"]\s*,\s*(?:require\(|[A-Za-z_$][\w$.]*\s*\))"),
    "go": re.compile(r"StripPrefix\(|\.(?:Route|Mount)\("),
}


def env_refs(env):
    """[{"name", "refs"}] for the variables of a compose `environment:` (a mapping or a list of NAME=value) whose value names another variable with $: NAMES
    only (a variable name that is not UPPER_SNAKE is left out), never a value."""
    pairs = list(env.items()) if isinstance(env, dict) else [str(x).split("=", 1) for x in (env or []) if isinstance(x, str) and "=" in x]
    out = []
    for kv in pairs:
        if len(kv) == 2 and re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(kv[0])):
            refs = dollar_refs(str(kv[1]))
            if refs:
                out.append({"name": str(kv[0]), "refs": refs})
    return out[:12]


def balanced(t, i, open_ch="(", close_ch=")", limit=6000, lang="c"):
    """The text from t[i] (an opening bracket) to its closing bracket, strings and comments skipped; None when it does not close within `limit` characters.
    lang "c" (JS, TS, Go, Java…): // and /* */ comments, backtick strings; lang "py": # comments (a quote in a comment is not a string: "don't…"), no // (floor division)."""
    py = lang == "py"
    depth, j, end, quote = 0, i, min(len(t), i + limit), None
    while j < end:
        c = t[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in ("'\"" if py else "'\"`"):
            quote = c
        elif py and c == "#":
            j = t.find("\n", j)
            if j < 0:
                return None
            continue
        elif not py and t.startswith("//", j):
            j = t.find("\n", j)
            if j < 0:
                return None
            continue
        elif not py and t.startswith("/*", j):
            j = t.find("*/", j + 2)
            if j < 0:
                return None
            j += 2
            continue
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return t[i:j + 1]
        j += 1
    return None


def strip_py_comments(s):
    """`s` without its Python # comments (a # inside a string stays)."""
    out, quote, i = [], None, 0
    while i < len(s):
        c = s[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < len(s):
                out.append(s[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            out.append(c)
        elif c == "#":
            while i < len(s) and s[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def dirname(p):
    d = os.path.dirname(p)
    return d if d else "."


def is_env_template(path):
    """.env.example, .env.sample, .env.template, .env.dist, .env.local.example, example.env, env.example…: a file that tells what to put in .env, with template values."""
    base = os.path.basename(path).lower()
    return "env" in base and bool(re.search(r"(^|[._-])(example|sample|template|dist|defaults?)([._-]|$)", base))


def in_dir(path, d):
    return d == "." or path.startswith(d + "/")


# Prisma schemas (schema.prisma, or the .prisma files of a multi-file schema): read line by line, in linear time (one pass per line, anchored patterns only).
# A SQLite use is the exact provider = "sqlite" of a datasource block; its url is env("NAME") — the name kept only when it is a plain variable name — or a
# literal, whose value is never kept. Comments (//) and the other blocks (generator, model…) are skipped.
PRISMA_FILE = re.compile(r"(?:\A|/)[^/]+\.prisma\Z")
PRISMA_DATASOURCE = re.compile(r"[ \t]*datasource[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*\{[ \t]*\Z")
PRISMA_DATASOURCE_NAME = re.compile(r"[ \t]*datasource[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]*\Z")      # its { on a following line
PRISMA_OPEN = re.compile(r"[ \t]*\{[ \t]*\Z")
PRISMA_SQLITE = re.compile(r'[ \t]*provider[ \t]*=[ \t]*"sqlite"[ \t]*\Z')
PRISMA_URL_ENV = re.compile(r'[ \t]*url[ \t]*=[ \t]*env\([ \t]*"([^"\\\n]{0,200})"[ \t]*\)[ \t]*\Z')
PRISMA_URL_LITERAL = re.compile(r'[ \t]*url[ \t]*=[ \t]*"[^"\\\n]*"[ \t]*\Z')
PRISMA_ENV_NAME = re.compile(r"\A[A-Z_][A-Z0-9_]{0,63}\Z")


def prisma_code(line):
    """(the code of one line of a Prisma schema — the text before a // comment that is not in a string —, its net count of { minus } outside strings).
    One pass over the line: a string ends at the line (Prisma strings do not span lines)."""
    depth, quote, i, n = 0, False, 0, len(line)
    while i < n:
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = False
        elif c == '"':
            quote = True
        elif c == "/" and line.startswith("//", i):
            return line[:i], depth
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return line, depth


def prisma_datasources(text):
    """The SQLite datasources of one Prisma schema: [{"line": the provider line, "url": "env" | "literal" | None, "env": NAME or None (env only), "url_line"}].
    Only a datasource block opened at the top level counts, and in it only the exact provider = "sqlite" (not "sqlite3", "SQLite", an unquoted word, a string
    followed by anything). The url's literal value is never returned; an env() name that is not a plain variable name is dropped (None). R40: a UTF-8 BOM is
    stripped; lines end at \n only (a trailing \r dropped) — splitlines() also ends a line at U+2028, U+0085, \x0b or \x0c, which made a comment's tail a line
    and shifted the line numbers; `datasource db` may have its { on a following line (blank or comment-only lines between)."""
    t = str(text or "")
    if t.startswith("\ufeff"):
        t = t[1:]
    out, depth, block, pending = [], 0, None, False
    for n, raw in enumerate(t.split("\n"), 1):
        code, delta = prisma_code(raw.rstrip("\r"))
        if depth == 0 and block is None:
            if PRISMA_DATASOURCE.match(code) or (pending and PRISMA_OPEN.match(code)):
                block, depth, pending = {"provider": None, "url": None}, 1, False
                continue
            if pending and not code.strip():
                continue                                    # still waiting for the block's {
            pending = bool(PRISMA_DATASOURCE_NAME.match(code))
            if pending:
                continue
        if block is not None and depth == 1:
            if PRISMA_SQLITE.match(code):
                block["provider"] = n
            else:
                m = PRISMA_URL_ENV.match(code)
                if m:
                    block["url"] = {"url": "env", "env": m.group(1) if PRISMA_ENV_NAME.match(m.group(1)) else None, "url_line": n}
                elif PRISMA_URL_LITERAL.match(code):
                    block["url"] = {"url": "literal", "url_line": n}
        depth = max(0, depth + delta)
        if block is not None and depth == 0:
            if block["provider"]:
                out.append(dict({"line": block["provider"]}, **(block["url"] or {"url": None})))
            block = None
    if block is not None and block["provider"]:           # a block left open at the end of the file
        out.append(dict({"line": block["provider"]}, **(block["url"] or {"url": None})))
    return out


# RT D5: the table proof names the app's tables. A table name read from the repository is kept only when it is a plain identifier (the rule of
# migrate-sqlite.py's qi and of prove.sh --pg-expect-table): anything else never reaches a command.
TABLE_NAME_RX = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
MAX_TABLE_NAMES = 50
PRISMA_MODEL = re.compile(r"[ \t]*model[ \t]+([A-Za-z][A-Za-z0-9_]*)[ \t]*\{[ \t]*\Z")
PRISMA_MAP = re.compile(r'[ \t]*@@map\([ \t]*(?:name[ \t]*:[ \t]*)?"([^"\\]*)"[ \t]*\)[ \t]*\Z')
PRISMA_SCHEMA_ATTR = re.compile(r'[ \t]*@@schema\([ \t]*"([^"\\]*)"[ \t]*\)[ \t]*\Z')


def prisma_models(text):
    """The tables of the models of one Prisma schema: [{"model", "table", "schema"}] — the table is @@map's name when the block has one, else the model's
    (Prisma keeps its case); "schema" is @@schema's name or None. Only a `model X {` block opened at the top level (views, enums, types and comments are not
    tables); the same line reader as prisma_datasources."""
    t = str(text or "")
    if t.startswith("\ufeff"):
        t = t[1:]
    out, depth, cur = [], 0, None
    for raw in t.split("\n"):
        code, delta = prisma_code(raw.rstrip("\r"))
        if depth == 0 and cur is None:
            m = PRISMA_MODEL.match(code)
            if m:
                cur, depth = {"model": m.group(1), "table": m.group(1), "schema": None}, 1
                continue
        elif cur is not None and depth == 1:
            m = PRISMA_MAP.match(code)
            if m:
                cur["table"] = m.group(1)
            m = PRISMA_SCHEMA_ATTR.match(code)
            if m:
                cur["schema"] = m.group(1)
        depth = max(0, depth + delta)
        if cur is not None and depth == 0:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def table_names(names):
    """{"names": the plain identifiers among `names` (sorted, unique, MAX_TABLE_NAMES at most), "skipped": how many others were left out}."""
    seen = sorted({n for n in names if isinstance(n, str)})
    ok = [n for n in seen if TABLE_NAME_RX.match(n)]
    return {"names": ok[:MAX_TABLE_NAMES], "skipped": len(seen) - len(ok[:MAX_TABLE_NAMES])}


class Scanner:
    def __init__(self, top, prefix):
        self.repo = Repo(top, prefix)
        self.top = top
        self.prefix = prefix
        self.notes = []
        self.simulated_skips = {}
        tp = self.repo.text_paths()
        self.repo.preload(tp)
        self.text = tp
        self.code = [p for p in tp if CODE_EXT.search(p)]
        self._prisma = None

    def prisma_tables(self, root):
        """RT D5: the tables the Prisma models of this root create in schema public (prisma_models: @@map's name, else the model's; a model of another @@schema
        left out): {"files", "names", "skipped"}, or None when the root has no model."""
        files, tabs = [], []
        for p in self.text:
            if PRISMA_FILE.search(p) and not SKIP_TEST.search("/" + p) and in_dir(p, root):
                ms = [m for m in prisma_models(self.repo.read(p)) if m["schema"] in (None, "public")]
                if ms:
                    files.append(p)
                    tabs += [m["table"] for m in ms]
        return dict({"files": files[:4]}, **table_names(tabs)) if files else None

    def sqlite_tables(self, full, size):
        """RT D5: the user tables of a committed SQLite file ({"names", "skipped"}, see table_names), or None when it cannot be read as one. The blob is read from
        HEAD (never the working tree) into a file of a fresh 700 folder, opened read-only and immutable, and only sqlite_master is read (no view, no trigger runs),
        under a 10-second progress guard; the folder is removed. SQLite's own sqlite_ tables and Prisma's _prisma_migrations are left out, as migrate-sqlite.py
        leaves them out."""
        if size > 64 * 1024 * 1024:
            return None
        rc, blob, _ = run(["git", "-C", self.top, "cat-file", "blob", "HEAD:" + full], timeout=120)
        if rc != 0 or not blob.startswith(b"SQLite format 3\0"):
            return None
        import shutil, sqlite3, tempfile, time, urllib.parse
        d = tempfile.mkdtemp(prefix="scan-sqlite-")
        try:
            f = os.path.join(d, "committed.db")
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as h:
                h.write(blob)
            blob = None
            conn = sqlite3.connect("file:%s?mode=ro&immutable=1" % urllib.parse.quote(f), uri=True)
            try:
                try:
                    conn.execute("pragma trusted_schema = off")
                except sqlite3.Error:
                    pass
                stop = time.time() + 10
                conn.set_progress_handler(lambda: 1 if time.time() > stop else 0, 10000)
                rows = conn.execute("select name from sqlite_master where type = 'table' limit 1000").fetchall()
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError):
            return None
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return table_names([r[0] for r in rows if isinstance(r[0], str) and not r[0].startswith("sqlite_") and r[0] != "_prisma_migrations"])

    def prisma_sqlite(self):
        """The SQLite datasources of the committed Prisma schemas (tests and examples skipped), as sqlite_uses hits: {"file", "line" (the provider line),
        "text": 'provider = "sqlite"' (that line as matched, nothing else of the file), "prisma": {"url", "env", "url_line"}} (see prisma_datasources)."""
        if self._prisma is None:
            self._prisma = [{"file": p, "line": d["line"], "text": 'provider = "sqlite"', "prisma": {k: v for k, v in d.items() if k != "line"}}
                            for p in self.text if PRISMA_FILE.search(p) and not SKIP_TEST.search("/" + p)
                            for d in prisma_datasources(self.repo.read(p))]
        return self._prisma

    # ------------------------------------------------------------ git & hygiene
    def git_info(self):
        def g(*a):
            rc, out, _ = run(["git", "-C", self.top] + list(a))
            return out.decode("utf-8", "replace").strip() if rc == 0 else ""
        dirty = g("status", "--porcelain")
        remotes = set()
        for line in g("remote", "-v").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                m = re.sub(r"^[a-z+]+://", "", parts[1])
                m = re.sub(r"^[^@/]*@", "", m)
                remotes.add(parts[0] + "=" + re.split(r"[/:]", m)[0])
        clever_aliases = []
        if os.path.exists(os.path.join(self.top, ".clever.json")):
            try:
                cj = json.load(open(os.path.join(self.top, ".clever.json")))
                apps = cj.get("apps", []) if isinstance(cj, dict) else None
                if not isinstance(apps, list):
                    raise ValueError("not a link file")           # a top-level list, "apps": null…: unreadable, below
                clever_aliases = [a.get("alias") for a in apps if isinstance(a, dict)]
            except (ValueError, OSError):
                clever_aliases = ["(unreadable .clever.json)"]
        return {
            "toplevel": self.top, "subfolder": self.prefix or None,
            "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "head": g("log", "-1", "--format=%h %cs %s")[:100],
            "uncommitted_changes": len([x for x in dirty.splitlines() if x.strip()]),
            "committed_files": len(self.repo.paths),
            "remotes": sorted(remotes), "worktree": os.path.isfile(os.path.join(self.top, ".git")),
            "clever_link": clever_aliases or None,
        }

    def hygiene(self):
        tracked_env = []
        for p in self.repo.paths:
            if re.search(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$", p) and not re.search(r"\.(example|sample|template|dist|defaults)$", p):
                t = (self.repo.read(p) or "").lstrip("\ufeff")          # a BOM must not hide the first name (a secret-like one included)
                names = re.findall(r"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", t)
                tracked_env.append({"file": p, "names": sorted(set(names)),
                                    "secret_like_names": sorted(n for n in set(names) if SECRETISH_NAME.search(n))})
        base = os.path.join(self.top, self.prefix) if self.prefix else self.top
        untracked_env = []
        try:
            for name in sorted(os.listdir(base)):
                if re.match(r"^\.env(\..+)?$", name) and not self.repo.exists(name):
                    untracked_env.append(name)   # existence only: content is never read
        except OSError:
            pass
        templates = []
        for p in self.repo.paths:
            if re.search(r"(^|/)\.env[^/]*\.(example|sample|template|dist)$", p):
                t = (self.repo.read(p) or "").lstrip("\ufeff")          # the same for a template: its first variable is not lost
                templates.append({"file": p, "names": sorted(set(re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=", t)))})
        not_test = [p for p in self.text if not SKIP_TEST.search("/" + p) and not re.search(r"(_test\.go|\.test\.[jt]sx?|\.spec\.[jt]sx?|\.d\.ts)$|generated", p)]
        # .env.example & co. hold template values (sk_live_xxxx, AKIA…EXAMPLE, change-me): not a secret committed. A template that holds a value which does not
        # read as a placeholder (somebody pasted a real key) is still reported.
        key_like = self.repo.grep(KEY_LIKE, paths=[p for p in not_test if not is_env_template(p)], max_hits=10)
        for h in self.repo.grep(KEY_LIKE, paths=[p for p in not_test if is_env_template(p)], max_hits=10):
            raw = (self.repo.read(h["file"]) or "").splitlines()[h["line"] - 1]
            named = re.match(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*[=:]\s*(.*)$", raw)      # judge the VALUE: MY_KEY=sk_live_… is not a template because of "MY"
            if not PLACEHOLDER.search(named.group(1) if named else raw):
                key_like.append(h)
        key_like = key_like[:10]
        code_nt = [p for p in self.code if p in set(not_test)]
        # a LITERAL value: quoted in code (a bare word there is a variable: password: password, user["password"]),
        # bare only in shell scripts; never a type annotation, never Password::class
        q = r"(?i)\b(password|passwd|pwd)\b['\"]?\s*(?:=(?!=)|:(?!:))\s*['\"](?!\s)[^'\"\n]{3,}['\"]|[a-z]+://[^:/@\s'\"$]+:[^@\s'\"$]+@"
        hardcoded_creds = self.repo.grep(q, paths=[p for p in code_nt if not p.endswith(".sh")], max_hits=10)
        hardcoded_creds += self.repo.grep(r"(?i)\b[A-Z_]*(PASSWORD|PASSWD|PWD)\b=(?!\$)[^\s'\"$]{3,}", paths=[p for p in code_nt if p.endswith(".sh")], max_hits=5)
        # a framework secret written in the code (a literal of 16+ chars, or the fallback of an env read)
        secret_literals = self.repo.grep(
            r"\b(SECRET_KEY|APP_KEY|JWT_SECRET|SESSION_SECRET|SECRET_KEY_BASE)\b['\"]?\s*[=:]\s*['\"][^'\"\s]{16,}['\"]"
            r"|\b(SECRET_KEY|APP_KEY|JWT_SECRET|SESSION_SECRET)['\"]\s*\)?\s*(or|\|\||,)\s*['\"][^'\"]{8,}['\"]", paths=code_nt, max_hits=6)
        return {"tracked_env_files": tracked_env, "untracked_env_files": untracked_env, "env_templates": templates,
                "key_like_strings": key_like, "hardcoded_credentials": hardcoded_creds, "secret_literals": secret_literals}

    # ------------------------------------------------------------ manifests
    def deps(self, root):
        """dependency names of a root (node / python / composer / gemfile)."""
        out = {"node": {}, "node_dev": {}, "python": [], "python_commented": [], "php": [], "ruby": []}
        pj = self.repo.read(self._j(root, "package.json"))
        if pj:
            try:
                d = json.loads(pj)
                out["node"] = d.get("dependencies", {}) or {}
                out["node_dev"] = d.get("devDependencies", {}) or {}
                out["package_json"] = {k: d.get(k) for k in ("scripts", "main", "engines", "packageManager", "workspaces") if d.get(k)}
            except ValueError:
                self.notes.append("package.json in %s is not valid JSON" % root)
        rq = self.repo.read(self._j(root, "requirements.txt"))
        if rq:
            for line in rq.splitlines():
                m = re.match(r"^\s*(#\s*)?([A-Za-z0-9_.-]+)(\[[^\]]*\])?", line)
                if not m or not m.group(2):
                    continue
                name = m.group(2).lower()
                if m.group(1):
                    if name in PY_FRAMEWORKS or name.startswith("psycopg"):
                        out["python_commented"].append(name)
                else:
                    out["python"].append(name)
        pp = self.repo.read(self._j(root, "pyproject.toml"))
        if pp:
            out["python"] += [m.lower() for m in re.findall(r"[\"']([A-Za-z0-9_.-]+)\s*[<>=~!\[;\"']", pp)]
        cj = self.repo.read(self._j(root, "composer.json"))
        if cj:
            try:
                out["php"] = list((json.loads(cj).get("require") or {}).keys())
            except ValueError:
                pass
        gf = self.repo.read(self._j(root, "Gemfile"))
        if gf:
            out["ruby"] = re.findall(r"gem\s+['\"]([^'\"]+)", gf)
        return out

    def _j(self, root, name):
        return name if root == "." else root + "/" + name

    def roots(self):
        found = {}
        for p in self.repo.paths:
            base = os.path.basename(p)
            for rt, rx in MANIFESTS:
                if re.search(rx, base) and not SKIP_TEST.search("/" + p) and "node_modules/" not in p:
                    found.setdefault(dirname(p), set()).add(rt)
        return found

    def dockerfile(self, root):
        t = self.repo.read(self._j(root, "Dockerfile"))
        if not t:
            return None
        lines = t.splitlines()
        info = {"file": self._j(root, "Dockerfile"), "from": [], "stages": [], "expose": [], "cmd": None, "entrypoint": None,
                "system_packages": [], "env_port": None}
        for n, line in enumerate(lines, 1):
            s = line.strip()
            m = re.match(r"(?i)^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", s)
            if m:
                info["from"].append({"image": m.group(1), "line": n})
                if m.group(2):
                    info["stages"].append(m.group(2))
            m = re.match(r"(?i)^EXPOSE\s+(.+)", s)
            if m:
                info["expose"] += m.group(1).split()
            m = re.match(r"(?i)^(CMD|ENTRYPOINT)\s+(.+)", s)
            if m:
                info[m.group(1).lower()] = {"value": mask(m.group(2)), "line": n}
            m = re.search(r"(?i)(apt-get install|apk add|yum install|dnf install)\s+(.+)", s)
            if m:
                pk = [x for x in re.split(r"\s+", m.group(2)) if x and not x.startswith("-") and x not in ("&&", "\\")]
                info["system_packages"] += [x for x in pk if re.fullmatch(r"^[a-z0-9][a-z0-9.+-]*$", x)][:12]
            m = re.match(r"(?i)^ENV\s+PORT[= ](\d+)", s)
            if m:
                info["env_port"] = m.group(1)
        return info

    RUNTIME_ORDER = ("python", "node", "php", "go", "ruby", "java", "dotnet", "rust", "elixir")

    def pick_runtime(self, root, kinds, files):
        """One runtime per root, DETERMINISTIC (never the order of a set), with explicit priorities:
        PHP when composer.json comes with a front controller (public/index.php, artisan, index.php);
        Python when a WSGI/ASGI entry point or manage.py exists (a package.json there is assets);
        Node when package.json has a start script or a server framework; else the fixed order."""
        native = sorted((k for k in kinds if k != "docker"), key=self.RUNTIME_ORDER.index)
        if not native:
            return "docker" if "docker" in kinds else None
        if len(native) == 1:
            return native[0]
        j = lambda n: self._j(root, n)  # noqa: E731
        if "php" in native and any(self.repo.exists(j(x)) for x in ("public/index.php", "artisan", "index.php", "web/index.php")):
            return "php"
        if "python" in native:
            pyfiles = [p for p in files if p.endswith(".py")]
            if self.repo.exists(j("manage.py")) or any(re.search(r"(?m)^\w+\s*=\s*(Flask|FastAPI|Starlette|create_app)\(|get_wsgi_application|get_asgi_application",
                                                                  self.repo.read(p) or "") for p in pyfiles[:200]):
                return "python"
        if "node" in native:
            try:
                pj = json.loads(self.repo.read(j("package.json")) or "{}")
            except ValueError:
                pj = {}
            deps = set((pj.get("dependencies") or {}).keys())
            if (pj.get("scripts") or {}).get("start") or deps & {"express", "fastify", "next", "@nestjs/core", "koa", "hono", "nuxt", "@strapi/strapi"}:
                return "node"
        return native[0]

    def root_facts(self, root, kinds, compose_names):
        files = [p for p in self.text if in_dir(p, root)]
        code = [p for p in files if CODE_EXT.search(p) and not BROWSER_ASSET.search("/" + p)]
        d = self.deps(root)
        fx = {"path": root, "manifests": sorted(kinds), "runtime": None, "framework": [], "versions": {},
              "start": {}, "listen": {}, "env_names": [], "build_time_env": [], "websockets": [], "workers": [],
              "schedulers": [], "disk_writes": [], "service_hosts": [], "platform_coupling": [], "http_server": None,
              "node": None, "python": None, "dotnet": None, "dockerfile": self.dockerfile(root), "log_markers": [],
              "routes": [], "title": None}
        fx["runtime"] = self.pick_runtime(root, kinds, files)
        # ---- node
        if "node" in kinds:
            nd = {"deps": sorted(d["node"].keys())[:60], "dev_build_tools": sorted(NODE_BUILD_TOOLS & set(d["node_dev"].keys())),
                  "build_tools_in_deps": sorted(NODE_BUILD_TOOLS & set(d["node"].keys()))}
            pj = d.get("package_json", {})
            sc = pj.get("scripts") or {}
            nd["scripts"] = {k: mask(v) for k, v in sc.items() if k in ("build", "start", "postinstall", "prestart", "dev", "serve")}
            nd["main"] = pj.get("main")
            nd["engines_node"] = (pj.get("engines") or {}).get("node")
            nd["package_manager"] = pj.get("packageManager")
            locks = [lf for lf in ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb")
                     if self.repo.exists(self._j(root, lf))]
            nd["lockfile"] = locks[0] if locks else None
            nd["nvmrc"] = self.repo.exists(self._j(root, ".nvmrc"))
            nd["client_router"] = sorted(CLIENT_ROUTERS & (set(d["node"]) | set(d["node_dev"])))      # a single-page app that routes in the browser: deep links need a fallback
            nd["caddyfile"] = self.repo.exists(self._j(root, "Caddyfile"))
            fx["framework"] += [f for f in NODE_FRAMEWORKS if f in d["node"] or f in d["node_dev"]]
            if "next" in d["node"] or "next" in d["node_dev"]:
                fx["next_image"], fx["next_image_evidence"] = self.next_image_facts(files)
            nd["middleware"] = [p for p in files if re.search(r"(^|/)(src/)?(middleware|proxy)\.(t|j)s$", p)]
            nd["src_dir"] = any(p.startswith(self._j(root, "src/")) for p in files) if root == "." else any(p.startswith(root + "/src/") for p in files)
            nd["next_config_hosts"] = []
            for p in files:
                if re.search(r"(^|/)next\.config\.(js|mjs|ts|cjs)$", p):
                    for n, line in enumerate((self.repo.read(p) or "").splitlines(), 1):
                        if re.search(r"vercel-storage\.com", line):
                            nd["next_config_hosts"].append({"file": p, "line": n, "text": mask(line)})
            fx["node"] = nd
            if nd["engines_node"]:
                fx["versions"]["node"] = nd["engines_node"]
            fx["start"]["package_json"] = nd["scripts"].get("start") or (("node " + nd["main"]) if nd["main"] else None)
        # ---- python
        if "python" in kinds:
            py = {"deps": sorted(set(d["python"]))[:60], "commented_deps": sorted(set(d["python_commented"]))}
            for vf in (".python-version", "runtime.txt"):
                t = self.repo.read(self._j(root, vf))
                if t:
                    v = [ln.strip() for ln in t.splitlines() if ln.strip() and not ln.strip().startswith("#")]
                    if v:
                        fx["versions"]["python"] = {"file": self._j(root, vf), "value": v[0]}
            if "python" not in fx["versions"]:
                pp = self.repo.read(self._j(root, "pyproject.toml")) or ""
                m = re.search(r"(?m)^requires-python\s*=\s*['\"]([^'\"]+)['\"]", pp)
                if m:
                    fx["versions"]["python"] = {"file": self._j(root, "pyproject.toml"), "value": m.group(1)}
            py["uv_lock"] = self.repo.exists(self._j(root, "uv.lock"))
            py["requirements_txt"] = self.repo.exists(self._j(root, "requirements.txt"))
            fx["framework"] += [f for f in PY_FRAMEWORKS if f in py["deps"]]
            # module guess
            mods = []
            for p in files:
                t = self.repo.read(p) or ""
                if p.endswith(".py"):
                    m = re.search(r"(?m)^(\w+)\s*=\s*(Flask|FastAPI|Starlette|create_app)\(", t)
                    if m:
                        rel = p[len(root) + 1:] if root != "." else p
                        mods.append({"module": rel[:-3].replace("/", ".") + ":" + m.group(1), "file": p, "kind": m.group(2)})
                    if p.endswith("wsgi.py") and "get_wsgi_application" in t:
                        rel = p[len(root) + 1:] if root != "." else p
                        mods.append({"module": rel[:-3].replace("/", ".") + ":application", "file": p, "kind": "django-wsgi"})
            py["module_candidates"] = mods[:4]
            py["manage_py"] = self.repo.exists(self._j(root, "manage.py"))
            if py["manage_py"]:
                py["django"] = self.django_facts(root, files)
            fx["python"] = py
        # ---- dotnet
        if "dotnet" in kinds:
            projs = [p for p in files if re.search(r"\.(cs|fs|vb)proj$", p)]
            dn = {"projects": projs, "target_frameworks": [], "output_type": None, "web_sdk": False}
            for pj in projs:
                t = self.repo.read(pj) or ""
                for n, line in enumerate(t.splitlines(), 1):
                    m = re.search(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", line)
                    if m:
                        dn["target_frameworks"].append({"value": m.group(1), "file": pj, "line": n})
                    m = re.search(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', line)
                    if m:
                        dn.setdefault("packages", {})[m.group(1)] = {"version": m.group(2), "file": pj, "line": n}
                    m = re.search(r"<OutputType>([^<]+)</OutputType>", line)
                    if m:
                        dn["output_type"] = m.group(1)
                dn["web_sdk"] = dn["web_sdk"] or "Microsoft.NET.Sdk.Web" in t
            fx["dotnet"] = dn
            if dn["target_frameworks"]:
                fx["versions"]["dotnet"] = dn["target_frameworks"][0]
        # ---- other version pins
        if "go" in kinds:
            t = self.repo.read(self._j(root, "go.mod")) or ""
            m = re.search(r"(?m)^go\s+(\S+)", t)
            if m:
                fx["versions"]["go"] = m.group(1)
            mm = re.search(r"(?m)^module\s+(\S+)", t)
            mains = sorted(set(dirname(p[len(root) + 1:] if root != "." else p) for p in files
                               if p.endswith(".go") and not p.endswith("_test.go") and not SKIP_TEST.search("/" + p)
                               and re.search(r"(?m)^package main\b", self.repo.read(p) or "") and re.search(r"(?m)^func main\(", self.repo.read(p) or "")))
            fx["go"] = {"module": mm.group(1) if mm else None, "main_dirs": mains}
        if "php" in kinds:
            try:
                cj = json.loads(self.repo.read(self._j(root, "composer.json")) or "{}")
            except ValueError:
                cj = {}
            fx["php"] = {"require_php": (cj.get("require") or {}).get("php"),
                         "public_index": self.repo.exists(self._j(root, "public/index.php")),
                         "laravel": self.repo.exists(self._j(root, "artisan")) and "laravel/framework" in (cj.get("require") or {})}
        # ---- code facts
        fx["listen"] = {
            "reads_port": self.repo.grep(r"process\.env\.PORT|os\.environ(\.get)?\(?\[?['\"]PORT|getenv\(['\"]PORT|ENV\[['\"]PORT|Getenv\(\"PORT\"|env::var\(\"PORT\"|\$\{?PORT\b", files, max_hits=5),
            # a port number in a SERVER call (listen, ListenAndServe, --port, PORT = …), any value but 8080
            "hardcoded_ports": [h for h in self.repo.grep(r"(\.listen\(\s*|ListenAndServe\(\s*['\"][^'\"]*:|Addr:\s*['\"][^'\"]*:|--port[= ]|--http[= ][^:\s]*:|\bPORT\s*=\s*|\bport\s*[:=]\s*|-b\s+[^:\s]*:|--bind[= ][^:\s]*:)['\"]?(\d{2,5})\b",
                                                          [p for p in code if not SKIP_TEST.search("/" + p)] + [p for p in files if os.path.basename(p) in ("Dockerfile", "Procfile", "gunicorn.conf.py")], max_hits=8)
                                if not re.search(r"8080|9000|\b(5432|3306|6379|27017|9200|5672|587|465|443|25|22)\b|\bdb\b|database|redis|postgres|mysql|mongo|smtp|mail|env\(|getenv|environ|process\.env|window\.", h["text"], re.I)][:4],
            # binds a server to localhost (a CLIENT host such as DB_HOST=env(…,'127.0.0.1') or window.location is not a bind)
            "localhost_bind": [h for h in self.repo.grep(r"(\.listen\(|\.run\(|uvicorn\.run\(|--bind[= ]|--host[= ]|-b\s|\bbind\s*=|ListenAndServe\()[^\n]{0,40}['\"]?(localhost|127\.0\.0\.1)\b", code, max_hits=6)
                               if not re.search(r"window\.location|env\(|getenv|environ|process\.env|_HOST", h["text"])][:4],
        }
        http_hits = self.repo.grep(r"\.listen\(|createServer|app\.run\(|uvicorn\.run|http\.ListenAndServe|WebApplication\.|HttpListener|UseUrls|Kestrel|express\(\)|Flask\(|FastAPI\(|get_wsgi_application|get_asgi_application|ServeHTTP|actix_web|axum::", code, max_hits=3)
        fx["http_server"] = bool(http_hits)
        fx["http_evidence"] = http_hits[:2]
        names = set()
        fx["env_reads"] = {}
        for p in code:
            if SKIP_TEST.search("/" + p):
                continue
            t = self.repo.read(p) or ""
            for m in ENV_NAME_RX.finditer(t):
                for g in m.groups():
                    if g:
                        names.add(g)
                        fx["env_reads"].setdefault(g, {"file": p, "line": t[:m.start()].count("\n") + 1})
        if "python" in kinds and "DATABASE_URL" not in fx["env_reads"]:
            implicit = self.python_database_url([p for p in code if p.endswith(".py") and not SKIP_TEST.search("/" + p)])
            if implicit:       # dj_database_url.config() / env.db() read DATABASE_URL without naming it: Clever never injects it (test T2)
                fx["env_reads"]["DATABASE_URL"] = implicit
                names.add("DATABASE_URL")
        names -= {"PATH", "HOME", "HOSTNAME"}
        fx["env_names"] = sorted(names)
        fx["build_time_env"] = sorted(n for n in names if PUBLIC_PREFIX.match(n))
        fx["websockets"] = self.repo.grep(r"socket\.io|new WebSocketServer|from ['\"]ws['\"]|require\(['\"]ws['\"]\)|channels\.layers|ActionCable|gorilla/websocket|laravel-echo|reverb|SignalR", code + [p for p in files if p.endswith(".html")], max_hits=3)
        fx["socket_events"] = sorted(set(m.group(1) for h in self.repo.grep(r"\.emit\(\s*['\"][A-Za-z_][\w-]*['\"]", code, max_hits=10)
                                         for m in [re.search(r"\.emit\(\s*['\"]([A-Za-z_][\w-]*)['\"]", h["text"])] if m))
        fx["workers"] = self.repo.grep(r"bullmq|new Queue\(|\bcelery\b|sidekiq|rq\.Queue|amqplib|kafkajs|pg-boss|graphile-worker|ListLeftPop|BLPOP|blpop", code, max_hits=4)
        fx["schedulers"] = self.repo.grep(r"node-cron|cron\.schedule|@nestjs/schedule|APScheduler|celery.*beat|sidekiq-cron|schedule:run|robfig/cron", code, max_hits=3)
        fx["disk_writes"] = [h for h in self.repo.grep(r"writeFile(Sync)?\(|createWriteStream\(|multer\(|diskStorage|UPLOAD_(DIR|PATH|FOLDER)|MEDIA_ROOT|FileSystemStorage|os\.WriteFile|sqlite3\.connect\(|\.(sqlite3?|db)['\"]|\.save\(\s*os\.path\.join", [p for p in code if not SKIP_TEST.search("/" + p)], max_hits=6)
                             if not re.search(r"https?://", h["text"])][:4]
        # H7: variables read with no default — the app stops (KeyError, sys.exit, throw) without them
        fx["env_required"] = sorted(set(m.group(1) for p in code if p.endswith(".py") and not SKIP_TEST.search("/" + p)
                                        for m in re.finditer(r"os\.environ\[\s*['\"]([A-Z_][A-Z0-9_]*)['\"]\s*\]", self.repo.read(p) or "")))
        src = [p for p in code if not SKIP_TEST.search("/" + p)]
        fx["sqlite_uses"] = self.repo.grep(r"sqlite3\.connect\(|new\s+(Database|sqlite3\.Database)\(|['\"]sqlite:///|provider\s*=\s*\"sqlite\"|django\.db\.backends\.sqlite3", src, max_hits=12)
        for h in fx["sqlite_uses"]:
            # R39: a sqlite:/// URI continued from the line above (`os.environ.get('DATABASE_URL') or \` then the URI): that line, so the plan knows which variable
            # the URI is the fallback of
            ls = (self.repo.read(h["file"]) or "").splitlines()
            k = h["line"] - 2
            while k >= 0 and not ls[k].strip():
                k -= 1
            if "sqlite:///" in ls[h["line"] - 1] and k >= 0 and re.search(r"(\\|\bor|\|\||\?\?|[(,=+])\s*\Z", ls[k].rstrip()) and not COMMENT.match(ls[k].strip()):
                h["prev"] = mask(ls[k], limit=200)
        # a Prisma schema is not a code file: its SQLite datasource comes from the schema reader (comments and other blocks skipped)
        fx["sqlite_uses"] = (fx["sqlite_uses"] + [h for h in self.prisma_sqlite() if in_dir(h["file"], root)])[:12]
        pt = self.prisma_tables(root)
        if pt:
            fx["prisma_tables"] = pt
        if "node" in kinds:
            fx["node"]["prisma_cli"] = "dependencies" if "prisma" in d["node"] else ("devDependencies" if "prisma" in d["node_dev"] else None)
        fx["upload_uses"] = self.repo.grep(r"\.save\(\s*os\.path\.join|send_from_directory\(|os\.listdir\(\s*UPLOAD|os\.remove\(\s*os\.path\.join|multer\(\s*\{?\s*dest|diskStorage\(", src, max_hits=12)
        # names of the file fields of an upload form (request.files['x'], multer .single('x'))
        fx["file_fields"] = sorted(set(m.group(1) for p in src
                                       for m in re.finditer(r"request\.files(?:\.get\(\s*|\[\s*)['\"]([A-Za-z_][A-Za-z0-9_]{0,40})['\"]|\.single\(\s*['\"]([A-Za-z_][A-Za-z0-9_]{0,40})['\"]", self.repo.read(p) or "")
                                       if m.group(1)) | set(m.group(1) for p in src
                                       for m in re.finditer(r"\.single\(\s*['\"]([A-Za-z_][A-Za-z0-9_]{0,40})['\"]", self.repo.read(p) or "")))[:4]
        # names of the form fields a POST handler reads (a real POST check needs a field the app expects)
        fx["form_fields"] = sorted(set(m.group(1) for p in src if p.endswith(".py")
                                       for m in re.finditer(r"request\.form(?:\.get\(\s*|\[\s*)['\"]([A-Za-z_][A-Za-z0-9_]{0,40})['\"]", self.repo.read(p) or "")))[:8]
        fx["creates_schema"] = bool(self.repo.grep(r"(?i)create\s+table", [p for p in code if not SKIP_TEST.search("/" + p)], max_hits=1))
        fx["platform_coupling"] = self.repo.grep(r"['\"]DYNO['\"]|\bHEROKU_[A-Z_]+|\bVERCEL_URL\b|PLATFORM_RELATIONSHIPS|\bFLY_APP_NAME\b|\bRENDER_EXTERNAL_URL\b|\bRAILWAY_[A-Z_]+|\bK_SERVICE\b|\bWEBSITE_SITE_NAME\b|\bSCALINGO_[A-Z_]+", code, max_hits=5)
        # hard-coded hostnames of other compose services
        if compose_names:
            names_all, names_ds = compose_names
            alt = "|".join(re.escape(n) for n in sorted(names_all, key=len, reverse=True))
            alt_ds = "|".join(re.escape(n) for n in sorted(names_ds, key=len, reverse=True)) or "(?!x)x"
            # only literals in HOST position: host=/server=, a URL, or the first argument of a connection call
            # (hasattr(g, 'redis') or a dict key 'redis' is NOT a host)
            rx = re.compile(r"(?i)(?:\bhost(?:name)?\s*[=:]\s*|\bserver\s*=\s*|://(?:[^@/\s'\"]*@)?"
                            r"|\b\w*(?:Redis|Connection|connect|Client|Pool)\w*\(\s*)['\"]?(" + alt + r")(?=[:;/'\"\s),]|$)")
            for h in self.repo.grep(rx, [p for p in code if not SKIP_TEST.search("/" + p)], max_hits=12):
                line = self.repo.read(h["file"]).splitlines()[h["line"] - 1]
                if re.search(r"(?i)redis|postgres|pg|mysql|mongo|amqp|host|server|connect|@|//", line):
                    m = rx.search(line)
                    h["service"] = m.group(1) if m else None
                    if h["service"]:
                        fx["service_hosts"].append(h)
        # latent bugs that fail the deploy: Node core modules used without being required/imported. express.static declared BEFORE the use serves the page
        # the route was meant to answer (test T3: GET / answered 200 without require('path')): not an alert, kept apart in missing_requires_shadowed
        fx["missing_requires"] = []
        fx["missing_requires_shadowed"] = []
        for p in code:
            if not p.endswith((".js", ".cjs", ".mjs")):
                continue
            t = self.repo.read(p) or ""
            for mod in ("path", "fs", "os", "url", "crypto", "util"):
                use = re.search(r"(?<![\w.$])%s\.(\w+)\(" % mod, t)
                if not use:
                    continue
                declared = re.search(r"require\(\s*['\"](node:)?%s['\"]\s*\)|import\s+[^;]*from\s+['\"](node:)?%s['\"]|\b(var|let|const)\s+%s\b|\bfunction\s*\([^)]*\b%s\b" % (mod, mod, mod, mod), t)
                if not declared:
                    line = t[:use.start()].count("\n") + 1
                    rec = {"file": p, "line": line, "module": mod, "text": mask(t.splitlines()[line - 1])}
                    static = STATIC_SERVER.search(t)
                    if static and static.start() < use.start():
                        rec["static_line"] = t[:static.start()].count("\n") + 1
                        fx["missing_requires_shadowed"].append(rec)
                    else:
                        fx["missing_requires"].append(rec)
        # log markers & routes (for prove suggestions)
        fx["log_markers"] = [h for h in self.repo.grep(r"(Console\.WriteLine|console\.log|print|logger\.info|log\.Printf)\(\s*\$?[\"'f]?[\"']?(Processing|Connected|Received|Listening|Started)", code, max_hits=4)]
        fx["routes"] = [h for h in self.repo.grep(r"@app\.route\(|\bpath\(\s*['\"]|app\.(get|post)\(\s*['\"]/|router\.(get|post)\(|HandleFunc\(\"/", code, max_hits=12)
                        if not re.match(r"^(\d+\.|#|//|\*)", h["text"])][:8]
        fx["health_routes"] = self.health_routes(root, [p for p in code if not SKIP_TEST.search("/" + p)])
        fx["app_objects"] = self.app_objects([p for p in code if not SKIP_TEST.search("/" + p)])
        for p in files:
            if p.endswith(".html") and ("templates/" in p or "views/" in p or os.path.basename(p) == "index.html"):
                t = self.repo.read(p) or ""
                m = re.search(r"<title>([^<{]{3,80})</title>", t)
                if m:
                    fx["title"] = {"text": m.group(1).strip(), "file": p}
                    break
        return fx

    def next_image_facts(self, files):
        """(next_image, evidence) of a Next.js root. True: a source file imports next/image (or its legacy / future flavours): the app asks the server
        to optimise images, which needs XS (test T6: nano is Killed). False: next.config says images.unoptimized: true (the optimiser is off). None: nothing
        says — sizing then takes the safe side (XS, [assumed]); the absence of an import proves nothing (a dependency may render next/image)."""
        for p in files:
            if re.search(r"(^|/)next\.config\.(js|mjs|ts|cjs)$", p):
                for n, line in enumerate((self.repo.read(p) or "").splitlines(), 1):
                    if not COMMENT.match(line.strip()) and re.search(r"\bunoptimized\s*:\s*true\b", line):
                        return False, {"file": p, "line": n, "text": mask(line)}
        for p in files:
            if not CODE_EXT.search(p) or SKIP_TEST.search("/" + p):
                continue
            for n, line in enumerate((self.repo.read(p) or "").splitlines(), 1):
                if NEXT_IMAGE_IMPORT.search(line) and not COMMENT.match(line.strip()):
                    return True, {"file": p, "line": n, "text": mask(line)}
        return None, None

    def python_database_url(self, py_files):
        """The first place where Python code reads DATABASE_URL other than through the readers env_reads already knows (os.environ, os.getenv, env(...)):
        dj_database_url.config() and django-environ's env.db() read it WITHOUT naming it — {"file", "line", "implicit"} — unless the call (read whole, its
        arguments may run over several lines, a # comment may hold an apostrophe) names another variable (env=, or a first positional argument); the name
        written as the first argument of a reading call (decouple's config('DATABASE_URL'), env.str(…), settings.get(…)) or as the alias of a settings field
        (pydantic Field(…, validation_alias='DATABASE_URL')) — {"file", "line"}; or None. A bare string (a log line, a help text) is not a read."""
        for p in py_files:
            t = self.repo.read(p) or ""
            found = []
            for m in re.finditer(r"\b(?:dj_database_url\.config|env\.db(?:_url)?)\(", t):
                n = t[:m.start()].count("\n") + 1
                if COMMENT.match(t.split("\n")[n - 1].strip()):
                    continue
                call = balanced(t, m.end() - 1, lang="py")
                args = strip_py_comments(call[1:-1]) if call else None
                if args is None or not (args.strip() == "" or re.match(r"\s*\w+\s*=(?!=)", args)) or re.search(r"(?<![\w.])env\s*=(?!=)", args):
                    continue
                found.append((n, {"file": p, "line": n, "implicit": "dj_database_url.config()" if m.group(0).startswith("dj_") else "env.db()"}))
            for n, line in enumerate(t.split("\n"), 1):
                if not COMMENT.match(line.strip()) and (DATABASE_URL_CALL.search(line) or DATABASE_URL_FIELD.search(line)):
                    found.append((n, {"file": p, "line": n}))
            if found:
                return sorted(found, key=lambda x: x[0])[0][1]
        return None

    def fn_block(self, t, paren_idx):
        """The block ({…}) of a function whose parameter list opens at t[paren_idx]: after the closing parenthesis and an optional return type — else None
        (the body is not where the signature ends: never the next block of the file)."""
        sig = balanced(t, paren_idx)
        if sig is None:
            return None
        after = paren_idx + len(sig)
        b = re.match(r"[^{};=]*\{", t[after:after + 300])
        return balanced(t, after + b.end() - 1, "{", "}") if b else None

    def named_body(self, t, name):
        """The body of the function `name` defined in the same file (JS function or const arrow, Go func or method), or None when it cannot be located with
        certainty: a name bound to a call (const h = makeHealth(), require("./handlers").health) or to something else has its body elsewhere."""
        name = re.split(r"\.", name)[-1]
        n = re.escape(name)
        m = re.search(r"\b(?:async\s+)?function\s+%s\s*\(|\bfunc\s+(?:\([^)]*\)\s*)?%s\s*\(" % (n, n), t)
        if m:
            return self.fn_block(t, m.end() - 1)
        m = re.search(r"\b(?:const|let|var)\s+%s\s*(?::[^=\n]+)?=\s*" % n, t)
        if not m:
            return None
        rest = t[m.end():m.end() + 400]
        f = re.match(r"(?:async\s+)?function\b[^(]*\(", rest)
        if f:
            return self.fn_block(t, m.end() + f.end() - 1)
        a = re.match(r"(?:async\s+)?(?:\([^(){};]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]*?)?=>\s*", rest)
        if not a:
            return None
        j = m.end() + a.end()
        if t[j:j + 1] == "{":
            return balanced(t, j, "{", "}")
        k = t.find("\n", j)
        return t[j:k if k >= 0 else len(t)]

    def app_object_list(self, code):
        """Every app object built in the code of a root: [(file, name)] — a Flask / FastAPI object, an express() / fastify() / Hono app, a Go mux or engine."""
        out = []
        for p in code:
            rx = PY_APP if p.endswith(".py") else GO_APP if p.endswith(".go") else JS_APP if p.endswith((".js", ".mjs", ".cjs", ".ts")) else None
            if rx is not None:
                out += [(p, n) for n in sorted(set(m.group(1) for m in rx.finditer(self.repo.read(p) or "")))]
        return out

    def app_objects(self, code):
        """Where the app object is built: [{"file", "name"}] (6 at most). The file a new route on the app object goes to."""
        return [{"file": f, "name": n} for f, n in self.app_object_list(code)][:6]

    def health_routes(self, root, code):
        """The health routes a root registers on its app object (see HEALTH_PATH for what is read, and why only that): [{"path", "file", "line",
        "touches_database"}] (4 at most). `path` is the path as written, trailing slash included. `touches_database`: True / False when the handler's body was
        read (a Python def, parameters over several lines included, up to its dedent; a JS or Go call up to its closing parenthesis; a function or arrow of the
        same file) and holds a database access or not; None when the handler is somewhere the scan did not follow — never guessed. Nothing is read when the
        root builds more than one app object (one may be mounted under a prefix, from another file), nor in a file where a mount, a prefix, a basePath or a
        StripPrefix is seen: the path may be served under a prefix."""
        out = []
        if len(self.app_object_list(code)) > 1:
            return out
        cfg = "".join(self.repo.read(q) or "" for q in code if re.search(r"(^|/)next\.config\.(js|mjs|ts|cjs)$", q))
        for p in code:
            t = self.repo.read(p) or ""
            rel = p if root == "." else p[len(root) + 1:]
            hf = HEALTH_FILE.search(rel)
            if hf:        # Next.js file-based route: the whole file is the handler; a basePath or trailingSlash setting changes the served path
                if not re.search(r"\b(?:basePath|trailingSlash)\b", cfg):
                    out.append({"path": "%s/%s" % ("/api" if "/api/" in "/" + rel else "", hf.group(1)), "file": p, "line": 1, "touches_database": bool(TOUCHES_DB.search(t))})
                continue
            if p.endswith(".py"):
                apps = sorted(set(m.group(1) for m in PY_APP.finditer(t)))
                if len(apps) != 1 or MOUNTED["py"].search(t):
                    continue
                lines = t.split("\n")
                starts = [0]
                for ln in lines[:-1]:
                    starts.append(starts[-1] + len(ln) + 1)
                for m in re.finditer(r"(?m)^[ \t]*@(%s)\.(?:route|get|post|api_route)\(\s*(?:(?:rule|path)\s*=\s*)?[rbf]?[\"']%s[\"']" % ("|".join(map(re.escape, apps)), HEALTH_PATH), t):
                    n = t[:m.start()].count("\n") + 1
                    i = n
                    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("@")):
                        i += 1
                    body = None
                    d = re.match(r"\s*(?:async\s+)?def\s+\w+\s*\(", lines[i]) if i < len(lines) else None
                    if d:
                        ind = len(lines[i]) - len(lines[i].lstrip())
                        sig = balanced(t, starts[i] + d.end() - 1, lang="py")          # the parameter list may run over several lines
                        if sig is not None:
                            end = starts[i] + d.end() - 1 + len(sig)
                            j = t[:end].count("\n")                                  # the line of the closing parenthesis
                            colon = lines[j][end - starts[j]:].find(":")
                            if colon >= 0:
                                got = [lines[j][end - starts[j] + colon + 1:]]        # a one-line body, or a comment
                                for ln in lines[j + 1:j + 121]:
                                    if ln.strip() and len(ln) - len(ln.lstrip()) <= ind:      # the next definition, route or statement at the def's level
                                        break
                                    got.append(ln)
                                body = "\n".join(got)
                    out.append({"path": m.group(2), "file": p, "line": n, "touches_database": None if body is None else bool(TOUCHES_DB.search(body))})
            elif p.endswith((".js", ".mjs", ".cjs", ".ts", ".go")):
                go = p.endswith(".go")
                own = set(m.group(1) for m in (GO_APP if go else JS_APP).finditer(t))
                # several apps (a sub-application mounted under a prefix), a mount / prefix / basePath / StripPrefix, chi's Route: not certain
                if len(own) > 1 or MOUNTED["go" if go else "js"].search(t):
                    continue
                apps = sorted(own | ({"http"} if go else set()))
                if not apps:
                    continue
                verbs = "HandleFunc|Handle|GET|Get" if go else "get|all"
                q = "\"" if go else "[\"']"
                for m in re.finditer(r"(?<![\w$.])(?:%s)\.(?:%s)(\()\s*%s(?:[A-Z]+\s+)?%s%s\s*," % ("|".join(map(re.escape, apps)), verbs, q, HEALTH_PATH, q), t):
                    span = balanced(t, m.start(1))
                    body = span
                    if span and not re.search(r"=>|\bfunction\b|\bfunc\s*\(", span):          # a named handler: its body, if it is in this file
                        ident = re.search(r"([A-Za-z_$][\w$.]*)\s*\)\Z", span)
                        body = self.named_body(t, ident.group(1)) if ident else None
                    out.append({"path": m.group(2), "file": p, "line": t[:m.start()].count("\n") + 1, "touches_database": None if body is None else bool(TOUCHES_DB.search(body))})
            if len(out) >= 4:
                break
        return out[:4]

    def django_facts(self, root, files):
        out = {}
        for p in files:
            if not p.endswith("settings.py") and "/settings/" not in p:
                continue
            t = self.repo.read(p) or ""
            for n, line in enumerate(t.splitlines(), 1):
                uc = re.search(r"\bROOT_URLCONF\s*=\s*['\"]([A-Za-z0-9_.]+)['\"]", line)
                if uc and "root_urlconf" not in out:      # the module that holds the project's urlpatterns: where a new route goes
                    out["root_urlconf"] = {"module": uc.group(1), "file": (root + "/" if root != "." else "") + uc.group(1).replace(".", "/") + ".py", "line": n}
                if re.search(r"ALLOWED_HOSTS\s*=", line):
                    out.setdefault("allowed_hosts", []).append({"file": p, "line": n, "text": mask(line)})
                if re.search(r"\b(IS_HEROKU_APP|IS_[A-Z_]*PROD[A-Z_]*)\s*=", line) or re.search(r"['\"]DYNO['\"]\s+in\s+os\.environ", line):
                    out.setdefault("platform_gate", []).append({"file": p, "line": n, "text": mask(line)})
                if re.search(r"SECRET_KEY\s*=", line) or re.search(r"['\"]DJANGO_SECRET_KEY['\"]", line):
                    out.setdefault("secret_key", []).append({"file": p, "line": n, "text": mask(line)})
                if re.search(r"Manifest(Static)?FilesStorage|STATIC_ROOT", line):
                    out.setdefault("static", []).append({"file": p, "line": n, "text": mask(line)})
                if re.search(r"dj_database_url|DATABASES\s*=|sqlite3|ssl_require", line):
                    out.setdefault("database", []).append({"file": p, "line": n, "text": mask(line)})
                if re.search(r"SECURE_SSL_REDIRECT|SECURE_PROXY_SSL_HEADER", line):
                    out.setdefault("https", []).append({"file": p, "line": n, "text": mask(line)})
        return out

    # ------------------------------------------------------------ provenance files
    def compose(self):
        cfs = [p for p in self.repo.paths if re.search(r"(^|/)(docker-)?compose\.ya?ml$", p)]
        if not cfs:
            return None
        cf = sorted(cfs, key=lambda p: (p.count("/"), len(p)))[0]
        data, line_of = yaml_lite(self.repo.read(cf) or "")
        svcs = []
        for name, s in ((data or {}).get("services") or {}).items():
            s = s or {}
            b = s.get("build")
            ctx = b.get("context") if isinstance(b, dict) else b
            env = s.get("environment")
            env_names = sorted(env.keys()) if isinstance(env, dict) else sorted(
                str(x).split("=")[0] for x in (env or []) if isinstance(x, str))
            vols = s.get("volumes") or []
            svcs.append({
                "name": name, "line": line_of.get("services." + name),
                "image": s.get("image") if isinstance(s.get("image"), str) else None,
                "build": (ctx or ".").lstrip("./") or "." if b is not None else None,
                "target": b.get("target") if isinstance(b, dict) else None,
                "ports": [str(p) for p in (s.get("ports") or [])],
                "volumes": [str(v) for v in vols if isinstance(v, str)],
                "named_volumes": [str(v).split(":")[0] for v in vols if isinstance(v, str) and not str(v).startswith((".", "/", "~"))],
                "depends_on": list(s.get("depends_on").keys()) if isinstance(s.get("depends_on"), dict) else (s.get("depends_on") or []),
                "entrypoint": mask(" ".join(map(str, s["entrypoint"])) if isinstance(s.get("entrypoint"), list) else str(s.get("entrypoint")), 300) if s.get("entrypoint") else None,
                "command": mask(" ".join(map(str, s["command"])) if isinstance(s.get("command"), list) else str(s.get("command")), 300) if s.get("command") else None,
                "expose": [str(x) for x in (s.get("expose") or [])],
                "profiles": s.get("profiles") or [],
                "environment_names": env_names,
                "environment_refs": env_refs(env),
                "healthcheck": bool(s.get("healthcheck")),
            })
        others = [p for p in cfs if p != cf]
        # a compose that only describes a DEV or DEMO setup must never drive the plan
        dev = []
        if "/" in cf:
            dev.append("compose file in a subfolder (%s), not at the repository root" % cf)
        if re.search(r"(^|/)\.devcontainer/", cf):
            dev.append("devcontainer compose")
        for sv in svcs:
            if (sv["build"] or "").startswith("vendor/") or "LARAVEL_SAIL" in sv["environment_names"] or re.search(r"sail-", sv["image"] or ""):
                dev.append("Laravel Sail (development containers built from vendor/)")
                break
        raw = self.repo.read(cf) or ""
        if re.search(r"sleep\s+infinity|network_mode:\s*['\"]?service:", raw):
            dev.append("a service that only sleeps or shares another's network (dev container)")
        return {"file": cf, "services": svcs, "other_compose_files": others, "dev_reasons": sorted(set(dev))}

    def procfile(self):
        pf = next((p for p in self.repo.paths if p == "Procfile"), None)
        if not pf:
            return None
        ents = []
        for n, line in enumerate((self.repo.read(pf) or "").splitlines(), 1):
            m = re.match(r"^\s*(#\s*)?([a-z][a-z0-9_-]*):\s+(?!//)(.+)$", line)
            if m:
                ents.append({"type": m.group(2), "command": mask(m.group(3)), "line": n, "commented": bool(m.group(1))})
        return {"file": pf, "entries": ents}

    def vm_files(self):
        """A VM set up by hand: systemd units, supervisor programs, crontabs, nginx sites (names and commands only;
        Environment= VALUES are never kept — only the variable names)."""
        out = {"systemd": [], "supervisor": [], "crontab": [], "nginx": []}
        for p in self.repo.paths:
            if SKIP_TEST.search("/" + p):
                continue
            base = os.path.basename(p)
            if p.endswith(".service"):
                t = self.repo.read(p) or ""
                m = re.search(r"(?m)^ExecStart=(.+)$", t)
                if m:
                    out["systemd"].append({"file": p, "line": t[:m.start()].count("\n") + 1, "exec": mask(m.group(1), 300),
                                           "env_names": sorted(set(re.findall(r"(?m)^Environment=\"?([A-Z_][A-Z0-9_]*)=", t))),
                                           "env_file": bool(re.search(r"(?m)^EnvironmentFile=", t))})
            elif re.search(r"supervisor", p) and p.endswith(".conf"):
                t = self.repo.read(p) or ""
                for m in re.finditer(r"(?ms)^\[program:([^\]]+)\](.*?)(?=^\[|\Z)", t):
                    c = re.search(r"(?m)^command\s*=\s*(.+)$", m.group(2))
                    if c:
                        out["supervisor"].append({"file": p, "name": m.group(1).strip(), "command": mask(c.group(1).strip(), 300)})
            elif base == "crontab" or base.endswith(".cron") or "/cron.d/" in "/" + p:
                for n, line in enumerate((self.repo.read(p) or "").splitlines(), 1):
                    tz = re.match(r"^\s*(?:CRON_TZ|TZ)\s*=\s*([A-Za-z_]+(?:/[A-Za-z_+-]+){0,2})\s*$", line)
                    if tz:
                        out["cron_tz"] = tz.group(1)
                    m = re.match(r"^\s*((?:[0-9*/,-]+\s+){4}[0-9*/,-]+|@(?:daily|hourly|weekly|monthly))\s+(.+)$", line)
                    if m:
                        out["crontab"].append({"file": p, "line": n, "schedule": m.group(1), "command": mask(m.group(2), 300)})
            elif "nginx" in p and (p.endswith(".conf") or "/sites-" in p or "/nginx/" in p):
                t = self.repo.read(p) or ""
                if "server" in t:
                    mb = re.search(r"client_max_body_size\s+([0-9]+[kKmMgG]?)", t)
                    sn = re.search(r"server_name\s+([^;]+);", t)
                    out["nginx"].append({"file": p, "max_body": mb.group(1) if mb else None, "server_name": untrusted(sn.group(1).strip(), 60) if sn else None,
                                         "static_locations": re.findall(r"location\s+(/[\w/.-]*)\s*\{[^}]*\b(?:alias|root)\b", t)[:3],
                                         "proxy_pass": bool(re.search(r"proxy_pass", t))})
        return out

    def manifest_json(self, name):
        if not self.repo.exists(name):
            return None
        try:
            d = json.loads(self.repo.read(name) or "{}")
        except ValueError:
            return {"file": name, "error": "invalid JSON"}
        env = []
        for k, v in (d.get("env") or {}).items():
            v = v if isinstance(v, dict) else {"value": v}
            env.append({"name": k, "generator": v.get("generator"), "required": v.get("required", True),
                        "has_default_value": "value" in v})
            refs = dollar_refs(v.get("value")) if isinstance(v.get("value"), str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(k)) else []
            if refs:
                env[-1]["refs"] = refs
        addons, versions = [], {}
        for a in d.get("addons") or []:
            nm = a if isinstance(a, str) else (a.get("plan") or a.get("name") or "?")
            addons.append(nm)
            v = ((a.get("options") or {}).get("version") if isinstance(a, dict) else None)
            if v:
                versions[str(nm).split(":")[0]] = str(v)[:10]
        return {"file": name, "env": env, "addons": addons, "addon_versions": versions, "formation": d.get("formation"),
                "scripts": sorted((d.get("scripts") or {}).keys()), "buildpacks": [b.get("url") for b in d.get("buildpacks") or [] if isinstance(b, dict)]}

    def supabase_counters(self):
        mig = [p for p in self.repo.paths if p.startswith("supabase/migrations/") and p.endswith(".sql")]
        sql = "\n".join(self.repo.read(p) or "" for p in mig)

        def c(rx):
            return len(self.repo.grep(rx, self.code, max_hits=500))

        def sites(rx):          # first line per file, for the level-B/C change list
            seen, out = set(), []
            for h in self.repo.grep(rx, self.code, max_hits=500):
                if h["file"] not in seen:
                    seen.add(h["file"]); out.append({"file": h["file"], "line": h["line"]})
            return out[:12]
        buckets = []
        # F6: one statement at a time (split on ';', a cap per statement), anchored — never a lazy scan of
        # the whole concatenated SQL (that was quadratic on a crafted migration)
        for stmt in sql.split(";"):
            if "storage.buckets" not in stmt.lower() or len(stmt) > 20000:
                continue
            m = re.match(r"(?is)\s*insert\s+into\s+storage\.buckets\s*\(([^)]*)\)\s*values\s*(.*)$", stmt)
            if not m:
                continue
            cols = [x.strip().strip('"').lower() for x in m.group(1).split(",")]
            for row in re.findall(r"\(([^()]*)\)", re.split(r"(?i)\bon\s+conflict\b", m.group(2))[0]):
                vals = [x.strip().strip("'").lower() for x in row.split(",")]
                if len(vals) == len(cols):
                    d = dict(zip(cols, vals))
                    if re.fullmatch(r"[a-z0-9._-]{1,63}", d.get("id", "")):
                        buckets.append({"id": d["id"], "public": d.get("public") in ("true", "t") if "public" in d else None})
        sb_deps = []
        for pj in [p for p in self.repo.paths if p == "package.json" or p.endswith("/package.json")][:5]:
            try:
                dj = json.loads(self.repo.read(pj) or "{}")
            except ValueError:
                continue
            for k in sorted(set(dj.get("dependencies") or {}) | set(dj.get("devDependencies") or {})):
                if k.startswith("@supabase/") and re.fullmatch(r"@supabase/[a-z0-9._-]{1,60}", k):
                    sb_deps.append({"file": pj, "name": k})
        return {"sites": {"from": sites(r"\.from\(['\"][A-Za-z_]+['\"]\)"),
                          "auth": sites(r"\.auth\.(signIn|signUp|signOut|getSession|getUser|onAuthStateChange|admin)"),
                          "storage": sites(r"\.storage\.from\(")},
                "storage_buckets": buckets[:20], "supabase_deps": sb_deps[:10],
                "auth_users_trigger": bool(re.search(r"(?is)create\s+trigger\b[^;]*\bon\s+auth\.users\b", sql)),
                "migrations": len(mig), "policies": len(re.findall(r"(?i)create policy", sql)),
                "auth_uid_in_sql": len(re.findall(r"(?i)auth\.uid\(\)", sql)),
                "extensions": sorted(set(re.findall(r"(?i)create extension(?: if not exists)?\s+\"?([a-z0-9_]+)", sql))),
                "calls": {"from": c(r"\.from\(['\"][A-Za-z_]+['\"]\)"), "auth": c(r"\.auth\.(signIn|signUp|signOut|getSession|getUser|onAuthStateChange|admin)"),
                          "storage": c(r"\.storage\.from\("), "channel": c(r"\.channel\(|postgres_changes"), "rpc": c(r"\.rpc\("),
                          "functions_invoke": c(r"functions\.invoke\(")},
                "edge_functions": sorted(set(p.split("/")[2] for p in self.repo.paths if p.startswith("supabase/functions/") and p.count("/") >= 3))}

    def vercel_json(self):
        if not self.repo.exists("vercel.json"):
            return None
        try:
            d = json.loads(self.repo.read("vercel.json") or "{}")
        except ValueError:
            return {"error": "invalid JSON"}
        crons = [{"path": str(c.get("path", "")), "schedule": str(c.get("schedule", ""))} for c in (d.get("crons") or []) if isinstance(c, dict)]
        return {"keys": sorted(d.keys()), "crons": len(crons), "cron_list": crons}

    def sdk_usage(self, rules):
        """For dep_map entries with `symbols`: the lines where a name IMPORTED from the package is called
        (import { get as getBlob } from 'pkg' → calls of getBlob( only; obj.get( never matches)."""
        out = {}
        for r in rules.values():
            for pkg, m in ((r.get("components") or {}).get("dep_map") or {}).items():
                if not m.get("symbols"):
                    continue
                imp = re.compile(r"import\s*(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]%s(?:/[^'\"]*)?['\"]" % re.escape(pkg))
                hits = []
                for p in self.code:
                    t = self.repo.read(p) or ""
                    local = {}
                    for mm in imp.finditer(t):
                        for part in mm.group(1).split(","):
                            part = part.strip()
                            if not part or part.startswith("type "):
                                continue
                            src, _, alias = part.partition(" as ")
                            local[src.strip()] = (alias or src).strip()
                    if not local:
                        continue
                    lines = t.splitlines()
                    for sym in m["symbols"]:
                        names = [local[n] for n in sym.get("names", []) if n in local]
                        if not names:
                            continue
                        rx = re.compile(r"(?<![\w.$])(%s)\s*\(" % "|".join(re.escape(n) for n in names))
                        for n, line in enumerate(lines, 1):
                            if rx.search(line) and not re.match(r"\s*(import|//|\*)", line):
                                hits.append({"file": p, "line": n, "symbol": sym["id"], "text": mask(line)})
                                break
                if hits:
                    out[pkg] = hits
        return out

    def auth_mechanism(self, all_deps):
        """Is there ANY authentication mechanism in the app? (deps or calls)"""
        deps = [d for d in all_deps if re.match(r"^(next-auth|@auth/|@clerk/|lucia|better-auth|passport|iron-session|express-session|jsonwebtoken|jose|@supabase/ssr|firebase-admin|django-allauth|djangorestframework-simplejwt|flask-login|devise)", d)]
        calls = self.repo.grep(r"getServerSession\(|\bauth\(\)|currentUser\(|verifyToken\(|jwt\.verify\(|@login_required|login_required\(|requireAuth\(", self.code, max_hits=3)
        return {"present": bool(deps or calls), "deps": deps[:5], "calls": [ "%s:%d" % (h["file"], h["line"]) for h in calls]}

    @staticmethod
    def client_call(client, t, m):
        """True when the verb call matched at `m` is made on an HTTP client (this.httpService.get, axiosInstance.get, fetcher.get): the identifier chain
        right before the separator, its last segment tested against the rule's `client_receiver`."""
        before = re.search(r"([\w$]+(?:(?:\.|->|::)[\w$]+)*)\Z", t[max(0, m.start() - 120):m.start()])
        return bool(before and client.match(re.split(r"\.|->|::", before.group(1))[-1]))

    def security(self):
        try:
            rules = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "rules", "_security.json")))
            pats = rules["patterns"]
        except (OSError, ValueError, KeyError):
            return []
        client = re.compile(rules["client_receiver"]) if rules.get("client_receiver") else None
        found, routes = [], {}
        for pt in pats:
            rx = re.compile(pt["regex"])
            for p in self.code:
                t = self.repo.read(p) or ""
                if pt.get("path_group"):
                    # a route registration: every match of the file (a client call is dropped), ONE finding per file and id — the first registration —
                    # carrying every path the file registers, in order (the input of a fix that closes a route without touching the code)
                    ms = list(itertools.islice((m for m in rx.finditer(t) if not (client and pt.get("skip_client") and self.client_call(client, t, m))), 24))
                    if not ms:
                        continue
                    rec = routes.get((pt["id"], p))
                    if rec is None:
                        rec = routes[(pt["id"], p)] = {"id": pt["id"], "file": p, "message": pt["message"], "hits": []}
                        found.append(rec)
                    rec["hits"] += [(m.start(), t[:m.start()].count("\n") + 1, "/" + m.group(pt["path_group"]).lstrip("/")) for m in ms]
                    continue
                m = rx.search(t)
                if not m:
                    continue
                if pt.get("absent_in_file") and re.search(pt["absent_in_file"], t):
                    continue
                line = t[:m.start()].count("\n") + 1
                msg = pt["message"] % m.group(pt["group_name"]) if pt.get("group_name") else pt["message"]
                found.append({"id": pt["id"], "file": p, "line": line, "message": msg,
                              "name": m.group(pt["group_name"]) if pt.get("group_name") else None})
        for i, f_ in enumerate(found):
            if "hits" in f_:
                hits = sorted(f_["hits"])
                paths = list(dict.fromkeys(h[2] for h in hits))
                found[i] = {"id": f_["id"], "file": f_["file"], "line": hits[0][1], "message": f_["message"] % hits[0][2], "name": hits[0][2],
                            "path": hits[0][2], "paths": paths[:6]}
        # #13: a Storage INSERT policy that only checks the bucket lets anyone (any signed-in user) write
        # into it — and overwrite or plant files under another user's path
        for p in [x for x in self.repo.paths if x.startswith("supabase/migrations/") and x.endswith(".sql")]:
            t = self.repo.read(p) or ""
            pos = 0
            for stmt in t.split(";"):
                start, pos = pos, pos + len(stmt) + 1
                if "storage.objects" not in stmt.lower() or len(stmt) > 20000:
                    continue
                m = re.match(r'(?is)\s*create\s+policy\s+"?([^"\n]{1,80}?)"?\s+on\s+storage\.objects\b(.*)$', stmt)
                if not m:
                    continue
                body = m.group(2)
                if re.search(r"(?i)\bfor\s+insert\b", body) and re.search(r"(?i)with\s+check", body) \
                        and not re.search(r"(?i)auth\.uid\(\)|owner|storage\.foldername|auth\.jwt\(\)", body):
                    found.append({"id": "storage-insert-bucket-only", "file": p, "line": t[:start + len(stmt) - len(stmt.lstrip())].count("\n") + 1,
                                  "message": "Storage policy %s lets any caller allowed by its role insert into the bucket: its WITH CHECK only tests the bucket (no owner = auth.uid())" % untrusted(m.group(1).strip(), 60),
                                  "name": None})
        return found

    def platform_texts(self):
        """#20: visible page texts that name the source platform ("Deploy to Vercel", "Blob on Vercel"):
        an OPTIONAL wording change once the app runs on Clever (first line per file, 4 files at most)."""
        rx = re.compile(r">\s*([^<>{}]{0,80}\b(Vercel|Netlify|Heroku|Railway|Fly\.io|Render\.com)\b[^<>{}]{0,80})\s*<|"
                        r"\b(?:title|description)\s*:\s*['\"]([^'\"]{0,80}\b(Vercel|Netlify|Heroku|Railway|Fly\.io)\b[^'\"]{0,80})['\"]")
        out, seen = [], set()
        for p in [x for x in self.code if re.search(r"\.(tsx|jsx|vue|svelte|html)$", x)] + [x for x in self.repo.paths if x.endswith(".html")]:
            if p in seen or SKIP_TEST.search("/" + p):
                continue
            seen.add(p)
            for n, line in enumerate((self.repo.read(p) or "").splitlines(), 1):
                m = rx.search(line)
                if m:
                    out.append({"file": p, "line": n, "text": mask((m.group(1) or m.group(3) or "").strip(), 80), "platform": m.group(2) or m.group(4)})
                    break
            if len(out) >= 4:
                break
        return out

    def declared(self):
        """What the repository SAYS about its production — nothing here is verified: plans written in render.yaml, committed dumps
        with their size, hints that the repository is an example. questions.py turns it into pre-filled questions."""
        plans, versions = self.render_plans()
        return {"plans": plans, "versions": versions, "dumps": self.committed_dumps(), "example_hints": self.example_hints()}

    def render_plans(self):
        """(plans, versions) of render.yaml (line scan, no YAML library): one plan entry per service and per database that names a plan, and one
        version entry per database that names its major version (postgresMajorVersion: a strict token only, else dropped)."""
        out, versions = [], []
        for fname in ("render.yaml", "render.yml"):
            t = self.repo.read(fname)
            if not t:
                continue
            section, item, item_indent = None, None, None

            def flush():
                if item and item.get("plan") and PLAN_TOKEN.match(item["plan"]):
                    out.append({"source": fname, "kind": "postgres" if section == "databases" else (item.get("type") or "service")[:20],
                                "name": mask(item.get("name") or "?", 60), "plan": item["plan"]})
                if item and section == "databases" and re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){0,2}", item.get("postgresMajorVersion") or ""):
                    versions.append({"source": fname, "kind": "postgres", "name": mask(item.get("name") or "?", 60), "version": item["postgresMajorVersion"]})
            for line in t.splitlines():
                raw = re.sub(r"\s+#.*$", "", line.rstrip())
                if not raw.strip() or raw.lstrip().startswith("#"):
                    continue
                m = re.match(r"^([A-Za-z]+):\s*$", raw)
                if m:
                    flush()
                    section, item, item_indent = m.group(1), None, None
                    continue
                if section not in ("services", "databases"):
                    continue
                m = re.match(r"^(\s*)-\s+([A-Za-z]+):\s*(.*)$", raw)
                if m and (item_indent is None or len(m.group(1)) <= item_indent):
                    flush()
                    item_indent, item = len(m.group(1)), {}
                    item[m.group(2)] = m.group(3).strip().strip("'\"")
                    continue
                m = re.match(r"^(\s+)([A-Za-z]+):\s*(.*)$", raw)
                if m and item is not None and len(m.group(1)) == item_indent + 2:
                    item[m.group(2)] = m.group(3).strip().strip("'\"")
            flush()
        return out[:12], versions[:4]

    def committed_dumps(self):
        """Committed database files and dumps with their size in bytes (git ls-tree -l): a SQLite or dump file, or an
        SQL file big enough not to be a schema. The five largest, outside migrations, fixtures and tests. Of a SQLite file, only its table names are read
        ("tables", sqlite_tables: RT D5, the table proof names them); nothing else of any file."""
        rc, out, _ = run(["git", "-C", self.top, "ls-tree", "-r", "-l", "-z", "--full-tree", "HEAD"])
        if rc != 0:
            return []
        pre = (self.prefix + "/") if self.prefix else ""
        found = []
        for ent in out.decode("utf-8", "replace").split("\0"):
            meta, _, path = ent.partition("\t")
            parts = meta.split()
            if len(parts) < 4 or parts[1] != "blob" or not parts[3].isdigit() or not path.startswith(pre):
                continue
            rel, size = path[len(pre):], int(parts[3])
            m = DUMP_RX.search(rel)
            if not m or DUMP_SKIP.search(rel):
                continue
            ext = m.group(1).lower() if m.group(1) else "dump"
            if ext in ("sqlite", "sqlite3", "db"):
                kind, keep = "sqlite", size >= 8192
            elif ext == "sql":
                kind, keep = "sql", size >= 100000 or (size >= 1000 and re.search(r"dump|backup|export", rel, re.I))
            else:
                kind, keep = "dump", size >= 1000
            if keep:
                found.append({"file": mask(rel, 120), "bytes": size, "kind": kind, "_full": path})
        found.sort(key=lambda d: (-d["bytes"], d["file"]))
        for d in found[:5]:
            full = d.pop("_full")
            if d["kind"] == "sqlite":
                tabs = self.sqlite_tables(full, d["bytes"])        # RT D5: the table proof names them
                if tabs is not None:
                    d["tables"] = tabs
        return found[:5]

    def example_hints(self):
        """Signs that the repository is an example, not a production system: its folder name, the title of its README, its package name."""
        hints = []
        folder = os.path.basename(self.top.rstrip("/")) + ((" / " + self.prefix) if self.prefix else "")
        if EXAMPLE_RX.search(folder):
            hints.append({"where": "folder name", "text": mask(folder, 60)})
        for name in ("README.md", "README", "README.rst", "readme.md"):
            t = self.repo.read(name)
            if t:
                first = next((x.strip().lstrip("#= ").strip() for x in t.splitlines() if x.strip()), "")
                if EXAMPLE_RX.search(first):
                    hints.append({"where": "README title", "text": mask(first, 80)})
                break
        pj = self.repo.read("package.json")
        if pj:
            try:
                d = json.loads(pj)
            except ValueError:
                d = {}
            nm = d.get("name") if isinstance(d, dict) else None
            if isinstance(nm, str) and EXAMPLE_RX.search(nm):
                hints.append({"where": "package.json name", "text": mask(nm, 60)})
        return hints[:3]

    def poc_branch(self):
        """Files already changed on clever-poc (relative to its merge-base with the current/default branch)."""
        def g(*a):
            rc, out, _ = run(["git", "-C", self.top] + list(a))
            return out.decode("utf-8", "replace").strip() if rc == 0 else ""
        # a BRANCH only: a tag named clever-poc (fetched with the clone) is the repository's word
        if not g("rev-parse", "--verify", "-q", "refs/heads/clever-poc"):
            return None
        cur = g("rev-parse", "--abbrev-ref", "HEAD")
        base_ref = next((b for b in ("main", "master", "trunk") if g("rev-parse", "--verify", "-q", "refs/heads/" + b)), None) if cur == "clever-poc" else "HEAD"
        if not base_ref:
            return {"exists": True, "files_changed": []}
        mb = g("merge-base", base_ref if base_ref == "HEAD" else "refs/heads/" + base_ref, "refs/heads/clever-poc")
        files = [f for f in g("diff", "--name-only", mb, "refs/heads/clever-poc").splitlines() if f] if mb else []
        if self.prefix:
            files = [f[len(self.prefix) + 1:] for f in files if f.startswith(self.prefix + "/")]
        return {"exists": True, "base": base_ref, "commits": len([x for x in g("rev-list", "%s..refs/heads/clever-poc" % mb).splitlines() if x]) if mb else None,
                "files_changed": files}

    # ------------------------------------------------------------ provenance detection
    def holds_database_image(self, folder):
        """True when a manifest of `folder` (the folder itself) runs a database image (rules/_services.json: postgres, mysql, mariadb, mongo, redis…):
        whatever its README says, it is a real database then."""
        rx = [re.compile(x) for k in DB_IMAGE_KINDS for x in (services().get(k) or {}).get("images", [])]
        for q in self.repo.paths:
            if dirname(q) != folder or not q.endswith((".yaml", ".yml")):
                continue
            for im in re.findall(r"(?m)^\s*-?\s*image:\s*[\"']?([^\s\"'#]+)", self.repo.read(q) or ""):
                im = re.sub(r"\A(?:(?:index\.|registry-1\.)?docker\.io/)?(?:library/)?", "", im.lower())
                if any(r.search(im) for r in rx):
                    return True
        return False

    def simulated_dirs(self):
        """{folder: {"readme", "line", "text"}} for the folders (never the repository root) whose OWN README says, in its first 600 characters, that the
        folder simulates a database ("manifests to simulate a database setup") — and whose manifests run no database image: what such a folder holds (a
        StatefulSet, a volume claim, CronJobs) is a simulator, not a database to migrate. A README of an ancestor folder, a sentence that simulates something
        else ("simulate 500 users", "terraform plan simulates the changes"), a README that says it further down: not counted."""
        if not hasattr(self, "_simulated"):
            out = {}
            for p in self.repo.paths:
                d = dirname(p)
                if d == "." or not README_NAME.match(os.path.basename(p)):
                    continue
                head = (self.repo.read(p) or "")[:600]
                m = SIMULATES_DB.search(head)
                if m and not self.holds_database_image(d):
                    n = head[:m.start()].count("\n") + 1
                    out[d] = {"readme": p, "line": n, "text": mask(head.splitlines()[n - 1], 120)}
            self._simulated = out
        return self._simulated

    def simulated_in(self, path):
        """The simulated folder (see simulated_dirs) that holds `path` itself, or None."""
        d = dirname(path)
        s = self.simulated_dirs().get(d)
        return dict(s, folder=d) if s else None

    def match_signal(self, sig, all_deps, skipped=None):
        """First hit of one signal (path [+content] / dep / code) as {file, line, count}, or None. A `simulable` path (a Kubernetes StatefulSet, volume claim
        or CronJob) inside a simulated folder is not a hit: it is appended to `skipped` (when given) as {folder, file, line}."""
        if "path" in sig:
            rx = re.compile(sig["path"])
            first, matched = None, 0
            for p in self.repo.paths:
                if not rx.search(p):
                    continue
                line = None
                if sig.get("content"):
                    t = self.repo.read(p) or ""
                    m = re.search(sig["content"], t)
                    if not m:
                        continue
                    line = t[:m.start()].count("\n") + 1
                sim = self.simulated_in(p) if sig.get("simulable") else None       # only the workloads of a simulated Kubernetes folder (rules/_couplings.json)
                if sim:
                    if skipped is not None:
                        skipped.append({"folder": sim["folder"], "file": p, "line": line})
                    continue
                matched += 1
                first = first or {"file": p, "line": line}
            return dict(first, count=matched) if first else None
        if "dep" in sig:
            return {"file": all_deps[sig["dep"]], "line": None, "count": 1} if sig["dep"] in all_deps else None
        if "code" in sig:
            pool = self.code if sig.get("code_only") else self.text
            pool = [p for p in pool if not SKIP_TEST.search("/" + p)]
            hits = [h for h in self.repo.grep(sig["code"], pool, max_hits=20) if not COMMENT.match(h["text"])][:1]
            if not hits and sig.get("code_only"):          # a Prisma schema is not a code file: its datasource, as the schema reader saw it (the SQLite must-move)
                hits = [h for h in self.prisma_sqlite() if re.search(sig["code"], h["text"])][:1]
            return {"file": hits[0]["file"], "line": hits[0]["line"], "count": 1} if hits else None
        return None

    def couplings(self, table, provenance_ids, all_deps):
        """What a landing would lose or have to move: generic entries + those of every detected provenance."""
        out, seen = [], set()
        self.simulated_skips = {}
        for rid in ["generic"] + sorted(provenance_ids):
            for c in table.get(rid, []):
                skipped = []
                hit = self.match_signal(c, all_deps, skipped)
                for s in skipped[:1]:
                    self.simulated_skips.setdefault(s["folder"], {}).setdefault(c["what"], {"what": c["what"], "file": s["file"], "line": s["line"]})
                if hit and c["what"] not in seen:
                    seen.add(c["what"])
                    out.append({"rule": rid, "kind": c["kind"], "what": c["what"], "target": c["target"], "effort": c["effort"],
                                "file": hit["file"], "line": hit["line"]})
        return out

    def simulated(self):
        """What the survey did NOT count as data to migrate, and why: [{"folder", "readme", "line", "text", "skipped": [{"what", "file", "line"}]}]
        (after couplings()): the simulated folders that held a StatefulSet, a volume claim, a CronJob…"""
        sim = self.simulated_dirs()
        return [{"folder": d, "readme": sim[d]["readme"], "line": sim[d]["line"], "text": sim[d]["text"],
                 "skipped": sorted(s.values(), key=lambda x: (x["file"], x["what"]))} for d, s in sorted(self.simulated_skips.items())]

    def detect(self, rules, all_deps):
        out = []
        for rid, r in rules.items():
            ev = []
            for sig in r.get("detect", []):
                if "path" in sig:
                    rx = re.compile(sig["path"])
                    matched = 0
                    for p in self.repo.paths:
                        if rx.search(p):
                            if sig.get("content"):
                                t = self.repo.read(p) or ""
                                m = re.search(sig["content"], t)
                                if not m:
                                    continue
                                line = t[:m.start()].count("\n") + 1
                            else:
                                line = None
                            matched += 1
                            if matched == 1:
                                first = {"file": p, "line": line, "signal": sig.get("signal", p), "strength": sig["strength"]}
                    if matched:
                        if sig.get("strong_if_count") and matched >= sig["strong_if_count"]:
                            first["strength"] = "strong"
                        if matched > 1:
                            first["signal"] += " (%d files)" % matched
                        ev.append(first)
                elif "dep" in sig:
                    if sig["dep"] in all_deps:
                        ev.append({"file": all_deps[sig["dep"]], "line": None, "signal": "dependency " + sig["dep"], "strength": sig["strength"]})
                elif "code" in sig:
                    pool = self.code if sig.get("code_only") else self.text
                    hits = [h for h in self.repo.grep(sig["code"], pool, max_hits=20) if not COMMENT.match(h["text"])][:1]
                    if hits:
                        ev.append({"file": hits[0]["file"], "line": hits[0]["line"], "signal": sig.get("signal", "code"), "strength": sig["strength"]})
            strong = [e for e in ev if e["strength"] == "strong"]
            weak = [e for e in ev if e["strength"] == "weak"]
            if strong or len(weak) >= r.get("min_weak", 2):
                out.append({"id": rid, "name": r["name"], "coverage": r["coverage"], "priority": r.get("priority", 0),
                            "confidence": "strong" if strong else "weak", "evidence": ev})
        comp = self.compose()
        for x in out:
            if x["id"] == "docker-compose" and comp and comp.get("dev_reasons"):
                x["confidence"] = "weak"          # a dev/demo compose describes no production: never primary over a real manifest
                x["dev_only"] = comp["dev_reasons"]
        # strong before weak, then the rule's priority (platform manifests above a compose), then the id (deterministic)
        out.sort(key=lambda x: (x["confidence"] != "strong", -x["priority"], x["id"]))
        return out


BROWSER_ASSET = re.compile(r"/(static|public|views|assets|www|wwwroot|vendor)/.*\.js$|(jquery|socket\.io|angular|bootstrap|lodash)[^/]*\.js$")
COMMENT = re.compile(r"^(#|//|\*|/\*|--|\d+\.\s)")
SKIP_TEST = re.compile(r"/(tests?|spec|__tests__|e2e|examples?|fixtures)/")
PLAN_TOKEN = re.compile(r"\A[A-Za-z0-9_.-]{1,40}\Z")
DUMP_RX = re.compile(r"\.(sql|dump|bak|backup|sqlite3?|db|pgdump|dmp)(?:\.(?:gz|bz2|xz|zst|zip))?\Z", re.I)
DUMP_SKIP = re.compile(r"(^|/)(migrations?|migrate|alembic|node_modules|vendor|\.git|supabase|fixtures?|tests?|__tests__|spec)/|(^|/)Thumbs\.db\Z", re.I)
EXAMPLE_RX = re.compile(r"(?<![a-z])(examples?|samples?|demos?|tutorials?|starters?|boilerplates?|quick-?starts?|getting[ _-]started|playground|hello[ _-]world)(?![a-z])", re.I)


def main(argv):
    ap = argparse.ArgumentParser(description="internal: called by scan.sh")
    ap.add_argument("--top", required=True)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    sc = Scanner(a.top, a.prefix)
    rules = load_rules()
    roots = sc.roots()
    comp = sc.compose()
    cnames = ({s["name"] for s in comp["services"]}, {s["name"] for s in comp["services"] if s["image"]}) if comp else None
    facts = [sc.root_facts(r, k, cnames) for r, k in sorted(roots.items())]
    all_deps = {}
    for f in facts:
        dd = sc.deps(f["path"])
        for k in list(dd["node"].keys()) + list(dd["node_dev"].keys()):
            all_deps.setdefault(k, sc._j(f["path"], "package.json"))
        for k in dd["python"]:
            all_deps.setdefault(k, sc._j(f["path"], "requirements.txt"))
        for k in dd["php"]:
            all_deps.setdefault(k, sc._j(f["path"], "composer.json"))
    prov = sc.detect(rules, all_deps)
    datastores = {}
    for kind, s in services().items():
        hits = []
        for rx in s.get("code_hints", []):
            hits += [h for h in sc.repo.grep(rx, sc.code, max_hits=6) if not COMMENT.match(h["text"])]
        if hits:
            datastores[kind] = hits[:3]
    result = {
        "tool": "scan.sh", "version": VERSION, "repo": sc.git_info(), "secrets_hygiene": sc.hygiene(),
        "provenances": prov, "primary_provenance": prov[0]["id"] if prov else None,     # never skipped for a weaker 'full' one (detect-only → report only)
        "roots": facts, "compose": comp, "procfile": sc.procfile(), "app_json": sc.manifest_json("app.json"),
        "scalingo_json": sc.manifest_json("scalingo.json"), "vercel_json": sc.vercel_json(), "vm": sc.vm_files(),
        "supabase": sc.supabase_counters() if any(p["id"] == "supabase" for p in prov) else None,
        "datastores_in_code": datastores,
        "couplings": sc.couplings(load_json(os.path.join(RULES_DIR, "_couplings.json")), [p["id"] for p in prov], all_deps),
        "simulated": sc.simulated(),
        "sdk_usage": sc.sdk_usage(rules),
        "platform_texts": sc.platform_texts(),
        "security": sc.security(),
        "auth_mechanism": sc.auth_mechanism(all_deps),
        "poc_branch": sc.poc_branch(),
        "declared": sc.declared(),
        "ci": [p for p in sc.repo.paths if re.match(r"^(\.github/workflows/.*\.ya?ml|\.gitlab-ci\.yml)$", p)][:6],
        "notes": sc.notes,
    }
    js = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(js + "\n")
    if a.json:
        print(js)
    else:
        print(summary(result))
        if a.out:
            print("json: " + a.out)
    return 0


def summary(r):
    g = r["repo"]
    L = ["# scan (committed files only) — %s" % os.path.basename(g["toplevel"]) + (("/" + g["subfolder"]) if g["subfolder"] else "")]
    L.append("branch %s · HEAD %s · %d committed files · %d uncommitted change(s) (not deployed)" % (
        g["branch"], head_text(g["head"]), g["committed_files"], g["uncommitted_changes"]))
    if r["provenances"]:
        L.append("provenance: " + ", ".join("%s%s (%s, %s)" % (p["id"], "*" if p["id"] == r["primary_provenance"] else "", p["confidence"], p["coverage"]) for p in r["provenances"])
                 + "   (* = primary: drives the plan)")
    else:
        L.append("provenance: none recognised (plain repository)")
    for f in r["roots"]:
        v = ", ".join("%s=%s" % (k, (x["value"] if isinstance(x, dict) else x)) for k, x in f["versions"].items())
        L.append("- %s: %s%s%s%s" % (f["path"], f["runtime"] or "?", (" [" + ", ".join(f["framework"][:5]) + "]") if f["framework"] else "",
                                     (" versions: " + v) if v else "", " http=no" if f["http_server"] is False else ""))
        for m_ in f.get("missing_requires", []):
            L.append("    LATENT BUG: %s used without require('%s') (%s:%d) — the route throws, GET answers 500" % (m_["module"], m_["module"], m_["file"], m_["line"]))
        if f["service_hosts"]:
            L.append("    hard-coded service hosts: " + ", ".join("%s (%s:%d)" % (h["service"], h["file"], h["line"]) for h in f["service_hosts"][:4]))
    if r["compose"]:
        L.append("compose %s: %s" % (r["compose"]["file"], ", ".join(s["name"] + ("(" + (s["image"] or "build:" + str(s["build"])) + ")") for s in r["compose"]["services"])))
    if r["procfile"]:
        L.append("Procfile: " + ", ".join(("#" if e["commented"] else "") + e["type"] for e in r["procfile"]["entries"]))
    for f_ in r.get("security") or []:
        L.append("SECURITY (source app, reported only): %s (%s:%d)" % (f_["message"], f_["file"], f_["line"]))
    h = r["secrets_hygiene"]
    if h["tracked_env_files"]:
        L.append("WARNING tracked env files: " + ", ".join("%s (%d names)" % (untrusted(t["file"], 120), len(t["names"])) for t in h["tracked_env_files"]))
    if h["untracked_env_files"]:
        L.append("untracked env files (content not read): " + ", ".join(untrusted(x, 120) for x in h["untracked_env_files"]))
    if h["hardcoded_credentials"]:
        L.append("WARNING hard-coded credentials (masked): " + ", ".join("%s:%d" % (x["file"], x["line"]) for x in h["hardcoded_credentials"][:5]))
    if h["key_like_strings"]:
        L.append("WARNING key-like strings (masked): " + ", ".join("%s:%d" % (x["file"], x["line"]) for x in h["key_like_strings"][:5]))
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
