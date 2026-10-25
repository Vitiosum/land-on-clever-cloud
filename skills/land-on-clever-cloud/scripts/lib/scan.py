"""scan.py — read-only survey of the COMMITTED tree (HEAD), as `clever deploy` will see it.

Called by scripts/scan.sh (which performs the git/$HOME guards). Never reads an untracked
file's content, never calls the network, never prints a secret value.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RULES_DIR, Repo, head_text, load_json, load_rules, mask, run, services, untrusted, yaml_lite, SECRETISH_NAME  # noqa: E402

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
PY_FRAMEWORKS = ["django", "flask", "fastapi", "starlette", "gunicorn", "uvicorn", "daphne", "celery", "channels",
                 "redis", "psycopg", "psycopg2", "psycopg2-binary", "dj-database-url", "whitenoise", "sqlalchemy",
                 "pymongo", "boto3", "supabase"]


def dirname(p):
    d = os.path.dirname(p)
    return d if d else "."


def in_dir(path, d):
    return d == "." or path.startswith(d + "/")


class Scanner:
    def __init__(self, top, prefix):
        self.repo = Repo(top, prefix)
        self.top = top
        self.prefix = prefix
        self.notes = []
        tp = self.repo.text_paths()
        self.repo.preload(tp)
        self.text = tp
        self.code = [p for p in tp if CODE_EXT.search(p)]

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
                clever_aliases = [a.get("alias") for a in cj.get("apps", [])]
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
                t = self.repo.read(p) or ""
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
                t = self.repo.read(p) or ""
                templates.append({"file": p, "names": sorted(set(re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=", t)))})
        not_test = [p for p in self.text if not SKIP_TEST.search("/" + p) and not re.search(r"(_test\.go|\.test\.[jt]sx?|\.spec\.[jt]sx?|\.d\.ts)$|generated", p)]
        key_like = self.repo.grep(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(sk|rk)_live_[A-Za-z0-9]{8,}|\bsb_secret_|\bAKIA[A-Z0-9]{12,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
            r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|base64:[A-Za-z0-9+/]{40,}={0,2}", paths=not_test, max_hits=10)
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
            fx["framework"] += [f for f in NODE_FRAMEWORKS if f in d["node"] or f in d["node_dev"]]
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
        # latent bugs that fail the deploy: Node core modules used without being required/imported
        fx["missing_requires"] = []
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
                    fx["missing_requires"].append({"file": p, "line": line, "module": mod, "text": mask(t.splitlines()[line - 1])})
        # log markers & routes (for prove suggestions)
        fx["log_markers"] = [h for h in self.repo.grep(r"(Console\.WriteLine|console\.log|print|logger\.info|log\.Printf)\(\s*\$?[\"'f]?[\"']?(Processing|Connected|Received|Listening|Started)", code, max_hits=4)]
        fx["routes"] = [h for h in self.repo.grep(r"@app\.route\(|\bpath\(\s*['\"]|app\.(get|post)\(\s*['\"]/|router\.(get|post)\(|HandleFunc\(\"/", code, max_hits=12)
                        if not re.match(r"^(\d+\.|#|//|\*)", h["text"])][:8]
        for p in files:
            if p.endswith(".html") and ("templates/" in p or "views/" in p or os.path.basename(p) == "index.html"):
                t = self.repo.read(p) or ""
                m = re.search(r"<title>([^<{]{3,80})</title>", t)
                if m:
                    fx["title"] = {"text": m.group(1).strip(), "file": p}
                    break
        return fx

    def django_facts(self, root, files):
        out = {}
        for p in files:
            if not p.endswith("settings.py") and "/settings/" not in p:
                continue
            t = self.repo.read(p) or ""
            for n, line in enumerate(t.splitlines(), 1):
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

    def security(self):
        try:
            pats = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "rules", "_security.json")))["patterns"]
        except (OSError, ValueError):
            return []
        found = []
        for pt in pats:
            rx = re.compile(pt["regex"])
            for p in self.code:
                t = self.repo.read(p) or ""
                m = rx.search(t)
                if not m:
                    continue
                if pt.get("absent_in_file") and re.search(pt["absent_in_file"], t):
                    continue
                line = t[:m.start()].count("\n") + 1
                msg = pt["message"] % m.group(pt["group_name"]) if pt.get("group_name") else pt["message"]
                found.append({"id": pt["id"], "file": p, "line": line, "message": msg,
                              "name": m.group(pt["group_name"]) if pt.get("group_name") else None})
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
    def match_signal(self, sig, all_deps):
        """First hit of one signal (path [+content] / dep / code) as {file, line, count}, or None."""
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
                matched += 1
                first = first or {"file": p, "line": line}
            return dict(first, count=matched) if first else None
        if "dep" in sig:
            return {"file": all_deps[sig["dep"]], "line": None, "count": 1} if sig["dep"] in all_deps else None
        if "code" in sig:
            pool = self.code if sig.get("code_only") else self.text
            pool = [p for p in pool if not SKIP_TEST.search("/" + p)]
            hits = [h for h in self.repo.grep(sig["code"], pool, max_hits=20) if not COMMENT.match(h["text"])][:1]
            return {"file": hits[0]["file"], "line": hits[0]["line"], "count": 1} if hits else None
        return None

    def couplings(self, table, provenance_ids, all_deps):
        """What a landing would lose or have to move: generic entries + those of every detected provenance."""
        out, seen = [], set()
        for rid in ["generic"] + sorted(provenance_ids):
            for c in table.get(rid, []):
                hit = self.match_signal(c, all_deps)
                if hit and c["what"] not in seen:
                    seen.add(c["what"])
                    out.append({"rule": rid, "kind": c["kind"], "what": c["what"], "target": c["target"], "effort": c["effort"],
                                "file": hit["file"], "line": hit["line"]})
        return out

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
        "sdk_usage": sc.sdk_usage(rules),
        "platform_texts": sc.platform_texts(),
        "security": sc.security(),
        "auth_mechanism": sc.auth_mechanism(all_deps),
        "poc_branch": sc.poc_branch(),
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
