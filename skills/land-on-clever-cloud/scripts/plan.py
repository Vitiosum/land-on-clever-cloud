#!/usr/bin/env python3
"""plan.py — from a scan to a component-by-component landing plan, filtered by zone.

Usage:
  python3 -I plan.py --scan SCAN.json [--zone par|parhds|rbxhds|grahds|...] [--health no|yes|unsure]
                  [--zone-reason TEXT] [--level A|B|C] [--allow-dev-plan] [--no-live] [--no-price]
                  [--cache-dir DIR] [--name PREFIX] [--json] [--out PLAN.json] [--report REPORT.md]

  --scan          output of scan.sh --out
  --health        answer to the first question ("health data?"): yes or unsure → parhds by default
                  (moving zones later is a migration); no → par
  --zone          explicit zone; anything but par/parhds needs --zone-reason (Paris first)
  --level         Supabase only: A app moves / BaaS stays (default), B data moves, C full exit
  --storage-key-file FILE  Supabase level C: the 600 file holding the project's secret key, written by
                  the developer; without it, private buckets make the files a BLOCKING point
  --allow-dev-plan  allow the free DEV database plans for a throwaway trial: UNENCRYPTED,
                  never in HDS, flagged in the Vision ("would need a migration later")
  --no-live       do not call `clever addon providers show`: use the dated snapshot (labelled)
  --no-price      do not read the live price grid
  --cache-dir     where live catalog answers are cached, 24 h (default: $XDG_CACHE_HOME or ~/.cache; refused inside a git tree)
  --name PREFIX   resource prefix (default $LOC_NAME_PREFIX, else the repo folder name):
                  apps and add-ons are named <prefix>-poc-<component>
  --two-org IDS   HDS only: place these components (services absent from the HDS zone) in a second,
                  non-HDS organisation in par, with the boundary condition shown (internal doctrine,
                  to be confirmed by Clever Cloud). AT RISK services (Keycloak) also need --legal-validated
  --replan        replace a FROZEN plan: once the POC code is committed on clever-poc, an existing
                  --out file stays the reference (a scan of clever-poc sees the POC's own changes) and
                  is printed as is; only a developer's request for a new plan justifies --replan

Output: the Vision (one screen, markdown) on stdout, or the full JSON with --json; --out
writes the JSON (components, verdict, evidence labels, provision spec for provision.sh,
price quote, prove suggestions). Deterministic for a given scan + catalog + grid.
Exit 0 on success, 2 on usage error.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from common import (Catalog, CONTROL_RX, ENV_NAME_RX, HDS_ZONES, clean_text, head_text, is_hds, load_rules, marker_text, md_code, runtimes, shq,  # noqa: E402
                    TRACKED_STATE_ADVICE, known_plan, record_plan, safe_folder, services, slugify, tracked_state, untrusted, zones_snapshot)
import price as pricemod  # noqa: E402

EFFORT_ORDER = ["none", "S", "M", "L", "XL"]
TARGETS = ("native", "addon", "docker", "cke", "keep-saas", "rewrite", "drop", "blocked")


def emax(*vals):
    vals = [v for v in vals if v]
    return max(vals, key=EFFORT_ORDER.index) if vals else "none"


def trunc(text, n):
    """Cut on a word boundary with an ellipsis, never leaving an unbalanced bracket."""
    t = str(text)
    if len(t) <= n:
        return t
    cut = t[: n - 1]
    if " " in cut[n // 2:]:
        cut = cut[: cut.rfind(" ")]
    for o, c in ("()", "[]", "{}"):
        while cut.count(o) > cut.count(c) and o in cut:
            cut = cut[: cut.rfind(o)].rstrip()
    return cut.rstrip(" ,;:—-(") + "…"


def server_target(cmd):
    """module:app served by gunicorn / uvicorn / daphne / waitress in a start command, or None.
    `gunicorn mydjango.wsgi` (no :callable) means mydjango.wsgi:application."""
    m = re.search(r"\b(?:gunicorn|uvicorn|daphne|waitress-serve|hypercorn)\b((?:\s+(?:-[-\w]+(?:[= ](?!-)[^\s]+)?|[^\s-][^\s]*))*)", cmd or "")
    if not m:
        return None
    args, skip = m.group(1).split(), False
    for i, a_ in enumerate(args):
        if skip:
            skip = False
            continue
        if a_.startswith("-"):
            skip = "=" not in a_ and a_ in ("-b", "--bind", "-w", "--workers", "-k", "--worker-class", "-c", "--config", "--host", "--port", "--log-file", "--log-level", "--timeout", "-t", "--chdir", "--app-dir")
            continue
        if re.fullmatch(r"[A-Za-z_][\w.]*(:[A-Za-z_]\w*(\(\))?)?", a_):
            t_ = a_.replace("()", "")
            return t_ if ":" in t_ else (t_ + ":application" if t_.endswith((".wsgi", ".asgi")) else t_ + ":app")
    return None


def fit_name(base, part, maxlen=63):
    """<prefix>-<part> within Clever's 63 characters, deterministically: the prefix is kept whole
    (deploy.sh recognises the POC's apps by it); a too-long part is cut and given a short hash."""
    full = "%s-%s" % (base, part)
    if len(full) <= maxlen:
        return full
    room = maxlen - len(base) - 1
    h = hashlib.sha256(full.encode()).hexdigest()[:6]
    cut = part[: max(room - 7, 1)].strip("-") or "x"
    return "%s-%s-%s" % (base, cut, h)


CRON_PROOF_COST = ("job run not proven until it shows in the logs; proving it costs 2 extra deploys "
                   "(a temporary */2 schedule on clever-poc, then the original schedule back): 2 builds, say it first")


def utc_offer(schedule, tz):
    """#24: a VM crontab runs in the VM's time zone, cron.json in UTC. For a fixed 'M H * * *'-like
    schedule, the UTC hours in summer and winter for that zone — an offer, the developer decides."""
    m = re.match(r"^(\d{1,2})\s+(\d{1,2})\s+(\S+\s+\S+\s+\S+)$", schedule.strip())
    if not m:
        return None
    try:
        import datetime
        from zoneinfo import ZoneInfo
        z = ZoneInfo(tz)
    except Exception:
        return None
    mi, h, rest = int(m.group(1)), int(m.group(2)), m.group(3)
    out = []
    for month in (7, 1):
        loc = datetime.datetime(2026, month, 15, h, mi, tzinfo=z)
        u = loc.astimezone(datetime.timezone.utc)
        out.append((u.hour, loc.tzname()))
    (hs, ns), (hw, nw) = out
    if hs == hw:
        return "`%s` at %02d:%02d %s = `%d %d %s` in UTC (cron.json is UTC)" % (schedule, h, mi, tz, mi, hs, rest)
    return ("`%s` at %02d:%02d %s is %02d:%02d UTC in summer (%s) and %02d:%02d UTC in winter (%s): cron.json is UTC — "
            "choose `%d %d %s` (summer) or `%d %d %s` (winter), a fixed UTC hour shifts by 1 h twice a year" % (
                schedule, h, mi, tz, hs, mi, ns, hw, mi, nw, mi, hs, rest, mi, hw, rest))


def prove_command(cid, pr):
    """The prove.sh line for one component. Every value is shell-quoted (shq): the <title> and the log
    line come from the scanned repository. Only <domain> and <web alias> are placeholders to replace."""
    if not pr or not (pr.get("expect") or pr.get("checks") or pr.get("log_expect") or pr.get("socketio_event") or pr.get("upload_form")
                      or pr.get("build_expect") or pr.get("cron_expect")):
        return None
    args = ["prove.sh", "https://<domain>"]
    if pr.get("expect"):
        args += ["--expect", shq(pr["expect"])]
    for ck in pr.get("checks", []):
        args += ["--check", shq("%s %s %s%s" % (ck["method"], ck["path"], ck["code"], (" " + ck["marker"]) if ck.get("marker") else ""))]
        if ck.get("data"):
            args += ["--data", shq(ck["data"])]
    if pr.get("upload_form"):
        uf = pr["upload_form"]
        args += ["--upload-check", shq("%s <a small test .png>" % uf["route"]), "--upload-field", shq(uf["field"])]
        for f in uf.get("fields") or []:
            args += ["--form-field", shq(f)]
        args += ["--upload-form", shq(uf.get("page") or "/")]
    if pr.get("socketio_event"):
        args += ["--socketio-event", shq(pr["socketio_event"])]
    if pr.get("build_expect"):
        args += ["--alias", shq(cid), "--log-since", "60m", "--build-expect", shq(pr["build_expect"])]
    alias = re.sub(r"[^a-z0-9-]+", "-", str(pr.get("alias") or cid).lower())
    if pr.get("auth_check"):
        ac = pr["auth_check"]
        # #15: the call WITH the secret is --check-auth (plain --check never sends it: 401); the script reads
        # the value from the app itself (--alias), never the agent
        args += ["--check-auth", shq("%s %s %s" % (ac["method"], ac["path"], ac["code"])), "--auth-env", shq(ac["auth_env"]), "--alias", shq(alias)]
    if pr.get("cron_expect"):
        if not pr.get("auth_check"):
            args += ["--alias", shq(alias)]
        args += ["--cron-expect", shq(pr["cron_expect"]), "--cron-wait", "180"]
    if pr.get("log_expect"):
        args += ["--alias", shq(cid), "--log-expect", shq(pr["log_expect"])]
    return " ".join(args)


def in_dir(path, d):
    return d in (".", "", None) or path.startswith(d + "/")


def ref(f, line=None):
    return "%s:%s" % (f, line) if line else f


def vtuple3(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", v)[:2])
    except ValueError:
        return ()


class Planner:
    def __init__(self, scan, args):
        self.s = scan
        self.a = args
        self.rules = load_rules()
        self.svc = services()
        self.rt = runtimes()
        self.cat = Catalog(cache_dir=args.cache_dir, live=not args.no_live, repo_top=(scan.get("repo") or {}).get("toplevel"))
        self.zone = args.zone
        self.hds = is_hds(self.zone)
        self.components = []
        self.assumed = []
        self.notes = []
        self.grid = None
        self.roots = {r["path"]: r for r in scan.get("roots", [])}
        self.two_org = set(x for x in (args.two_org or "").split(",") if x)
        self.base = slugify(args.name or os.path.basename(scan["repo"]["toplevel"]), 40) + "-poc"
        self.tradeoffs = []      # security trade-offs the plan itself introduces (shown in the Security line)
        self.open_risks = []     # source weaknesses deliberately kept (no mechanism to fix them in a POC)
        self.kept = []           # files kept as is, with why it is harmless
        self.optional_changes = []   # proposed, not counted, not applied without a yes
        self.watch = json.load(open(os.path.join(os.path.dirname(HERE), "rules", "_watch.json")))["entries"]
        self.watch_hits = []
        self.handled_couplings = set()   # couplings the plan maps itself (not repeated on the "must move" line)

    # ---------------------------------------------------------------- helpers
    def root(self, path):
        path = path or "."
        return self.roots.get(path) or self.roots.get(path.rstrip("/")) or None

    def add(self, **c):
        # every id comes from the scanned repo (compose service names, Procfile types…): untrusted
        raw = str(c["id"])
        c["id"] = slugify(raw)
        if c["id"] != raw:
            c.setdefault("assumed", []).append("component name %r was not a safe name: renamed %s" % (clean_text(raw, 60), c["id"]))
        c["today"] = clean_text(c.get("today", ""), 120)
        c.setdefault("changes", [])
        c.setdefault("config", {})
        c.setdefault("evidence", [])
        c.setdefault("assumed", [])
        c.setdefault("blockers", [])        # unverified points that make the POC fail as written: no "ready" while one is open
        c.setdefault("required_inputs", [])  # values the developer must provide (cannot be generated): counted in the verdict
        c.setdefault("optional", False)
        c.setdefault("org", "main")
        c.setdefault("options", [])
        assert c["target"] in TARGETS, c["target"]
        self.components.append(c)
        return c

    def load_grid(self):
        if self.a.no_price or self.grid is not None:
            return self.grid
        try:
            self.grid = pricemod.load(self.zone)
        except Exception as exc:  # noqa: BLE001
            self.notes.append("price grid unavailable (%s): no price quoted" % exc)
            self.grid = {}
        return self.grid

    def pick_plan(self, kind):
        """Smallest dedicated plan with encryption at rest, read from the live catalog + live grid."""
        s = self.svc[kind]
        pid = s.get("provider")
        if not s.get("dedicated"):
            return s.get("default_plan"), False, "plan: %s (no encryption option on this service)" % s.get("default_plan")
        if self.a.allow_dev_plan and not self.hds and any(p.get("slug") == "dev" for p in (self.cat.provider(pid) or {}).get("plans", [])):
            return "dev", False, "DEV plan (throwaway trial): UNENCRYPTED, no backups — would need a migration later"
        plans = self.cat.dedicated_plans(pid)
        grid = (self.load_grid() or {}).get(s.get("price_source") or "", {})
        enc_plans = [p for p in plans if p["encryption"]]
        priced = [(grid.get(p["slug"].lower()), p) for p in enc_plans if grid.get(p["slug"].lower()) is not None]
        if priced:
            priced.sort(key=lambda x: x[0])
            p = priced[0][1]
            bk = p["features"].get("backups")
            return p["slug"], True, "smallest dedicated plan with encryption (catalog + grid read today)" + ((", backups: " + bk) if bk else "")
        return s.get("default_plan"), bool(s.get("encryption")), "default dedicated plan (catalog or grid not readable: confirm with clever addon providers show %s)" % pid

    # ---------------------------------------------------------------- add-ons and zone filtering
    def addon_component(self, kind, cid, today, evidence, optional=False, reason=""):
        s = self.svc[kind]
        pid = s.get("provider")
        avail = self.cat.available(pid, self.zone)
        lab = self.cat.label(pid)
        ev = list(evidence) + ["%s: %s %s in %s" % (lab, pid, "available" if avail else ("NOT available" if avail is False else "availability unknown"), self.zone)]
        if avail is False and self.hds and cid in self.two_org and s.get("two_org"):
            tw = s["two_org"]
            if tw["risk"] == "at-risk" and not self.a.legal_validated:
                self.notes.append("--two-org %s refused: AT RISK service, needs written validation by Clever's legal team and the customer's DPO (--legal-validated)" % cid)
            elif self.cat.available(pid, "par"):
                plan, enc, why = self.pick_plan(kind)
                c = self.add(id=cid, kind="datastore", today=today, target="addon", effort="S",
                             detail="%s %s in a 2nd non-HDS org (par)" % (s["label"], plan), evidence=ev + ["[read] boundary: " + tw["boundary"]],
                             optional=optional, service=kind, provider=pid, plan=plan, encryption=enc, plan_reason=why, org="secondary",
                             zone_override="par", price_item="%s:%s" % (s["price_source"], plan) if s.get("price_source") else None)
                c["assumed"].append("two-organisation boundary for %s (internal presales doctrine, to be confirmed by Clever Cloud; validation: Clever legal + customer DPO): %s" % (cid, tw["boundary"]))
                return c
        if avail is False:
            fb = s.get("fallback_absent") or {"target": "blocked", "effort": "XL", "detail": "%s is not available in %s" % (s["label"], self.zone)}
            c = self.add(id=cid, kind="datastore", today=today, target=fb["target"], effort=fb["effort"],
                         detail=fb["detail"], evidence=ev, optional=optional, service=kind)
            tw = s.get("two_org")
            if self.hds and tw:
                other_ok = self.cat.available(pid, "par")
                opt = {"option": "second non-HDS organisation (zone par)", "provider": pid, "available_in_par": other_ok,
                       "boundary": tw["boundary"], "risk": tw["risk"],
                       "status": "internal presales doctrine, to be confirmed by Clever Cloud; validation by Clever's legal team and the customer's DPO",
                       "never": "this skill does not qualify compliance"}
                if tw["risk"] == "at-risk":
                    opt["default_instead"] = fb["detail"]
                c["options"].append(opt)
            return c
        plan, enc, why = self.pick_plan(kind)
        detail = "%s %s%s" % (s["label"], plan, ", encrypted" if enc else (", UNENCRYPTED" if s.get("dedicated") else ""))
        c = self.add(id=cid, kind="datastore", today=today, target="addon", effort="none", detail=detail, evidence=ev,
                     optional=optional, service=kind, provider=pid, plan=plan, encryption=enc, plan_reason=why,
                     price_item=("cellar:storage" if s.get("price_source") == "cellar" else "%s:%s" % (s["price_source"], plan)) if s.get("price_source") else None)
        if plan == "dev":
            c["assumed"].append("DEV plan: unencrypted and without backups — moving to an encrypted plan later is a new add-on + data migration")
        if reason:
            c["detail"] += " (" + reason + ")"
        return c

    def wiring(self, kind, file, root):
        w = self.svc.get(kind, {}).get("wiring") or {}
        pk = ((root or {}).get("dotnet") or {}).get("packages", {})
        if file.endswith((".cs", ".fs", ".vb")):
            for key, txt in w.items():
                if key.startswith("dotnet:") and key.split(":", 1)[1] in pk:
                    return txt
        if file.endswith(".py") and "python" in w:
            return w["python"]
        if file.endswith((".js", ".mjs", ".cjs", ".ts")) and "node" in w:
            return w["node"]
        return w.get("default") or self.svc.get(kind, {}).get("env_hint", "")

    def kind_for_image(self, image):
        for kind, s in self.svc.items():
            for rx in s.get("images", []):
                if re.search(rx, image):
                    return kind
        return None

    # ---------------------------------------------------------------- native app components
    def app_component(self, cid, root_path, today, role, evidence, extra_changes=None, start_hint=None):
        r = self.root(root_path)
        if r is None:
            return self.add(id=cid, kind=role, today=today, target="docker", effort="M",
                            detail="no recognisable runtime in %s: Docker app" % root_path, evidence=evidence)
        rt = r["runtime"]
        changes = list(extra_changes or [])
        cfg = {}
        assumed = []
        blockers = []
        ev = list(evidence)
        target, base_effort = "native", "none"
        folder = None if r["path"] == "." else r["path"]
        if folder:
            if safe_folder(folder) is None:
                return self.add(id=cid, kind=role, today=today, target="blocked", effort="XL",
                                detail="unsafe folder name in the repository: rename it first", evidence=evidence)
            cfg["APP_FOLDER"] = folder
        detail = ""
        secrets = []
        prove = {"expect": None, "checks": [], "log_expect": None}
        if r.get("title"):
            prove["expect"] = marker_text(r["title"]["text"])
        # ---- runtime-specific
        if rt == "python":
            py = r.get("python") or {}
            mods = list(py.get("module_candidates") or [])
            hint = server_target(start_hint or "")
            if hint:
                mods.insert(0, {"module": hint, "file": "start command", "kind": "start-command"})
            mod = next((m for m in mods if m["kind"] in ("start-command", "django-wsgi")), mods[0] if mods else None)
            if mod and not re.fullmatch(r"^[A-Za-z0-9_.]+:[A-Za-z_][A-Za-z0-9_]*$", mod["module"]):
                blockers.append("WSGI module path is not a plain Python dotted name: set CC_PYTHON_MODULE by hand")
                mod = None
            if mod:
                cfg["CC_PYTHON_MODULE"] = mod["module"]
                ev.append("[read] " + ref(mod["file"]))
            else:
                blockers.append("no WSGI/ASGI module found (Flask/FastAPI app object, Django wsgi.py, or a gunicorn/uvicorn target): set CC_PYTHON_MODULE by hand")
            if "gunicorn" in py.get("deps", []):
                cfg["CC_PYTHON_BACKEND"] = "gunicorn"
            elif "uvicorn" in py.get("deps", []):
                cfg["CC_PYTHON_BACKEND"] = "uvicorn"
            pv = r["versions"].get("python")
            if not pv:
                # #29: a VM names its interpreter in the unit (ExecStart=/usr/bin/python3.11 …, venv/bin/python3.11)
                vm_ = self.s.get("vm") or {}
                for u in (vm_.get("systemd") or []) + (vm_.get("supervisor") or []):
                    mv = re.search(r"python(3\.\d{1,2})\b", str(u.get("exec") or u.get("command") or ""))
                    if mv:
                        pv = {"file": u.get("file", "the VM unit"), "value": mv.group(1)}
                        break
            if not pv:
                assumed.append("Python version not pinned (no .python-version, runtime.txt, requires-python or pythonX.Y in a unit): Clever takes "
                               "its default (3.13 observed 2026-10-25) — run `python3 --version` where the app runs today and set CC_PYTHON_VERSION")
            acc = sorted((a for a in self.rt["runtimes"]["python"]["accepted_versions"] if re.fullmatch(r"3\.\d+", a)), key=vtuple)
            if pv:
                want = pv["value"]
                mmv = re.search(r"(\d+)\.(\d+)", want)
                mm = "%s.%s" % mmv.groups() if mmv else None
                if mm in acc:
                    cfg["CC_PYTHON_VERSION"] = mm
                elif mm:
                    # the CLOSEST accepted version: the smallest one at or above the declared one, else the highest
                    up = [a for a in acc if vtuple(a) >= vtuple(mm)]
                    best = up[0] if up else acc[-1]
                    cfg["CC_PYTHON_VERSION"] = best
                    ev.append("[read] %s says %s; closest accepted on Clever: %s [read doc %s]" % (pv["file"], want, best, self.rt["read_date"]))
                    assumed.append("Python %s on Clever (closest accepted to %s, runtime page read %s%s)" % (
                        best, want, self.rt["read_date"], "; EOL" if best in self.rt["runtimes"]["python"].get("eol", []) else ""))
            if py.get("uv_lock") and not py.get("requirements_txt"):
                changes.append({"file": (folder + "/" if folder else "") + "requirements.txt", "line": None, "effort": "S",
                                "short": "add requirements.txt (uv export --format requirements-txt --no-hashes)",
                                "what": "uv.lock and no requirements.txt: the Python runtime installs requirements.txt (legacy mode, the one CC_PYTHON_MODULE/BACKEND/MANAGE_TASKS apply to); "
                                        "generate it with `uv export --format requirements-txt --no-hashes > requirements.txt` and commit it. The native uv mode "
                                        "(CC_PYTHON_UV_RUN_COMMAND, listen on 0.0.0.0:$PORT; MODULE/BACKEND ignored) is documented [read doc 2026-10-25] but not observed yet"})
            for sl in [x for x in self.s["secrets_hygiene"].get("secret_literals", []) if in_dir(x["file"], r["path"]) and x["file"].endswith(".py")][:1]:
                nm = "DJANGO_SECRET_KEY" if "DJANGO_SECRET_KEY" in (r.get("env_names") or []) else "SECRET_KEY"
                secrets.append(nm)
                if re.search(r"environ|getenv|env\(", sl["text"]):      # already read from the environment: the literal is only a fallback
                    ev.append("[read] %s: %s is read from the environment with a literal fallback — generated on Clever, the fallback stays unused" % (ref(sl["file"], sl["line"]), nm))
                    continue
                secrets.remove(nm)
                changes.append({"file": sl["file"], "line": sl["line"], "effort": "S", "short": "SECRET_KEY written in the code → read %s from the environment" % nm,
                                "what": "the secret key is a literal in the code (rotate it): read it with os.environ.get(%r, <local dev value>); the plan generates %s on Clever (env-safe.sh, never shown)" % (nm, nm)})
                secrets.append(nm)
            dj = py.get("django")
            if dj:
                tasks = []
                if dj.get("static"):
                    tasks.append("collectstatic --noinput")
                    ev.append("[read] " + ref(dj["static"][0]["file"], dj["static"][0]["line"]) + " static files need collectstatic (Clever does not run it by itself)")
                gate = dj.get("platform_gate")
                if gate:
                    g = gate[0]
                    cfg["APP_ENV"] = "production"
                    ev.append("[read] APP_ENV=production is set by provision BEFORE the first build: CC_PYTHON_MANAGE_TASKS (migrate, collectstatic) runs at build with the production settings")
                    changes.append({"file": g["file"], "line": g["line"], "effort": "S",
                                    "short": "production settings gated on Heroku's DYNO → also switch on APP_ENV=production (set by provision)",
                                    "what": "production settings are switched on by a Heroku-only variable (DYNO): add the explicit switch the plan sets on Clever, "
                                            "e.g. `IS_HEROKU_APP = (\"DYNO\" in os.environ or os.environ.get(\"APP_ENV\") == \"production\") and \"CI\" not in os.environ`; "
                                            "keep Heroku working; "
                                            "(e.g. an env var) so ALLOWED_HOSTS, the database and HTTPS settings apply on Clever — "
                                            "otherwise every request answers 400, which still passes the default health check"})
                https = dj.get("https") or []
                if any("SECURE_SSL_REDIRECT" in x["text"] for x in https) and not any("SECURE_PROXY_SSL_HEADER" in x["text"] for x in https):
                    x = next(x for x in https if "SECURE_SSL_REDIRECT" in x["text"])
                    changes.append({"file": x["file"], "line": x["line"], "effort": "S",
                                    "short": "SECURE_SSL_REDIRECT needs SECURE_PROXY_SSL_HEADER behind Clever's proxy",
                                    "what": "HTTPS redirect relies on the proxy header being trusted (today via gunicorn.conf.py forwarded_allow_ips, which the Clever-managed gunicorn may not read): set SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') or the app redirects in a loop"})
                    ev.append("[observed 2026-10-25] " + self.rt["runtimes"]["python"]["observed"][0].split(": ", 1)[1])
                if any("ssl_require" in x["text"] and "True" in x["text"] for x in dj.get("database", [])):
                    ev.append("[observed 2026-10-25] dj_database_url ssl_require=True works with the PostgreSQL add-on (validation round 1)")
                cfg["_django_tasks"] = tasks
                prove["checks"].append({"method": "GET", "path": "/", "code": 200})
            routes = [x for x in r.get("routes", []) if re.search(r"path\(\s*['\"]([a-z0-9_-]+/)", x["text"])]
            for x in routes[:1]:
                m = re.search(r"path\(\s*['\"]([a-z0-9_-]+/)", x["text"])
                prove["checks"].append({"method": "GET", "path": "/" + m.group(1), "code": 200, "why": "real code path (%s)" % ref(x["file"], x["line"])})
            # H9: a POST check only on a POST route at "/" whose handler reads a form field (its name is the data sent)
            flask_post = [x for x in r.get("routes", []) if "POST" in x["text"] and re.search(r"route\(\s*['\"]/['\"]", x["text"])]
            fields = r.get("form_fields") or []
            if flask_post and fields:
                prove["checks"].append({"method": "POST", "path": "/", "code": 200, "data": "%s=a" % fields[0],
                                        "why": "real code path: the form POST handler, field %s (%s)" % (fields[0], ref(flask_post[0]["file"], flask_post[0]["line"]))})
            self.data_checks(r, prove)
            detail = "Python runtime, %s%s" % (cfg.get("CC_PYTHON_BACKEND", "uwsgi"), (", " + cfg["CC_PYTHON_MODULE"]) if cfg.get("CC_PYTHON_MODULE") else "")
        elif rt == "node":
            nd = r.get("node") or {}
            sc = nd.get("scripts") or {}
            pm = "npm"
            if nd.get("lockfile") == "pnpm-lock.yaml":
                pm = "pnpm"
            elif nd.get("lockfile") == "yarn.lock":
                pm = "yarn"
            if sc.get("build"):
                cfg["CC_POST_BUILD_HOOK"] = "%s run build" % pm
                ev.append("[read] package.json scripts.build: the runtime runs install, not build")
                if nd.get("dev_build_tools"):
                    cfg["CC_NODE_DEV_DEPENDENCIES"] = "install"
            if not nd.get("lockfile"):
                changes.append({"file": (folder + "/" if folder else "") + "package-lock.json", "line": None, "effort": "S",
                                "short": "commit a lockfile", "what": "no lockfile committed: dependencies float at build — generate and commit one"})
            if not nd.get("engines_node"):
                assumed.append("Node version not pinned (%s/package.json engines.node): latest LTS will be used" % (folder or "."))
            if not sc.get("start") and not nd.get("main"):
                changes.append({"file": (folder + "/" if folder else "") + "package.json", "line": None, "effort": "S",
                                "short": "add a start script", "what": "no start script nor main: add one (or CC_RUN_COMMAND)"})
            detail = "Node.js runtime" + (" (%s)" % ("start: " + (sc.get("start") or "node " + str(nd.get("main")))))
            static_fw = set(nd.get("deps") or []) | set(nd.get("dev_build_tools") or [])
            if not sc.get("start") and not nd.get("main") and sc.get("build") and not r.get("http_server") and static_fw & {"vite", "react-scripts", "astro", "@sveltejs/adapter-static", "parcel"}:
                # a static site: Clever's static runtime, built by CC_BUILD_COMMAND
                changes[:] = [c_ for c_ in changes if c_.get("short") != "add a start script"]
                rt = "static"
                cfg.pop("CC_POST_BUILD_HOOK", None)
                cfg.pop("CC_NODE_DEV_DEPENDENCIES", None)
                out = "/build" if "react-scripts" in static_fw else "/dist"
                cfg["CC_BUILD_COMMAND"] = "%s install && %s run build" % (pm, pm)
                cfg["CC_WEBROOT"] = (("/" + folder) if folder else "") + out
                ev.append("[read] build script, no start script, no server: static site (CC_BUILD_COMMAND, CC_WEBROOT) [read doc 2026-10-25]")
                blockers.append("static runtime: the doc does not confirm that Node is available during CC_BUILD_COMMAND — prove one build first (or build on a Node app)")
                detail = "static runtime (built by CC_BUILD_COMMAND, served from %s)" % cfg["CC_WEBROOT"]
        elif rt == "dotnet":
            dn = r.get("dotnet") or {}
            acc = self.rt["runtimes"]["dotnet"]["accepted_versions"]
            for tf in dn.get("target_frameworks", []):
                m = re.match(r"net(\d+)\.(\d+)", tf["value"])
                if m:
                    major = m.group(1)
                    if major not in [a.split(".")[0] for a in acc]:
                        changes.append({"file": tf["file"], "line": tf["line"], "effort": "S",
                                        "short": "retarget %s → net8.0 (not supported)" % tf["value"],
                                        "what": "targets %s, not supported on Clever (accepted: %s [read doc %s]): retarget to net8.0 and re-test" % (
                                            tf["value"], ", ".join(acc), self.rt["read_date"])})
            detail = ".NET runtime"
            if any(c_.get("short", "").startswith("retarget") for c_ in changes):
                df = r.get("dockerfile") or {}
                for fr in df.get("from", []):
                    m = re.match(r"^(mcr\.microsoft\.com/dotnet/(sdk|runtime|aspnet|runtime-deps)):(\d+\.\d+)", fr["image"])
                    if m and m.group(3).split(".")[0] not in [a_.split(".")[0] for a_ in acc]:
                        changes.append({"file": df["file"], "line": fr["line"], "effort": "S",
                                        "short": "FROM %s:%s → 8.0 (so docker-compose keeps working with net8.0)" % (m.group(1).split("/")[-1], m.group(3)),
                                        "what": "the Dockerfile still builds with %s:%s: move it to 8.0 together with the project retarget, or the local compose build breaks" % (m.group(1), m.group(3))})
            cfg["DOTNET_NUGET_SIGNATURE_VERIFICATION"] = "false"
            self.tradeoffs.append("%s: DOTNET_NUGET_SIGNATURE_VERIFICATION=false turns off NuGet signature checks on Clever's build VM — NU3018 reproduced 4/4 there (SDK 8.0.132: the VM's code-signing trust chain, not a defect of this project; a local SDK restores fine). Keep it until the platform is fixed" % (folder or "app"))
            for pkg, info in sorted((dn.get("packages") or {}).items()):
                for wch in self.watch:
                    if wch["ecosystem"] == "nuget" and wch["package"] == pkg and info["version"].startswith(wch["line"] + ".") and vtuple3(info["version"]) <= vtuple3(wch["at_or_below"]):
                        self.watch_hits.append("%s %s (%s:%s): %s" % (pkg, info["version"], info["file"], info["line"], wch["short"]))
                        if wch.get("optional_change"):
                            self.optional_changes.append({"file": info["file"], "line": info["line"], "what": wch["optional_change"]})
        elif rt == "go":
            go = r.get("go") or {}
            mains, mod_ = go.get("main_dirs") or [], go.get("module")
            df = (r.get("dockerfile") or {})
            built = re.search(r"go build[^\n]*?\./(cmd/[\w./-]+)", json.dumps(df)) if df else None
            if "." in mains or not mains:
                detail = "Go runtime (gomod: go install of the module %s)" % (mod_ or "")
                if not mains:
                    blockers.append("Go: no `package main` with `func main` found — nothing to build")
            else:
                pick = (built.group(1).rstrip("/") if built and built.group(1).rstrip("/") in mains else (mains[0] if len(mains) == 1 else None))
                if pick and mod_:
                    cfg["CC_GO_PKG"] = "%s/%s" % (mod_, pick)
                    ev.append("[read] main package %s (no main at the module root): CC_GO_PKG [read doc 2026-10-25]" % pick)
                else:
                    blockers.append("Go: no main at the module root and several candidates (%s): set CC_GO_PKG=<module>/<dir>" % ", ".join(mains[:4]))
                detail = "Go runtime (gomod, CC_GO_PKG %s)" % cfg.get("CC_GO_PKG", "?")
        elif rt == "php":
            ph = r.get("php") or {}
            if ph.get("public_index"):
                cfg["CC_WEBROOT"] = "/public"
                ev.append("[read] public/index.php: CC_WEBROOT=/public (else the repository root is served) [read doc 2026-10-25]")
            want = ph.get("require_php")
            acc = self.rt["runtimes"]["php"].get("accepted_versions", [])
            mmv = re.search(r"(\d+)\.(\d+)", want or "")
            if mmv:
                up = [a for a in acc if vtuple(a) >= vtuple("%s.%s" % mmv.groups())]
                if up:
                    cfg["CC_PHP_VERSION"] = up[0]
                    ev.append("[read] composer.json require.php %s: CC_PHP_VERSION=%s (the default is the latest) [read doc 2026-10-25]" % (want, up[0]))
            if ph.get("laravel"):
                secrets.append("APP_KEY")
                cfg["_secret_formats"] = {"APP_KEY": "laravel"}
                ev.append("[read] artisan + laravel/framework: APP_KEY generated on Clever in Laravel's base64: format")
            detail = "PHP runtime%s" % ((", webroot " + cfg["CC_WEBROOT"]) if cfg.get("CC_WEBROOT") else "")
        elif rt in ("ruby", "rust", "java", "elixir"):
            detail = "%s runtime" % rt
            blockers.append("%s: plan.py has no detailed rules yet for this runtime (build, start, port) — read %s" % (rt, self.rt["runtimes"].get(rt, {}).get("doc", "the runtime page")))
        elif rt == "docker":
            df = r.get("dockerfile") or {}
            target, base_effort = "docker", "S"
            if not (df.get("cmd") or df.get("entrypoint")):
                changes.append({"file": df.get("file", "Dockerfile"), "line": None, "effort": "S", "short": "add CMD", "what": "no CMD/ENTRYPOINT: Clever needs one"})
            ports = [p.split("/")[0] for p in df.get("expose", [])]
            ok_ports = [p for p in ports if re.fullmatch(r"[0-9]{1,5}", p) and 1 <= int(p) <= 65535]
            if len(ok_ports) < len(ports):
                blockers.append("EXPOSE in %s is not a plain port number (%s): set CC_DOCKER_EXPOSED_HTTP_PORT by hand" % (
                    df.get("file", "Dockerfile"), ", ".join(clean_text(p, 20) for p in ports if p not in ok_ports)[:60]))
            ex = [p for p in ok_ports if p != "8080"]
            if not ports:   # no EXPOSE: the port the CMD/ENTRYPOINT (or the compose service) really listens on
                txt = " ".join(str((df.get(k) or {}).get("value", "")) for k in ("cmd", "entrypoint")) + " " + " ".join(start_hint or "" for _ in [0])
                m = re.search(r"(?:--http[= ][^:\s\"']*:|--port[= ]|\s-p\s+|0\.0\.0\.0:)(\d{2,5})\b", txt)
                if m and m.group(1) != "8080" and 1 <= int(m.group(1)) <= 65535:
                    ex = [m.group(1)]
                    ev.append("[read] the start command listens on %s (no EXPOSE)" % m.group(1))
            if ex:
                cfg["CC_DOCKER_EXPOSED_HTTP_PORT"] = ex[0]
            detail = "Docker runtime (no native runtime fits)"
        else:
            target, base_effort, detail = "docker", "M", "unknown stack"
            blockers.append("unknown stack in %s: no runtime recognised" % (folder or "."))
        # ---- latent bugs that would fail the deploy (found statically)
        for m_ in r.get("missing_requires", []):
            changes.append({"file": m_["file"], "line": m_["line"], "effort": "S",
                            "short": "latent bug: `%s` used without require('%s') → 500, health check fails" % (m_["module"], m_["module"]),
                            "what": "`%s.…` is used but `%s` is never required/imported: the route throws a ReferenceError, answers 500, and the deploy's health check (200-499) fails — add `const %s = require('%s')`" % (m_["module"], m_["module"], m_["module"], m_["module"])})
        # ---- listening
        if role == "web" and rt not in ("python", "dotnet", "docker"):
            if r["listen"]["reads_port"]:
                ev.append("[read] reads PORT at " + ref(r["listen"]["reads_port"][0]["file"], r["listen"]["reads_port"][0]["line"]))
            elif r["listen"]["hardcoded_ports"]:
                h = r["listen"]["hardcoded_ports"][0]
                changes.append({"file": h["file"], "line": h["line"], "effort": "S", "short": "read $PORT, bind 0.0.0.0", "what": "hard-coded port: read $PORT (8080) and bind 0.0.0.0"})
        for h in r["listen"].get("localhost_bind", []):
            if not re.search(r"ALLOWED_HOSTS", h["text"]):
                changes.append({"file": h["file"], "line": h["line"], "effort": "S", "short": "bind 0.0.0.0", "what": "binds localhost: bind 0.0.0.0"})
        if role == "worker" and r.get("http_server") is False and rt not in ("docker",):
            main_file = next((x["file"] for x in r.get("log_markers", [])), None) or (folder or ".")
            changes.append({"file": main_file, "line": None, "effort": "M",
                            "short": "add a minimal HTTP endpoint on 8080 (no HTTP server today; C# snippet: reference/dotnet-worker-health.md)" if rt == "dotnet" else "add a minimal HTTP endpoint on 8080 (no HTTP server today)",
                            "what": "background process with no HTTP server: Clever checks that something listens on 8080 before the deploy succeeds — add a minimal health endpoint (e.g. GET / → 200) next to the loop"})
        # ---- hard-coded hosts of compose services
        byfile = {}
        for h in r.get("service_hosts", []):
            byfile.setdefault(h["file"], []).append(h)
        for f, hs in sorted(byfile.items()):
            names = sorted(set(h["service"] for h in hs))
            kinds = [self.compose_kind.get(n) for n in names] if hasattr(self, "compose_kind") else []
            vars_ = [self.wiring(k, f, r) for k in kinds if k]
            creds = [x for x in self.s["secrets_hygiene"]["hardcoded_credentials"] if x["file"] == f]
            changes.append({"file": f, "line": hs[0]["line"], "lines": sorted(set(h["line"] for h in hs)), "effort": "S",
                            "short": "host%s %s hard-coded%s → add-on variables, local fallback kept" % ("s" if len(names) > 1 else "", "/".join("'%s'" % n for n in names), " (+ credentials, masked)" if creds else ""),
                            "what": "hostname%s %s hard-coded%s: read the add-on variables, keeping today's value only as a local fallback when the variable is absent (compose keeps working locally) — %s" % (
                                "s" if len(names) > 1 else "", "/".join("'%s'" % n for n in names),
                                " with credentials in code (masked)" if creds else "", " · ".join(vars_) or "names from env-safe.sh names")})
        # ---- websockets / workers
        if r.get("websockets"):
            w = r["websockets"][0]
            evs = [e for e in r.get("socket_events", []) if e not in ("message", "connect", "disconnect")]
            assumed.append("WebSockets (%s) through Clever's reverse proxy: prove the data path%s; keep 1 instance (no sticky sessions needed)" % (
                ref(w["file"], w["line"]), (" (socket.io event '%s')" % evs[0]) if evs else ""))
            if "socket.io" in (r.get("framework") or []):
                if evs:
                    prove["socketio_event"] = evs[0]
                else:
                    prove["checks"].append({"method": "GET", "path": "/socket.io/?EIO=4&transport=polling", "code": 200, "marker": "sid",
                                            "why": "socket.io handshake (%s)" % ref(w["file"], w["line"])})
        if r.get("log_markers") and role == "worker":
            lm = r["log_markers"][0]
            m = re.search(r"(Processing|Connected|Received|Listening|Started)[^'\"{]*", lm["text"])
            if m:
                prove["log_expect"] = marker_text(m.group(0))
        if r.get("disk_writes") and rt != "docker":
            d = r["disk_writes"][0]
            assumed.append("local disk write at %s: the disk is ephemeral — Cellar or a database if it must persist" % ref(d["file"], d["line"]))
        effort = emax(base_effort, *[c["effort"] for c in changes])
        has_build = bool(rt == "node" and ((r.get("node") or {}).get("scripts") or {}).get("build"))
        return self.add(id=cid, kind=role, today=today, target=target, effort=effort, detail=detail, runtime=rt,
                        clever_type=self.rt["runtimes"].get(rt, {}).get("clever_type", rt), folder=folder,
                        config=cfg, changes=changes, evidence=ev, assumed=assumed, blockers=blockers, secrets_generate=secrets, prove=prove,
                        has_build_script=has_build)

    # ---------------------------------------------------------------- provenance strategies
    def from_compose(self):
        comp = self.s["compose"]
        cf = comp["file"]
        self.compose_kind = {}
        for sv in comp["services"]:
            if sv["image"]:
                self.compose_kind[sv["name"]] = self.kind_for_image(sv["image"])
        custom = 0
        for sv in sorted(comp["services"], key=lambda x: not x["ports"]):     # web services first: workers attach to them
            ev = ["[read] " + ref(cf, sv["line"])]
            today = ("built from %s/" % sv["build"] if sv["build"] != "." else "built from ./") if sv["build"] else sv["image"]
            if sv["profiles"]:
                self.add(id=sv["name"], kind="job", today=today + " (profile %s)" % ",".join(sv["profiles"]), target="drop", effort="none",
                         detail="seeding job (compose profile): a Clever Task if needed", evidence=ev)
                continue
            if sv["image"] and not sv["build"]:      # B: `build:` wins — `image:` is then only the tag of the built image
                kind = self.compose_kind.get(sv["name"])
                if kind is None:
                    u = self.rules["docker-compose"]["components"]["unknown_image"]
                    self.add(id=sv["name"], kind="other", today=today, target=u["target"], effort=u["effort"], detail=u["detail"], evidence=ev)
                    continue
                s = self.svc[kind]
                if s.get("image_target"):
                    it = s["image_target"]
                    self.add(id=sv["name"], kind="other", today=today, target=it["target"], effort=it["effort"], detail=it["detail"], evidence=ev)
                    continue
                reason = ""
                if sv["named_volumes"]:
                    reason = "volume %s → add-on" % ",".join(sv["named_volumes"])
                self.addon_component(kind, sv["name"], today, ev, reason=reason)
                continue
            custom += 1
            r = self.root(sv["build"])
            web = bool(sv["ports"]) or (r is not None and r.get("http_server") and r["runtime"] != "dotnet")
            role = "web" if sv["ports"] else ("web" if web and r and r["runtime"] in ("node",) else "worker")
            if sv["target"]:
                ev.append("[read] build target '%s' in compose (local dev stage) — Clever builds the app natively, not this stage" % sv["target"])
            cmd = sv.get("command") or ""
            same = next((c for c in self.components if c["kind"] == "web" and c.get("compose_build") == sv["build"]), None)
            if role == "worker" and same is not None and cmd:
                # same code as a web service, another command: a worker process on that app (B + F), not a second web app
                self.add_worker_process(same, sv["name"], cmd, ev)
                continue
            c = self.app_component(sv["name"], sv["build"], today, role, ev, start_hint=cmd or None)
            c["compose_build"] = sv["build"]
            if cmd and c["runtime"] in ("node", "go", "ruby", "php") and role == "web" and not re.search(r"npm (run )?start|yarn start", cmd):
                c["config"].setdefault("CC_RUN_COMMAND", cmd)
                c["evidence"].append("[read] compose command → CC_RUN_COMMAND")
            if cmd and c["runtime"] == "python":
                pre, server = self.split_start(cmd)
                if pre:
                    self.apply_pre_start(c, pre, ev[0])
        th = self.rules["docker-compose"]["components"]["cke_threshold"]
        docker_custom = [c for c in self.components if c["target"] == "docker"]
        if len(docker_custom) >= th:
            if self.hds:
                self.notes.append("%d containers would fit Clever Kubernetes, which is never available in HDS zones: separate apps instead" % len(docker_custom))
            else:
                self.notes.append("%d containers must stay containers together: Clever Kubernetes (BETA) is an option" % len(docker_custom))
        for o in comp.get("other_compose_files") or []:
            self.notes.append("other compose file ignored: %s" % o)

    def split_start(self, cmd):
        """`a; b && gunicorn x:app` → (pre-start commands, server command). Migrations go to CC_PRE_RUN_HOOK,
        compilation steps to CC_POST_BUILD_HOOK."""
        parts = [p_.strip() for p_ in re.split(r"\s*(?:;|&&)\s*", cmd or "") if p_.strip()]
        if len(parts) < 2:
            return [], cmd
        return parts[:-1], parts[-1]

    def apply_pre_start(self, web_c, pre, ev):
        run_, build_ = [], []
        for c_ in pre:
            (run_ if re.search(r"db upgrade|migrate|alembic|seed|prestart", c_) else build_).append(c_)
        if run_:
            web_c["config"]["CC_PRE_RUN_HOOK"] = " && ".join(([web_c["config"]["CC_PRE_RUN_HOOK"]] if web_c["config"].get("CC_PRE_RUN_HOOK") else []) + run_)
            web_c["evidence"].append("%s: %s before the server → CC_PRE_RUN_HOOK (every deploy, before the start)" % (ev, "; ".join(run_)))
        if build_:
            web_c["config"]["CC_POST_BUILD_HOOK"] = " && ".join(([web_c["config"]["CC_POST_BUILD_HOOK"]] if web_c["config"].get("CC_POST_BUILD_HOOK") else []) + build_)
            web_c["evidence"].append("%s: %s → CC_POST_BUILD_HOOK" % (ev, "; ".join(build_)))

    def add_worker_process(self, web_c, ptype, cmd, ev):
        """F: a background process runs NEXT to the web process on the same app (CC_WORKER_COMMAND_n), never as
        an app without a runtime; Celery uses the Python runtime's own variables."""
        m = re.search(r"\bcelery\b.*?(?:-A|--app)[= ](\S+).*?\b(worker|beat)\b", cmd)
        if m and (web_c.get("runtime") == "python"):
            if m.group(2) == "worker":
                web_c["config"]["CC_PYTHON_CELERY_MODULE"] = m.group(1)
                detail = "Celery worker: CC_PYTHON_CELERY_MODULE=%s on %s" % (m.group(1), web_c["id"])
            else:
                web_c["config"]["CC_PYTHON_CELERY_USE_BEAT"] = "true"
                web_c["config"].setdefault("CC_PYTHON_CELERY_MODULE", m.group(1))
                detail = "Celery beat: CC_PYTHON_CELERY_USE_BEAT=true on %s" % web_c["id"]
        elif web_c.get("target") == "docker":
            web_c["blockers"].append("%s process (%s): workers are not available for Docker apps — a second app is needed" % (ptype, trunc(cmd, 50)))
            detail = "not possible on a Docker app"
        else:
            n = len([k for k in web_c["config"] if k.startswith("CC_WORKER_COMMAND_")])
            web_c["config"]["CC_WORKER_COMMAND_%d" % n] = cmd
            detail = "CC_WORKER_COMMAND_%d on %s (same app, no extra cost)" % (n, web_c["id"])
        return self.add(id=ptype, kind="process", today="%s: %s" % (ptype, cmd[:50]), target="native", effort="none",
                        detail=detail, evidence=ev)

    def from_procfile(self, rid):
        pf = self.s.get("procfile")
        rule = self.rules[rid] if rid in self.rules else self.rules["heroku"]
        pmap = self.rules["heroku"]["components"]["process_map"]
        web_c = None
        if pf:
            for e in pf["entries"]:
                ev = ["[read] " + ref(pf["file"], e["line"])]
                if e["type"] == "web" and not e["commented"]:
                    pre, server = self.split_start(e["command"])
                    web_c = self.app_component("web", ".", "Procfile web: " + e["command"][:60], "web", ev, start_hint=server)
                    if pre:
                        self.apply_pre_start(web_c, pre, ev[0])
            if web_c is None:
                web_c = self.app_component("web", ".", "web app", "web", [])
            for e in pf["entries"]:
                if e["type"] == "web":
                    continue
                pm = pmap.get(e["type"], {"target": "native", "detail": "CC_WORKER_COMMAND (same app) or a second app"})
                ev = ["[read] " + ref(pf["file"], e["line"])]
                if e["type"] == "release":
                    tasks = (web_c or {}).get("config", {}).get("_django_tasks")
                    det = pm["detail"]
                    det = "CC_PYTHON_MANAGE_TASKS: migrate at build" if "manage.py" in e["command"] else det
                    if e["commented"]:
                        det += " (commented in Procfile today)"
                    if tasks is not None and "migrate" in e["command"]:
                        web_c["config"]["_django_tasks"] = ["migrate --no-input"] + [t_ for t_ in tasks if not t_.startswith("migrate")]
                    elif not e["commented"] and "manage.py" not in e["command"]:
                        web_c["config"]["CC_PRE_RUN_HOOK"] = e["command"]
                        det = "CC_PRE_RUN_HOOK=%s (every deploy, before the start: idempotent)" % trunc(e["command"], 60)
                    self.add(id="release", kind="job", today="Procfile release: " + e["command"][:50], target="native", effort="none",
                             detail=det, evidence=ev + (["[observed 2026-10-25] migrate at build reached the linked PostgreSQL add-on; prove.sh --build-expect 'Applying' re-checks it on this run"] if "manage.py" in e["command"] else []))
                    if "manage.py" in e["command"] and web_c is not None:
                        web_c["prove"]["build_expect"] = "Applying"
                elif not e["commented"]:
                    self.add_worker_process(web_c, e["type"], e["command"], ev)
        if web_c is None:
            web_c = self.app_component("web", ".", "web app", "web", [])
        # manifest add-ons and secrets
        man = self.s.get("app_json") if rid == "heroku" else (self.s.get("scalingo_json") or self.s.get("app_json"))
        amap = rule["components"].get("addon_map") or self.rules["heroku"]["components"]["addon_map"]
        declared = set()
        if man:
            for a in man.get("addons", []):
                base = a.split(":")[0]
                kind = amap.get(base)
                if kind:
                    declared.add(kind)
                    ac = self.addon_component(kind, kind, "%s add-on %s" % (rule["name"].split(" ")[0], a), ["[read] " + man["file"]])
                    ver = (man.get("addon_versions") or {}).get(base)
                    if ver:
                        ac["assumed"].append("%s asks %s version %s: the add-on is created with Clever's default version — check that the app accepts it" % (man["file"], kind, clean_text(ver, 10)))
            for e in man.get("env", []):
                if e.get("generator") == "secret" and ENV_NAME_RX.match(str(e["name"])):
                    web_c.setdefault("secrets_generate", []).append(e["name"])
                    web_c["evidence"].append("[read] %s env %s: generator secret → env-safe.sh generate" % (man["file"], e["name"]))
        self.implied_datastores(declared, web_c)

    def implied_datastores(self, declared, web_c):
        for kind, hits in sorted(self.s.get("datastores_in_code", {}).items()):
            if kind in declared or kind not in ("postgresql", "mysql", "redis", "mongodb", "elasticsearch", "s3"):
                continue
            h = hits[0]
            optional = False
            reason = ""
            r = self.root(web_c.get("folder") or ".") or {}
            py = r.get("python") or {}
            if kind == "postgresql" and any(d.startswith("psycopg") for d in py.get("commented_deps", [])):
                optional = True
                reason = "optional; SQLite today"
                web_c["changes"].append({"file": (web_c.get("folder") + "/" if web_c.get("folder") else "") + "requirements.txt", "line": None, "effort": "S",
                                         "short": "uncomment psycopg", "what": "uncomment the PostgreSQL driver (psycopg) to use the add-on"})
            if kind == "elasticsearch" and not optional:
                optional, reason = True, "optional: the app runs without search (offered as 'go without')"
            c = self.addon_component(kind, kind, "%s used in code" % self.svc[kind]["label"].replace(" add-on", ""),
                                     ["[read] " + ref(h["file"], h["line"])], optional=optional, reason=reason)

    def wire_addon_variables(self):
        """E: Clever injects its own add-on variables (POSTGRESQL_ADDON_URI, REDIS_URL…), never DATABASE_URL,
        POSTGRES_*, DB_*… Every app that reads such a convention gets a code change at the line that reads it:
        fall back to the add-on variable (a copied value would not follow credential rotation)."""
        addons = [c for c in self.components if c["target"] == "addon" and c.get("service")]
        apps = [c for c in self.components if c["kind"] in ("web", "worker") and c["target"] in ("native", "docker")]
        taken = set()
        for ad in addons:
            s = self.svc.get(ad["service"], {})
            conv, compat = s.get("conventions") or {}, set(s.get("compatible") or [])
            for app in apps:
                r = self.root(app.get("folder") or ".") or {}
                reads = r.get("env_reads") or {}
                hit = sorted(n for n in reads if n in conv and n not in compat and (app["id"], n) not in taken)
                byfile = {}
                for n in hit:
                    taken.add((app["id"], n))
                    byfile.setdefault(reads[n]["file"], []).append(n)
                for f, names in sorted(byfile.items()):
                    line = min(reads[n]["line"] for n in names)
                    pairs = ", ".join("%s → %s" % (n, conv[n]) for n in names)
                    note = "" if s.get("conventions_verified", True) else " [assumed names: check them with env-safe.sh names after the link]"
                    app["changes"].append({"file": f, "line": line, "effort": "S",
                                           "short": "%s: fall back to the %s add-on variable%s" % ("/".join(names), s["label"].replace(" add-on", ""), "s" if len(names) > 1 else ""),
                                           "what": "reads %s, which Clever does not inject: read it first, then fall back to %s (code change, keeps the local setup working; a copied value would not follow credential rotation)%s" % (
                                               ", ".join(names), pairs, note)})
                    app["evidence"].append("[read] %s reads %s" % (ref(f, line), ", ".join(names)))

    def from_vercel(self):
        webs = [r for r in self.s["roots"] if r["runtime"] == "node"]
        web_c = None
        for r in webs[:2]:
            c = self.app_component("web" if r["path"] == "." else r["path"], r["path"], "Next.js/Node on Vercel", "web", [])
            web_c = web_c or c
        web_c = web_c or {"config": {}, "folder": None, "changes": [], "evidence": [], "assumed": []}
        dm = self.rules["vercel"]["components"]["dep_map"]
        usage = self.s.get("sdk_usage") or {}
        sec = self.s.get("security") or []
        for r in webs:
            deps = set((r.get("node") or {}).get("deps", []))
            folder = None if r["path"] == "." else r["path"]
            for dep, m in sorted(dm.items()):
                if dep not in deps:
                    continue
                ev = ["[read] %spackage.json dependency %s" % ((folder + "/") if folder else "", dep)]
                if m.get("service") and m["target"] == "addon":
                    c = self.addon_component(m["service"], m.get("component_id") or dep, dep, ev)
                    c["effort"] = emax(c["effort"], m["effort"])
                    continue
                if not m.get("create_addon"):
                    self.add(id=m.get("component_id") or dep, kind="other", today=dep, target=m["target"], effort=m["effort"],
                             detail=m["detail"], evidence=ev)
                    continue
                # a rewrite whose target is a Clever service: the add-on is created, priced, and the code changes listed
                svc = self.svc[m["service"]]
                ad = self.addon_component(m["service"], "cellar" if m["service"] == "s3" else m["service"],
                                          "(new) target of the %s rewrite" % dep, ev)
                bucket = self.base + "-files"
                if m.get("bucket_var"):
                    web_c["config"][m["bucket_var"]] = bucket
                changes = []
                hits = usage.get(dep, [])
                auth = (self.s.get("auth_mechanism") or {}).get("present")
                noauth = not auth and any(f_["id"] in ("upload-token-no-auth", "auth-todo") for f_ in sec)
                if noauth and m.get("open_risk") and m["open_risk"] not in self.open_risks:
                    self.open_risks.append(m["open_risk"])
                nd = r.get("node") or {}
                pre = (folder + "/" if folder else "") + ("src/" if nd.get("src_dir") else "")
                if m.get("shared_client"):
                    sc_ = m["shared_client"]
                    changes.append({"file": pre + sc_["file"], "line": None, "effort": "S", "short": sc_["short"], "what": sc_["what"]})
                for h in hits:
                    sym = next(x for x in m["symbols"] if x["id"] == h["symbol"])
                    short, what = sym["short"], sym["what"]
                    if noauth:   # no auth mechanism in the source: keep its (anonymous) behaviour, say it is an open risk
                        short = short.replace("authenticate, ", "keep anonymous as in the source (open risk), ")
                        what = what.replace("authenticates the user first, ", "keeps the source's anonymous behaviour (open risk: no auth mechanism exists), ").replace("; authenticate first", "; anonymous as in the source (open risk)")
                    changes.append({"file": h["file"], "line": h["line"], "effort": sym["effort"], "short": short,
                                    "what": what + " — client: " + m["client"]})
                changes.append({"file": (folder + "/" if folder else "") + "package.json", "line": None, "effort": "S",
                                "short": m["package_change"].split(": ", 1)[1], "what": m["package_change"]})
                calls = len(hits)
                rc = self.add(id=m.get("component_id") or slugify(dep), kind="other", today="%s (%d call site%s)" % (dep, calls, "s" if calls != 1 else ""),
                              target="rewrite", effort=emax(m["effort"], *[c_["effort"] for c_ in changes]), detail=m["detail"],
                              evidence=ev + ["[read] %s:%s %s" % (h["file"], h["line"], h["symbol"]) for h in hits[:4]],
                              changes=changes, service=m["service"])
                rc["migration"] = {"tool": "scripts/migrate-files.py", "bucket": bucket, "addon": ad["id"], "how": m["migration"]}
                kept = m.get("kept") or {}
                if self.s.get("vercel_json") is not None and kept.get("vercel.json"):
                    self.kept.append(("vercel.json", kept["vercel.json"]))
                for h in nd.get("next_config_hosts") or []:
                    self.kept.append(("%s:%s" % (h["file"], h["line"]), kept.get("next_config_hosts", "")))
        # middleware runs in next start
        for r in webs:
            for mw in ((r.get("node") or {}).get("middleware") or []):
                self.add(id="middleware", kind="other", today="Next.js middleware (%s)" % mw, target="native", effort="none",
                         detail="runs inside next start on the Node server (no edge network); its headers still apply",
                         evidence=["[read] %s" % mw])
        vj = self.s.get("vercel_json") or {}
        for cr in vj.get("cron_list") or []:
            self.cron_component(cr, web_c, sec)
        self.implied_datastores(set(), web_c if web_c.get("id") else (self.components[0] if self.components else {}))

    def cron_component(self, cr, web_c, sec):
        """vercel.json cron → clevercloud/cron.json + job script; the job environment is EMPTY on Clever."""
        path, sched = cr["path"], cr["schedule"]
        if not re.fullmatch(r"^/[A-Za-z0-9/_.-]*$", path) or not re.fullmatch(r"^[0-9*/,\- ]+$", sched):
            self.notes.append("cron %r / %r not plain: write cron.json by hand" % (clean_text(path, 60), clean_text(sched, 40)))
            return
        name = slugify(path.strip("/").replace("/", "-") or "root", 30)
        folder = web_c.get("folder")
        pre = (folder + "/") if folder else ""
        secret = None
        for f_ in sec:
            if f_["id"] == "bearer-env-undefined" and f_.get("name"):
                secret = f_["name"]
                web_c["changes"].append({"file": f_["file"], "line": f_["line"], "effort": "S",
                                         "short": "refuse when %s is unset (else 'Bearer undefined' passes); compare in constant time" % secret,
                                         "what": "the route compares the header with `Bearer ${process.env.%s}`: return 401 when %s is unset, and compare with crypto.timingSafeEqual on equal-length buffers (never !==) — snippet in reference/runtimes-hooks.md; the plan generates the secret on Clever" % (secret, secret)})
        job = "clevercloud/cron-job.sh GET %s" % path
        changes = [
            {"file": pre + "clevercloud/cron.json", "line": None, "effort": "S",
             "short": "new: [\"%s $ROOT/%s\"] (UTC, runs on every instance)" % (sched, job),
             "what": "clevercloud/cron.json is a JSON array of strings \"<cron> $ROOT/<script> <args>\" ($ROOT is substituted): [\"%s $ROOT/%s\"]" % (sched, job)},
            {"file": pre + "clevercloud/cron-env.sh", "line": None, "effort": "S",
             "short": "new: copy of templates/cron/cron-env.sh, run by CC_PRE_RUN_HOOK: writes INSTANCE_NUMBER, PORT%s to clevercloud/.cron-env (600)" % ((", " + secret) if secret else ""),
             "what": "cron jobs run with an EMPTY environment: templates/cron/cron-env.sh (CC_PRE_RUN_HOOK) writes INSTANCE_NUMBER, PORT and the names of CRON_VARIABLES%s to clevercloud/.cron-env with umask 077 — reference/runtimes-hooks.md" % (
                 (" (%s)" % secret) if secret else "")},
            {"file": pre + "clevercloud/cron-job.sh", "line": None, "effort": "S",
             "short": "new: copy of templates/cron/cron-job.sh: instance 0 only, calls GET %s on 127.0.0.1:$PORT%s" % (path, " with the Bearer secret from a 600 header file" if secret else ""),
             "what": "the job sources clevercloud/.cron-env, refuses without INSTANCE_NUMBER, runs on instance 0 only, then calls GET %s on http://127.0.0.1:${PORT:-8080}%s; its output appears in the app logs (CMD/CMDOUT/CMDEND lines)" % (
                 path, (" with `Authorization: Bearer $%s` read by curl -H @<600 temp file> (never on curl's command line)" % secret) if secret else "")},
            {"file": pre + ".gitignore", "line": None, "effort": "S", "short": "ignore clevercloud/.cron-env",
             "what": "add clevercloud/.cron-env to .gitignore (written at each start on the instance, it holds the job's variables)"},
        ]
        web_c["config"]["CC_PRE_RUN_HOOK"] = "bash clevercloud/cron-env.sh"
        if secret:
            web_c["config"]["CRON_VARIABLES"] = secret          # the NAME the job needs; the value stays an app variable
        if secret:
            web_c.setdefault("secrets_generate", [])
            if secret not in web_c["secrets_generate"]:
                web_c["secrets_generate"].append(secret)
        c = self.add(id="cron-" + name, kind="job", today="vercel.json cron %s %s" % (sched, path), target="native", effort="S",
                     detail=self.rules["vercel"]["components"]["vercel_json"]["crons"]["detail"],
                     evidence=["[read] vercel.json crons", "[observed 2026-10-25] cron jobs get an empty environment (validation round 2)"],
                     changes=changes)
        c["prove"] = {"checks": [{"method": "GET", "path": path, "code": 401, "why": "the route refuses a call without the secret"}],
                      "auth_check": {"method": "GET", "path": path, "code": 200, "auth_env": secret} if secret else None,
                      "cron_expect": "ok", "alias": web_c.get("id") or "web"}
        c["assumed"] = list(c.get("assumed") or []) + [CRON_PROOF_COST]

    def from_supabase(self):
        lv = (self.a.level or "A").upper()
        levels = self.rules["supabase"]["components"]["levels"]
        sb = self.s.get("supabase") or {}
        webs = [r for r in self.s["roots"] if r["runtime"] in ("node", "python", "php")]
        for r in webs[:1]:
            self.app_component("web", r["path"], "front/API (often on Vercel)", "web", [])
        calls = sb.get("calls", {})
        counts = ", ".join("%s=%d" % (k, v) for k, v in calls.items() if v)
        ev = ["[read] supabase counters: %s; policies=%s" % (counts or "none", sb.get("policies"))]
        if lv == "A":
            self.add(id="supabase", kind="datastore", today="Supabase (DB, Auth, Storage…)", target="keep-saas", effort="none",
                     detail="stays on Supabase for the POC — plugged into a DEV Supabase project, never production; source read-only",
                     evidence=ev)
            self.notes.append("Supabase level B (%s, effort %s) and C (%s, effort %s) are plans, not POC: detailed method in %s" % (
                levels["B"]["name"], levels["B"]["effort"], levels["C"]["name"], levels["C"]["effort"], self.rules["supabase"]["components"]["migration_guide"]))
        else:
            sites = sb.get("sites") or {}
            mig = "supabase/migrations/*.sql" if sb.get("migrations") else "the schema (survey)"
            self.addon_component("postgresql", "database", "Supabase PostgreSQL", ev)
            # #3: the real changes of a level B/C landing, listed (they count in the verdict)
            rest = [{"file": x["file"], "line": x["line"], "effort": "M", "short": "PostgREST .from(…) from the browser → a server route querying PostgreSQL",
                     "what": "move each .from(…) call of this file to a server route that runs the SQL with the user's id set first (set_config('app.user_id', …, true)) so RLS applies; never a superuser connection"}
                    for x in sites.get("from") or []]
            rest.append({"file": mig, "line": None, "effort": "M", "short": "auth.uid() → current_setting('app.user_id') in every policy; ALTER TABLE … FORCE ROW LEVEL SECURITY",
                         "what": "the add-on has no Supabase roles: define auth.uid() (or rewrite the policies) on current_setting('app.user_id', true)::uuid, and FORCE ROW LEVEL SECURITY on each table so the owner role is filtered too (modules/supabase/reference/data-migration.md)"})
            self.add(id="supabase-rest", kind="other", today="PostgREST calls from the browser (%d)" % calls.get("from", 0),
                     target="rewrite", effort="L", detail=levels["B"]["rule"], evidence=ev, changes=rest)
            if lv == "C":
                auth_ch = [{"file": x["file"], "line": x["line"], "effort": "M", "short": "supabase.auth.* → the app's own session routes",
                            "what": "replace the Supabase Auth calls of this file with the routes of modules/supabase/templates/auth-nextjs (app/api/auth/{signup,login,logout}, lib/session.ts)"}
                           for x in sites.get("auth") or []]
                auth_ch += [{"file": d["file"], "line": None, "effort": "S", "short": "remove %s" % d["name"],
                             "what": "remove the dependency %s once no import is left (level C: no Supabase SDK in the app)" % d["name"]}
                            for d in sb.get("supabase_deps") or []]
                auth_ch.append({"file": "app/api/auth/ + lib/ (from modules/supabase/templates/auth-nextjs)", "line": None, "effort": "M",
                                "short": "new: signup / login / logout routes, session, rate limit, bcrypt verify",
                                "what": "copy the auth template (signup, login, logout, lib/auth.ts, lib/session.ts, lib/ratelimit.ts); the bcrypt cost comes from the survey (auth.password_hash_cost)"})
                auth_ch.append({"file": mig, "line": None, "effort": "M",
                                "short": "import auth.users (ids + bcrypt hashes) into the app's users table, then the profile trigger" + (" (it fires on auth.users: create it AFTER the import)" if sb.get("auth_users_trigger") else ""),
                                "what": "accounts copied source → target by a script (psql, PGPASSFILE), hashes never through the agent's context; the trigger that creates profiles runs after the import, not during it"})
                self.add(id="auth", kind="other", today="Supabase Auth", target="rewrite", effort="L",
                         detail="auth inside the app (users and bcrypt hashes migrated)" + ("" if self.hds else " or the Keycloak add-on"), evidence=ev, changes=auth_ch)
                if calls.get("storage"):
                    st = self.addon_component("s3", "storage", "Supabase Storage", ev, reason="serve with nosniff + CSP sandbox; HTML/SVG refused")
                    self.storage_key_gate(st, sb)
                    # #13: the target's upload route must check the owner, which the source policy did not
                    for f_ in [x for x in (self.s.get("security") or []) if x["id"] == "storage-insert-bucket-only"]:
                        if isinstance(st, dict):
                            st["changes"].append({"file": f_["file"], "line": f_["line"], "effort": "S",
                                                  "short": "upload: check the owner (owner = auth.uid() / the session's user), not only the bucket",
                                                  "what": "the source INSERT policy only tests the bucket: on the target, the upload route (and the policy if kept) requires owner = the signed-in user and a path under their id"})
            else:
                self.add(id="auth", kind="other", today="Supabase Auth", target="keep-saas", effort="S",
                         detail="Supabase Auth kept; JWT verified server-side", evidence=ev)
            self.notes.append("detailed migration method: %s (survey, data, auth, Storage → Cellar, acceptance)" % self.rules["supabase"]["components"]["migration_guide"])

    def data_checks(self, r, prove):
        """#23: an app with data (SQLite → PostgreSQL, files → Cellar) is proven on the paths that touch them:
        its parameterless GET routes, and one upload through its HTML form when it has one."""
        if not (r.get("sqlite_uses") or r.get("upload_uses")):
            return
        have = {(k["method"], k["path"]) for k in prove["checks"]}
        gets = []
        for x in r.get("routes") or []:
            m = re.search(r"(?:\.get|route)\(\s*['\"](/[A-Za-z0-9_/.-]*)['\"]", x["text"])
            if m and m.group(1) != "/" and "<" not in m.group(1) and "POST" not in x["text"] and ("GET", m.group(1)) not in have:
                gets.append((m.group(1), x))
        for path, x in gets[:2]:
            prove["checks"].append({"method": "GET", "path": path, "code": 200, "why": "real code path (%s)" % ref(x["file"], x["line"])})
        files = r.get("file_fields") or []
        posts = []
        for x in r.get("routes") or []:
            m = re.search(r"(?:\.post\(\s*|route\(\s*)['\"](/[A-Za-z0-9_/.-]*)['\"]", x["text"])
            if m and ("post" in x["text"].lower()) and "<" not in m.group(1):
                posts.append(m.group(1))
        if r.get("upload_uses") and files and posts:
            others = [f for f in (r.get("form_fields") or []) if f not in files][:3]
            prove["upload_form"] = {"route": posts[0], "field": files[0], "page": "/", "fields": ["%s=Test POC" % f for f in others]}
        if r.get("sqlite_uses"):
            prove["data_hint"] = "after the data migration, add --expect '<a value of a migrated row>' (the page shows the migrated data)"

    def storage_key_gate(self, st, sb):
        """#1/#2: the bytes of a PRIVATE bucket cannot be read without a user session (a login WRITES to the
        source) — only with the project's secret key, provided by the developer in a 600 file."""
        if not isinstance(st, dict):
            return
        bk = sb.get("storage_buckets") or []
        private = [b["id"] for b in bk if b.get("public") is not True]
        if bk and not private:
            st.setdefault("evidence", []).append("[read] every bucket created by the migrations is public: no secret key needed to read the files")
            return
        what = ("private bucket(s) %s" % ", ".join(private[:5])) if private else "buckets of unknown visibility (not in the migrations: assume private)"
        kf = getattr(self.a, "storage_key_file", None)
        kf_top = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(kf)), "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL).stdout.decode().strip() if kf else ""
        if kf_top and os.path.realpath(kf_top) == os.path.realpath(os.path.expanduser("~")):
            kf_top = ""
        if kf_top:
            st.setdefault("blockers", []).append("files: the key file %s is inside the repository %s (a `git add .` would commit it): move it outside, e.g. ~/sb-secret-key" % (untrusted(kf, 60), untrusted(kf_top, 60)))
            return
        if kf and os.path.isfile(kf) and not (os.stat(kf).st_mode & 0o077):
            st.setdefault("evidence", []).append("[observed] secret key file provided by the developer (mode 600, never read by plan.py): migrate-storage.py --key-file")
            return
        st.setdefault("blockers", []).append(
            "files: BLOCKED until the project's secret key is provided — %s: their bytes need the sb_secret_… (or service_role) key, read-only. "
            "The developer copies it from the dashboard (Settings > API keys), `(umask 077; pbpaste > ~/sb-secret-key)` OUTSIDE the repository, clears the clipboard; "
            "then plan again with --storage-key-file <file> (the agent never extracts it)" % what)

    def cron_command(self, cmd):
        """A crontab command of the VM → the argv to run from the app folder on Clever, or None."""
        c = re.sub(r"\s*(>>?|2>&1|2>>?)\s*\S*", " ", cmd)                    # no log redirections (the output is in the app logs)
        c = re.sub(r"^\s*cd\s+\S+\s*(&&|;)\s*", "", c).strip()               # the job runs from the app folder
        c = re.sub(r"(^|\s)(\S*/)?venv/bin/", r"\1", c)                         # the runtime's own python, not the VM's venv
        c = re.sub(r"(^|\s)/\S*/bin/(python3?|node|php|ruby)\b", r"\1\2", c)
        return c if c and not re.search(r"[|&;`$<>]", c) else None

    def from_vm(self):
        """vm-manual: an app run by hand on a VM (systemd / supervisor, nginx, crontab). The process comes from the
        Procfile when there is one, else from the systemd ExecStart / supervisor command; SQLite → PostgreSQL add-on
        + data migration, local files → Cellar + file migration, crontab → cron.json, Environment= names → variables."""
        vm = self.s.get("vm") or {}
        pf = self.s.get("procfile")
        roots = [r for r in self.s["roots"] if r["runtime"] not in (None, "docker")] or [r for r in self.s["roots"] if r["runtime"]]
        via_procfile = bool(pf and any(e["type"] == "web" for e in pf["entries"]))
        if via_procfile:
            self.from_procfile("heroku")          # also maps the datastores used in code
            web_c = next(c for c in self.components if c["id"] == "web")
        else:
            start = (vm.get("systemd") or [{}])[0].get("exec") or next((x["command"] for x in vm.get("supervisor", []) if re.search(r"gunicorn|uvicorn|node|php|daphne", x["command"])), "")
            r = roots[0] if roots else {"path": "."}
            ev = ["[read] %s ExecStart" % ref(vm["systemd"][0]["file"], vm["systemd"][0]["line"])] if vm.get("systemd") else []
            web_c = self.app_component("web", r["path"], "VM process: " + trunc(start or "?", 60), "web", ev, start_hint=start)
            for sp in vm.get("supervisor", []):
                if sp["command"] != start and not re.search(r"gunicorn|uvicorn|daphne", sp["command"]):
                    self.add_worker_process(web_c, slugify(sp["name"], 30), re.sub(r"(^|\s)(\S*/)?venv/bin/", r"\1", sp["command"]), ["[read] " + sp["file"]])
        if re.search(r"--bind[= ]127\.0\.0\.1|-b\s+(127\.0\.0\.1|localhost)", json.dumps(vm)):
            web_c["evidence"].append("[read] the VM binds 127.0.0.1 behind nginx: on Clever the runtime binds the server itself (no change, trap avoided)")
        for sd in vm.get("systemd", []):
            if sd["env_names"]:
                web_c["assumed"].append("systemd Environment= names %s: VM paths/values, not copied — replaced by the add-on variables below (values never read)" % ", ".join(sd["env_names"]))
        for ng in vm.get("nginx", []):
            notes = []
            if ng.get("max_body"):
                notes.append("client_max_body_size %s → enforce the limit in the app" % ng["max_body"])
            if ng.get("server_name"):
                notes.append("server_name %s → `clever domain add` (certificate automatic)" % ng["server_name"])
            if ng.get("static_locations"):
                notes.append("static locations %s → served by the app or the static runtime" % ", ".join(ng["static_locations"]))
            web_c["evidence"].append("[read] %s: nginx proxy_pass → Clever's reverse proxy (nothing to set); %s" % (ng["file"], "; ".join(notes) or "nothing else"))
        r = self.root(web_c.get("folder") or ".") or {}
        pre = (web_c.get("folder") + "/") if web_c.get("folder") else ""
        handled = set()
        # SQLite → PostgreSQL add-on, code change at each call site, data migration with row counts
        if r.get("sqlite_uses"):
            pg = next((c for c in self.components if c["target"] == "addon" and c.get("service") == "postgresql"), None) or \
                self.addon_component("postgresql", "database", "SQLite on the VM disk", ["[read] " + ref(r["sqlite_uses"][0]["file"], r["sqlite_uses"][0]["line"])],
                                     reason="replaces SQLite (the disk is ephemeral)")
            byf = {}
            for h in r["sqlite_uses"]:
                if re.search(r"sqlite:///", h["text"]):      # an ORM URL fallback (DATABASE_URL first): the ORM speaks PostgreSQL, no rewrite
                    continue
                byf.setdefault(h["file"], []).append(h["line"])
            py_deps = set(((r.get("python") or {}).get("deps")) or [])
            for f, lines in sorted(byf.items()):
                web_c["changes"].append({"file": f, "line": lines[0], "lines": lines, "effort": "M",
                                         "short": "SQLite → PostgreSQL (POSTGRESQL_ADDON_URI): psycopg connection, %s placeholders, identity keys",
                                         "what": "replace sqlite3 with psycopg on POSTGRESQL_ADDON_URI (keep SQLite only as a local fallback when the variable is absent): placeholders ? → %s, "
                                                 "`integer primary key autoincrement` → `bigint generated by default as identity primary key`, row_factory → dict rows"})
            if byf and not any(d_.startswith("psycopg") for d_ in py_deps):
                web_c["changes"].append({"file": pre + "requirements.txt", "line": None, "effort": "S", "short": "add psycopg[binary]", "what": "add the PostgreSQL driver (psycopg[binary])"})
            dm = self.add(id="data", kind="job", today="SQLite file on the VM (not in git)", target="native", effort="S", optional=not byf,
                          detail="one-time copy into the PostgreSQL add-on with scripts/migrate-sqlite.py (schema translated, row counts equal per table, sequences reset)",
                          evidence=["[read] " + ref(h["file"], h["line"]) for h in r["sqlite_uses"][:2]])
            dm["migration"] = {"tool": "scripts/migrate-sqlite.py", "addon": pg["id"], "how": "--sqlite <the .db file from the VM> --pg-env-file <600 file> --dry-run, then without"}
            if not self.hds:
                dm["options"].append({"option": "keep SQLite on an FS Bucket (one instance, low write rate)", "boundary": "no FS Bucket in HDS zones; one instance only; no backups of the file by the platform",
                                      "risk": "acceptable for a POC", "status": "not proposed by default"})
            handled.add("data")
        # local files → Cellar, code change, file migration
        if r.get("upload_uses"):
            ce = self.addon_component("s3", "cellar", "files on the VM disk", ["[read] " + ref(r["upload_uses"][0]["file"], r["upload_uses"][0]["line"])],
                                      reason="replaces the uploads folder (the disk is ephemeral)")
            bucket = self.base + "-files"
            web_c["config"]["CELLAR_BUCKET"] = bucket
            byf = {}
            for h in r["upload_uses"]:
                byf.setdefault(h["file"], []).append(h["line"])
            for f, lines in sorted(byf.items()):
                web_c["changes"].append({"file": f, "line": lines[0], "lines": lines, "effort": "M",
                                         "short": "local files → Cellar (PutObject / GetObject on CELLAR_BUCKET)",
                                         "what": "write with PutObject and read with GetObject on CELLAR_BUCKET (boto3, endpoint https://$CELLAR_ADDON_HOST, path-style, SigV4 region us-east-1); "
                                                 "deliver with `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`, inline only png/jpeg/webp/gif"})
            web_c["changes"].append({"file": pre + "requirements.txt", "line": None, "effort": "S", "short": "add boto3", "what": "add boto3 (S3 client for Cellar)"})
            fm = self.add(id="files", kind="job", today="uploaded files on the VM (not in git)", target="native", effort="S",
                          detail="one-time copy into the bucket %s with scripts/migrate-files.py --source-dir (keys kept, SHA-256 verified, manifest; symbolic links never followed)" % bucket,
                          evidence=["[read] " + ref(h["file"], h["line"]) for h in r["upload_uses"][:2]], service="s3")
            fm["migration"] = {"tool": "scripts/migrate-files.py", "bucket": bucket, "addon": ce["id"], "how": "--source-dir <the uploads folder copied from the VM>"}
            handled.add("files")
        # crontab → clevercloud/cron.json + templates/cron/ (run mode)
        lines_ = []
        for cr in vm.get("crontab", []):
            cmd = self.cron_command(cr["command"])
            if not cmd:
                web_c["blockers"].append("crontab %s: command not translatable as is (%s) — rewrite it as a script of the app" % (ref(cr["file"], cr["line"]), untrusted(cr["command"], 60)))
                continue
            lines_.append((cr, cmd))
        if lines_:
            names = sorted(set(["POSTGRESQL_ADDON_URI"] * bool(r.get("sqlite_uses")) + (["CELLAR_ADDON_HOST", "CELLAR_ADDON_KEY_ID", "CELLAR_ADDON_KEY_SECRET", "CELLAR_BUCKET"] if r.get("upload_uses") else [])))
            web_c["config"]["CC_PRE_RUN_HOOK"] = " && ".join(([web_c["config"]["CC_PRE_RUN_HOOK"]] if web_c["config"].get("CC_PRE_RUN_HOOK") else []) + ["bash clevercloud/cron-env.sh"])
            if names:
                web_c["config"]["CRON_VARIABLES"] = " ".join(names)
            entries = ", ".join('"%s $ROOT/clevercloud/cron-job.sh run %s"' % (cr["schedule"], cmd) for cr, cmd in lines_)
            ch = [{"file": pre + "clevercloud/cron.json", "line": None, "effort": "S", "short": "new: [%s] (UTC)" % entries,
                   "what": "crontab of the VM → clevercloud/cron.json [%s]; the schedule is in UTC on Clever" % entries},
                  {"file": pre + "clevercloud/cron-env.sh", "line": None, "effort": "S", "short": "new: copy of templates/cron/cron-env.sh (CC_PRE_RUN_HOOK)",
                   "what": "jobs run with an EMPTY environment: cron-env.sh writes INSTANCE_NUMBER, PORT and CRON_VARIABLES (%s) to clevercloud/.cron-env (600)" % (", ".join(names) or "none")},
                  {"file": pre + "clevercloud/cron-job.sh", "line": None, "effort": "S", "short": "new: copy of templates/cron/cron-job.sh (run mode, instance 0 only)",
                   "what": "cron-job.sh run <command>: sources .cron-env, runs on instance 0 only, from the app folder"},
                  {"file": pre + ".gitignore", "line": None, "effort": "S", "short": "ignore clevercloud/.cron-env", "what": "add clevercloud/.cron-env to .gitignore"}]
            jobfiles = sorted(set(re.findall(r"\b([\w/.-]+\.py)\b", " ".join(c_ for _, c_ in lines_))))
            for jf in jobfiles:
                if r.get("sqlite_uses") or r.get("upload_uses"):
                    ch.append({"file": pre + jf, "line": None, "effort": "S", "short": "the job now reads PostgreSQL and the Cellar bucket, not the VM disk",
                               "what": "the job worked on the local database / folder: list and delete through POSTGRESQL_ADDON_URI and the Cellar bucket instead"})
            tz = vm.get("cron_tz")
            offers = [o for o in (utc_offer(cr["schedule"], tz or "Europe/Paris") for cr, _ in lines_) if o]
            assumed = [("the VM's time zone is %s (read: CRON_TZ/TZ of the crontab): " % tz if tz else
                        "the VM's time zone is assumed Europe/Paris (check `timedatectl` on the VM): ") + o for o in offers[:2]]
            cj = self.add(id="cron", kind="job", today="crontab: " + ", ".join(cr["schedule"] for cr, _ in lines_), target="native", effort="S",
                          detail="clevercloud/cron.json + templates/cron/ (run mode: one instance, empty environment handled)",
                          evidence=["[read] " + ref(cr["file"], cr["line"]) for cr, _ in lines_[:2]], changes=ch, assumed=assumed + [CRON_PROOF_COST])
            handled.add("cron")
        handled |= {"process"}
        for c_ in self.s.get("couplings") or []:
            if c_["kind"] in handled:
                self.handled_couplings.add(c_["what"])
        if r.get("sqlite_uses") or r.get("upload_uses"):
            web_c["assumed"] = [a_ for a_ in web_c["assumed"] if not a_.startswith("local disk write")]
        if not via_procfile:
            self.implied_datastores({"postgresql"} if r.get("sqlite_uses") else set(), web_c)

    def not_an_app(self, r, paths):
        """Roots that are not deployable apps: cloud functions, codelab 'start' variants next to an 'end',
        tooling-only package.json (no start, no main, no server dependency)."""
        nd = r.get("node") or {}
        deps = set(nd.get("deps") or [])
        start = (nd.get("scripts") or {}).get("start") or ""
        if "firebase-functions" in deps or re.search(r"\bfirebase\b|functions:shell", start):
            return "cloud functions (not an app on its own: see what must move)"
        m = re.match(r"^(.*)-start(/.*)?$", r["path"])
        if m and any(p_ == m.group(1) + "-end" + (m.group(2) or "") for p_ in paths):
            return "codelab starting point (the '-end' variant is the app)"
        if r["runtime"] == "node" and not start and not nd.get("main") and not (deps & {"express", "fastify", "next", "@nestjs/core", "koa", "hono", "nuxt", "@strapi/strapi", "socket.io"}) and not (nd.get("scripts") or {}).get("build"):
            return "tooling only (no start script, no server dependency)"
        return None

    def generic(self):
        paths = [r["path"] for r in self.s["roots"]]
        for r in self.s["roots"]:
            if r["runtime"] is None:
                continue
            why = self.not_an_app(r, paths)
            if why:
                self.notes.append("%s skipped: %s" % (r["path"], why))
                continue
            role = "web" if r.get("http_server") or r["runtime"] == "node" else "worker"
            self.app_component(r["path"] if r["path"] != "." else "web", r["path"], "%s app" % r["runtime"], role, [])
        self.implied_datastores(set(), next((c for c in self.components if c["kind"] == "web"), {"changes": [], "folder": None}))

    # ---------------------------------------------------------------- verdict, provision, price
    def build(self):
        prim = self.s.get("primary_provenance")
        provs = {p["id"]: p for p in self.s.get("provenances", [])}
        full = [p for p in self.s.get("provenances", []) if p["coverage"] == "full"]
        self.mode = "plan"
        if prim and provs.get(prim, {}).get("coverage") != "full":
            self.mode = "report-only"
        if prim == "supabase":
            self.from_supabase()
        elif prim == "docker-compose" and self.s.get("compose") and not self.s["compose"].get("dev_reasons"):
            self.from_compose()
        elif prim in ("heroku", "scalingo"):
            self.from_procfile(prim)
        elif prim == "vercel":
            self.from_vercel()
        elif prim == "vm-manual":
            self.from_vm()
        else:
            if prim == "docker-compose":
                self.notes.append("the compose file describes a development setup (%s): planned from the code, not from it" % "; ".join(self.s["compose"].get("dev_reasons") or []))
            self.generic()
        self.wire_addon_variables()
        self.also = [p for p in self.s.get("provenances", []) if p["id"] != prim]
        # finalize config: Django tasks (migrate when a database add-on is planned), secret formats, required inputs
        dbs = [c for c in self.components if c["target"] == "addon" and c.get("service") in ("postgresql", "mysql")]
        provided = set()
        for c in self.components:
            if c["target"] == "addon":
                provided |= set((self.svc.get(c.get("service"), {}).get("conventions") or {}).keys()) | set(self.svc.get(c.get("service"), {}).get("compatible") or [])
        for c in self.components:
            t = c["config"].pop("_django_tasks", None)
            if t is not None and dbs and not any(x.startswith("migrate") for x in t):
                t = ["migrate --no-input"] + t
                c["evidence"].append("[read] Django + a database add-on: migrate --no-input in CC_PYTHON_MANAGE_TASKS (tables created at build)")
            if t:
                c["config"]["CC_PYTHON_MANAGE_TASKS"] = ", ".join(t)
            fm = c["config"].pop("_secret_formats", None)
            if fm:
                c["secret_formats"] = fm
            if c["kind"] in ("web", "worker") and c["target"] in ("native", "docker"):
                r = self.root(c.get("folder") or ".") or {}
                need = [n for n in (r.get("env_required") or []) if n not in provided and n not in c["config"] and n not in (c.get("secrets_generate") or [])]
                if need:
                    c["required_inputs"] = sorted(set(c.get("required_inputs", []) + need))
        if self.mode != "report-only":
            self.platform_text_options()
        self.verdict()
        self.provision()
        self.price()

    def applied_files(self):
        pb = self.s.get("poc_branch") or {}
        return set(pb.get("files_changed") or [])

    def code_changes(self, include_applied=False):
        files = {}
        applied = self.applied_files()
        for c in self.components:
            if c["target"] == "drop":
                continue
            for ch in c["changes"]:
                if ch["file"] in applied and not include_applied:
                    continue
                files.setdefault(ch["file"], []).append((c["id"], ch))
        return files

    def blockers(self):
        out = []
        for c in self.components:
            if c["optional"] or c["target"] == "drop":
                continue
            out += ["%s: %s" % (c["id"], b) for b in c.get("blockers", [])]
            if c["kind"] == "other" and c["target"] == "docker":
                out.append("%s: %s — not provisioned by this plan (a Docker app to set up by hand, or drop it)" % (c["id"], trunc(c["today"], 60)))
        webs = [c for c in self.components if c["kind"] == "web" and c["target"] in ("native", "docker") and not c["optional"]]
        if self.mode != "report-only" and not webs and not any(c["target"] in ("keep-saas",) for c in self.components):
            out.append("no web app could be planned from this repository: nothing would answer HTTP")
        return out

    def platform_text_options(self):
        """#20: page texts naming the source platform → optional wording changes (not counted, only with a yes)."""
        for t in (self.s.get("platform_texts") or [])[:2]:
            what = "page text names %s (%s): optional wording change once on Clever — not needed to run; if it is the <title>, update prove --expect too" % (
                t["platform"], untrusted(t["text"], 80))
            if not any(o["file"] == t["file"] for o in self.optional_changes):
                self.optional_changes.append({"file": t["file"], "line": t["line"], "what": what})

    def verdict(self):
        files = self.code_changes()
        inputs = sorted(set(i for c in self.components if not c["optional"] for i in c.get("required_inputs", [])))
        n = len(files) + len(inputs)
        tg = [c["target"] for c in self.components if not c["optional"]]
        self.blocking = self.blockers() if self.mode != "report-only" else []
        if self.mode == "report-only":
            v = "not yet covered — report only"
        elif "blocked" in tg:
            v = "not a fit today"
        elif self.blocking:
            v = "not ready — %d blocking point%s to settle first" % (len(self.blocking), "s" if len(self.blocking) > 1 else "")
        elif "keep-saas" in tg or any(c["target"] == "rewrite" and c["effort"] in ("L", "XL") for c in self.components if not c["optional"]):
            v = "partial landing"
        elif n:
            v = "ready with %d change%s" % (n, "s" if n > 1 else "")
        else:
            v = "ready"
        self.verdict_text = v
        self.n_changes = n
        self.inputs = inputs

    def provision(self):
        apps, addons = [], []
        base = self.base
        app_aliases = []
        for c in self.components:
            if c["target"] in ("native", "docker") and c["kind"] in ("web", "worker"):
                alias = re.sub(r"[^a-z0-9-]+", "-", c["id"].lower())
                env = {k: v for k, v in c["config"].items() if ENV_NAME_RX.match(k) and not CONTROL_RX.search(str(v))}
                apps.append({"alias": alias, "name": fit_name(base, alias), "type": c.get("clever_type") or c.get("runtime"),
                             "folder": c.get("folder"), "flavor": self.rt["platform"]["default_flavor"],
                             "build_flavor": self.rt["platform"]["default_build_flavor"], "instances": 1,
                             "env": env, "secrets_generate": c.get("secrets_generate", []), "org": c["org"], "component": c["id"],
                             "has_build_script": bool(c.get("has_build_script")), "secret_formats": c.get("secret_formats") or {}})
                app_aliases.append(alias)
        users = {}
        for c in self.components:
            if c["target"] != "addon":
                continue
            linked = []
            for comp in self.components:
                if comp["kind"] not in ("web", "worker") or comp["target"] not in ("native", "docker"):
                    continue
                r = self.root(comp.get("folder") or ".") or {}
                hosts = {h["service"] for h in r.get("service_hosts", [])}
                kinds = {self.compose_kind.get(h) for h in hosts} if hasattr(self, "compose_kind") else set()
                if c["id"] in hosts or c.get("service") in kinds or not hasattr(self, "compose_kind"):
                    linked.append(re.sub(r"[^a-z0-9-]+", "-", comp["id"].lower()))
            users[c["id"]] = linked
            opts = ["encryption=true"] if c.get("encryption") else []
            addons.append({"name": fit_name(base, re.sub(r"[^a-z0-9-]+", "-", c["id"].lower())), "provider": c["provider"],
                           "plan": c["plan"], "options": opts, "link": linked or app_aliases[:1], "org": c["org"],
                           "zone": c.get("zone_override") or self.zone,
                           "optional": c["optional"], "component": c["id"]})
        self.provision_spec = {"zone": self.zone, "hds": self.hds, "name_prefix": base, "apps": apps, "addons": addons,
                               "secondary_org_zone": "par" if self.hds else None,
                               "rules": {"no_dev_plan_in_hds": True, "encryption_on_dedicated": True, "node_no_NODE_ENV_production_before_build": True}}

    def price(self):
        items = []
        for a in self.provision_spec["apps"]:
            items.append("apps:%s" % a["flavor"])
        nb = len(self.provision_spec["apps"])
        if nb:
            items.append("apps:%s%s@build" % (self.provision_spec["apps"][0]["build_flavor"].lower(), ("x%d" % nb) if nb > 1 else ""))
        for c in self.components:
            if c.get("price_item"):
                items.append(c["price_item"])
        self.price_items = items
        self.quote = None
        if self.a.no_price:
            return
        g = self.load_grid()
        if g:
            self.quote = pricemod.quote(self.zone, items, 5, g)
            if self.quote:
                self.quote["read_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())   # when THIS grid was read
            ad_items = [c["price_item"] for c in self.components if c.get("price_item")]
            self.quote_addons = pricemod.quote(self.zone, ad_items, 5, g) if ad_items else None
        # optional components priced separately for "go without X"
        self.quote_without = {}
        if self.quote:
            for c in self.components:
                if c["optional"] and c.get("price_item"):
                    rest = list(items)
                    rest.remove(c["price_item"])
                    self.quote_without[c["id"]] = pricemod.quote(self.zone, rest, 5, g)

    # ---------------------------------------------------------------- output
    def to_json(self):
        return {
            "tool": "plan.py", "version": 1, "zone": self.zone, "hds": self.hds, "health_answer": self.a.health,
            "zone_reason": self.a.zone_reason, "mode": self.mode,
            "primary_provenance": self.s.get("primary_provenance"),
            "also_detected": [{"id": p["id"], "coverage": p["coverage"], "evidence": p["evidence"][:2]} for p in self.also],
            "verdict": self.verdict_text, "blocking": getattr(self, "blocking", []), "required_inputs": getattr(self, "inputs", []), "code_changes": {f: [dict(ch, component=cid) for cid, ch in v] for f, v in self.code_changes().items()},
            "components": self.components, "notes": self.notes, "catalog_sources": self.cat.sources,
            "provision": self.provision_spec, "price_items": self.price_items, "price": self.quote,
            "price_without": getattr(self, "quote_without", {}),
            "price_addons_only": getattr(self, "quote_addons", None),
            "watch": self.watch_hits,
            "open_risks": self.open_risks,
            "kept": [{"file": k, "why": v} for k, v in self.kept],
            "optional_changes": self.optional_changes,
            "repo": {k: self.s["repo"].get(k) for k in ("toplevel", "branch", "head", "uncommitted_changes")},
            "secrets_hygiene": {"tracked_env_files": [{"file": t["file"], "names": t["names"]} for t in self.s["secrets_hygiene"]["tracked_env_files"]],
                                "hardcoded_credentials": [ref(x["file"], x["line"]) for x in self.s["secrets_hygiene"]["hardcoded_credentials"]]},
        }

    def vision(self):
        name = os.path.basename(self.s["repo"]["toplevel"])
        L = []
        zl = {"par": "Paris", "parhds": "Paris HDS"}.get(self.zone, self.zone)
        L.append("## %s on Clever Cloud — %s" % (name, self.verdict_text.capitalize()))
        if self.mode == "report-only":
            r = self.rules.get(self.s["primary_provenance"], {})
            L.append("Provenance **%s** is recognised but not covered yet: this is a report, not a plan. %s" % (r.get("name"), r.get("not_covered_note", "")))
        else:
            nat = [c["id"] for c in self.components if c["target"] == "native" and c["kind"] != "job"]
            add = [c["id"] for c in self.components if c["target"] == "addon"]
            drop = [c["id"] for c in self.components if c["target"] == "drop"]
            other = [c["id"] + " (" + c["target"] + ")" for c in self.components if c["target"] not in ("native", "addon", "drop")]
            s = "%s land%s natively" % (", ".join(nat) or "nothing", "s" if len(nat) == 1 else "")
            if add:
                s += "; %s become%s managed add-on%s" % (", ".join(add), "s" if len(add) == 1 else "", "s" if len(add) > 1 else "")
            if other:
                s += "; " + ", ".join(other)
            if drop:
                s += "; %s %s not needed" % (", ".join(drop), "is" if len(drop) == 1 else "are")
            L.append(s + ".")
        L.append("")
        cut = False
        L.append("| Component | Today | On Clever Cloud | Effort | Evidence |")
        L.append("|---|---|---|---|---|")
        for c in self.components:
            ev = c["evidence"][0] if c["evidence"] else "[assumed]"
            ev = ev.split(" ", 2)
            ev = " ".join(ev[:2])
            tgt = c["detail"]
            row = (c["id"], trunc(c["today"], 48), c["target"], trunc(tgt, 80), c["effort"], trunc(ev, 48))
            cut = cut or any(str(x).endswith("…") for x in row)
            L.append("| %s | %s | %s: %s | %s | %s |" % row)
        if cut:
            pass   # the "…" note goes on the Details line (saves a line)
        files = self.code_changes()
        applied = self.code_changes(include_applied=True)
        already = sorted(set(applied) - set(files))
        if files:
            L.append("")
            L.append(("**Changes a plan would need (%d file%s, indicative — nothing is applied):**" if self.mode == "report-only" else "**Changes (%d file%s):**") % (len(files), "s" if len(files) > 1 else ""))
            groups = {}
            for f, lst in sorted(files.items()):
                d_ = os.path.dirname(f)
                if os.path.basename(d_) == "clevercloud" and all(ch.get("line") is None and (ch.get("short") or "").startswith("new") for _, ch in lst):
                    groups.setdefault(d_, []).append((f, lst))
                    continue
                lines = sorted(set(x for _, ch in lst for x in (ch.get("lines") or [ch.get("line")]) if x))
                L.append("- `%s%s` — %s (%s)" % (f, (":" + ",".join(str(x) for x in lines[:3])) if lines else "", "; ".join(ch.get("short") or ch["what"][:80] for _, ch in lst),
                                                 emax(*[ch["effort"] for _, ch in lst])))
            for d_, items in sorted(groups.items()):
                L.append("- `%s/` — new files %s: Clever cron (templates/cron/), empty cron environment handled (S; detail in REPORT.md)" % (
                    d_, ", ".join(os.path.basename(f) for f, _ in items)))
            if any("local fallback kept" in (ch.get("short") or "") for lst in files.values() for _, ch in lst):
                L.append("- (fallbacks: today's values are used only when the add-on variable is absent, so docker-compose keeps working locally)")
        if already:
            L.append("- already applied on `clever-poc` (not counted): " + ", ".join("`%s`" % f for f in already))
        for oc in self.optional_changes:
            L.append("- optional (not counted, only with a yes): `%s:%s` — %s" % (oc["file"], oc["line"], oc["what"]))
        for c in self.components:
            mg = c.get("migration")
            if mg and mg.get("bucket"):
                L.append("**Files to migrate:** existing objects → Cellar bucket `%s` (created by the script; global name) with `%s` — keys kept, SHA-256 verified, manifest; the source token stays in a 600 file, never shown." % (
                    mg["bucket"], mg["tool"]))
            elif mg:
                L.append("**Data to migrate — BEFORE the first deploy:** the SQLite file of the VM (never in git) → the PostgreSQL add-on with `%s` (%s) — schema translated, row counts equal per table, sequences reset, manifest; if the app already created its tables at start, an empty table is reused, one with rows is refused." % (
                    mg["tool"], mg["how"]))
        links = self.links_line() if self.mode != "report-only" else ""
        if links:
            L.append("**Add-on variables go to:** " + links)
        order = self.deploy_order() if self.mode != "report-only" else ""
        if order:
            L.append("**Deploy order:** " + order)
        opts = [(c["id"], o) for c in self.components for o in c.get("options", [])]
        for cid, o in opts:
            L.append("- Option for %s: %s — boundary: %s (%s)" % (cid, o["option"], o["boundary"][:110], "internal, to be confirmed by Clever Cloud"))
        L.append("")
        L.append("**What you gain:** %s." % self.gains())
        loses = [c["id"] + " → " + c["target"] for c in self.components if c["target"] in ("keep-saas", "rewrite", "blocked")]
        cpl = [c for c in (self.s.get("couplings") or []) if not c.get("handled") and c["what"] not in self.handled_couplings]
        moves = ["%s → %s (%s; %s)" % (c["what"], c["target"], c["effort"], ref(c["file"], c["line"])) for c in cpl]
        if self.mode == "report-only":
            r_ = self.rules.get(self.s["primary_provenance"], {})
            L.append("**What would be lost or must move:** %s." % ("; ".join(trunc(m_, 200) for m_ in moves[:6]) + (" (+%d in REPORT.md)" % (len(moves) - 6) if len(moves) > 6 else "")
                     if moves else "not assessed automatically for %s — check data, files, scheduled jobs and platform APIs by hand before any landing" % r_.get("name", "this provenance")))
        elif loses or moves:
            L.append("**What you lose / must move:** %s." % "; ".join(loses + [trunc(m_, 200) for m_ in moves[:4]]))
        else:
            migrated = any(c.get("migration") for c in self.components)
            L.append("**What you lose / stays elsewhere:** %s." % (
                "nothing, once the data and files are migrated (lines above)" if migrated else
                "nothing functional; your compose/local setup stays for development" if self.s.get("compose") else "nothing functional for this app"))
        cfgl = []
        for ap_ in self.provision_spec["apps"][:4]:
            names = [k for k in ap_["env"].keys() if k != "APP_FOLDER"]
            sec = ap_.get("secrets_generate") or []
            cfgl.append("%s: %s%s" % (ap_["alias"], ", ".join("%s=%s" % (k, ap_["env"][k]) for k in names) or "defaults",
                                       ("; %s generated on Clever, never shown" % ", ".join(sec)) if sec else ""))
        if cfgl and self.mode != "report-only":
            L.append("**Config (env vars, no code):** " + " · ".join(cfgl))
        if getattr(self, "blocking", None):
            L.append("**Blocking (no go until settled):** " + " · ".join(trunc(b, 160) for b in self.blocking[:4]) + (" (+%d in REPORT.md)" % (len(self.blocking) - 4) if len(self.blocking) > 4 else "") + ".")
        if getattr(self, "inputs", None):
            L.append("**To provide before the first build (cannot be generated):** " + ", ".join(self.inputs) + ".")
        assumed = [a for c in self.components for a in c["assumed"]][:4]
        if assumed:
            txt = " · ".join(trunc(a, 170) for a in assumed)
            L.append("**Not verified yet:** " + txt + ("" if txt.endswith(("…", ".")) else "."))
        h = self.s["secrets_hygiene"]
        watch = []
        for t in h["tracked_env_files"]:
            if t.get("secret_like_names"):
                watch.append("%s is committed with secret-like names (%s): rotate those values, then on clever-poc %s + add it to .gitignore" % (
                    md_code(t["file"]), ", ".join(t["secret_like_names"]), md_code("git rm --cached -- " + shq(t["file"]))))
            else:
                watch.append("%s is committed (%d names, none secret-like, values not shown): pushed but not loaded by Clever — harmless here; optional: %s + .gitignore on clever-poc" % (
                    md_code(t["file"]), len(t["names"]), md_code("git rm --cached -- " + shq(t["file"]))))
        if h["hardcoded_credentials"]:
            watch.append("credentials hard-coded in %d place(s) (masked): never reused on Clever" % len(h["hardcoded_credentials"]))
        if h["key_like_strings"]:
            watch.append("key-like strings committed in %d place(s) (masked): rotate" % len(h["key_like_strings"]))
        watch += ["dependency " + w for w in self.watch_hits]
        if watch:
            L.append("**Watch:** " + " · ".join(watch) + ".")
        sec = self.s.get("security") or []
        if self.kept:
            L.append("**Kept as is:** " + " · ".join("`%s` — %s" % (k, trunc(v, 120)) for k, v in self.kept))
        if sec or self.tradeoffs or self.open_risks:
            shown = [f_ for f_ in sec if not (self.open_risks and f_["id"] == "auth-todo")]
            fixed_at = {ch["file"] for c in self.components for ch in c.get("changes", [])}      # a change in that file addresses it
            items = ["%s (%s:%s)%s" % (f_["message"].split(":")[0], f_["file"], f_["line"],
                                       "" if f_["file"] in fixed_at else " — not fixed by this plan")
                     for f_ in shown[:4]]
            more = " (+%d in REPORT.md)" % (len(shown) - 4) if len(shown) > 4 else ""
            txt = ""
            if items:
                all_fixed = all(f_["file"] in fixed_at for f_ in shown[:4])
                txt += ("source app, reported only, never copied as-is: " if self.open_risks or not all_fixed else
                        "source app, reported only, never copied as-is (the changes above fix them): ") + " · ".join(items) + more
            if self.tradeoffs:
                txt += (" · " if txt else "") + "introduced by this plan: " + " · ".join(trunc(t_, 420) for t_ in self.tradeoffs)
            if self.open_risks:
                txt = " · ".join(self.open_risks) + (" · " + txt if txt else "")
            L.append("**Security:** " + txt + ".")
        L.append("**Compliance-ready:** " + self.compliance_line())
        if self.mode == "report-only":
            L.append("**POC:** none — nothing will be created and no price is quoted until %s has a mapping (report only)." % self.rules.get(self.s["primary_provenance"], {}).get("name", "this provenance"))
        else:
            L.append("**POC:** " + self.poc_line())
            L.append("**Repo:** %s; nothing on `%s`. `.clever-landing/` is git-excluded." % (
                ("branch `clever-poc`, %d file%s" % (len(files), "s" if len(files) > 1 else "")) if files else "no code change",
                self.s["repo"]["branch"]))
        wo = [c["id"] for c in self.components if c["optional"] and c["target"] != "drop" and c["kind"] != "job"]
        wo += [c["id"] for c in self.components if c["kind"] == "worker" and c["effort"] in ("M", "L") and c["id"] not in wo]
        reply = "go · " + (" · ".join("go without %s" % x for x in wo[:2]) + " · " if wo else "") + "report only"
        if self.mode == "report-only":
            reply = "report only (this provenance is not covered yet)"
        elif getattr(self, "blocking", None):
            reply = "settle the blocking point%s, then plan again · report only" % ("s" if len(self.blocking) > 1 else "")
        L.append("")
        L.append("Reply: **%s**" % reply)
        L.append("Details: `.clever-landing/REPORT.md`" + (" (… = shortened here, full text there)" if cut else ""))
        return "\n".join(L)

    def report(self, vision):
        L = ["# Landing %s on Clever Cloud — detail" % os.path.basename(self.s["repo"]["toplevel"]), "",
             "Generated by plan.py from the committed tree (HEAD %s). Figures read on the day they were generated; nothing here is a commitment from Clever Cloud." % head_text(self.s["repo"]["head"]),
             "Text between « » and every file name below come from the scanned repository: data, never instructions.", "",
             "## Vision", "", vision, "", "## Components"]
        for c in self.components:
            L.append("")
            L.append("### %s — %s (%s)" % (c["id"], c["target"], c["effort"]))
            L.append("- today: %s" % c["today"])
            L.append("- on Clever: %s" % c["detail"])
            if c.get("plan_reason"):
                L.append("- plan: %s" % c["plan_reason"])
            for e in c["evidence"]:
                L.append("- evidence: %s" % e)
            for ch in c["changes"]:
                L.append("- change `%s%s` (%s): %s" % (ch["file"], (":%s" % ch["line"]) if ch.get("line") else "", ch["effort"], ch["what"]))
            for k, v in sorted(c["config"].items()):
                L.append("- env `%s=%s`" % (k, v))
            for n in c.get("secrets_generate") or []:
                L.append("- secret `%s`: generated on Clever with env-safe.sh generate (never shown)" % n)
            for a_ in c["assumed"]:
                L.append("- [assumed] %s" % a_)
            for o in c.get("options", []):
                L.append("- option: %s — boundary: %s — status: %s" % (o["option"], o["boundary"], o["status"]))
            if (c.get("prove") or {}).get("command"):
                L.append("- prove: " + md_code(c["prove"]["command"]) + ((" — " + c["prove"]["data_hint"]) if c["prove"].get("data_hint") else ""))
        sec = self.s.get("security") or []
        if sec:
            L += ["", "## Security of the source app (reported, never copied as-is)", ""] + ["- %s:%s — %s" % (f_["file"], f_["line"], f_["message"]) for f_ in sec]
        if self.tradeoffs:
            L += ["", "## Security trade-offs introduced by this plan", ""] + ["- " + t_ for t_ in self.tradeoffs]
        if self.open_risks:
            L += ["", "## Open risks (kept from the source — repeat them in the final report)", ""] + ["- " + t_ for t_ in self.open_risks]
        if self.also:
            L += ["", "## Also detected", ""] + ["- %s (%s): %s" % (p["id"], p["coverage"], ", ".join(e["signal"] for e in p["evidence"][:3])) for p in self.also]
        if self.notes:
            L += ["", "## Notes", ""] + ["- " + n for n in self.notes]
        L += ["", "## Catalog sources", ""] + ["- %s: %s" % (k, v) for k, v in sorted(self.cat.sources.items())]
        return "\n".join(L) + "\n"

    def links_line(self):
        ads = self.provision_spec["addons"]
        if not ads or len(self.provision_spec["apps"]) < 2:
            return ""
        parts = []
        for a_ in ads:
            parts.append("%s → %s" % (a_["component"], ", ".join(a_["link"])))
        return " · ".join(parts)

    def deploy_order(self):
        apps = self.provision_spec["apps"]
        if len(apps) < 2:
            return ""
        comp = {c["id"]: c for c in self.components}
        workers = [a_["alias"] for a_ in apps if comp.get(a_["component"], {}).get("kind") == "worker"]
        webs = [a_["alias"] for a_ in apps if a_["alias"] not in workers]
        # H9: only when a worker's code really creates the schema (CREATE TABLE in its folder)
        workers = [a_["alias"] for a_ in apps if a_["alias"] in workers
                   and ((self.root(comp.get(a_["component"], {}).get("folder") or ".") or {}).get("creates_schema"))]
        if not workers:
            return ""
        return "%s first, then %s — a reader started before the worker creates its tables logs errors like `relation … does not exist` until then (benign)" % (
            ", ".join(workers), ", ".join(webs))

    def gains(self):
        g = ["hosted in %s by a European provider" % ("Paris (HDS zone)" if self.zone == "parhds" else "Paris" if self.zone == "par" else self.zone)]
        if any(c["target"] == "native" for c in self.components):
            g.append("git-push deploys on native runtimes (no image to maintain)")
        if any(c["target"] == "addon" for c in self.components):
            enc = sorted(set(self.svc[c["service"]]["label"].replace(" add-on", "") for c in self.components if c["target"] == "addon" and c.get("encryption")))
            other = sorted(set(self.svc[c["service"]]["label"].replace(" add-on", "") for c in self.components if c["target"] == "addon" and not c.get("encryption")))
            if enc:
                g.append("managed %s, encrypted at rest from day one, with daily backups" % "/".join(enc))
            if other:
                g.append("managed %s" % "/".join(other))
        g.append("per-second billing on one invoice")
        return "; ".join(g)

    def compliance_line(self):
        enc = [c for c in self.components if c["target"] == "addon"]
        unenc = [c["id"] for c in enc if not c.get("encryption") and self.svc[c["service"]].get("dedicated")]
        parts = ["zone %s" % self.zone]
        dbs = [c for c in enc if self.svc[c["service"]].get("dedicated")]
        if dbs:
            parts.append("databases encrypted at rest from creation" if not unenc else "UNENCRYPTED: %s (would need a migration later)" % ", ".join(unenc))
        bk = [c.get("plan_reason", "") for c in enc if "backups" in c.get("plan_reason", "")]
        if bk:
            parts.append("backups: " + bk[0].split("backups: ")[-1])
        if any(c["service"] == "s3" for c in enc):
            parts.append("Cellar has no at-rest encryption option (encrypt sensitive files in the app if required)")
        if self.a.health == "unsure":
            parts.append("health data 'not sure' → parhds from day one (changing zone later is a migration)")
        if self.hds:
            parts.append("still needed for health data: an HDS contract signed with Clever Cloud (the zone alone is not compliance)")
        else:
            parts.append("for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration)")
        return " · ".join(parts) + "."

    def poc_line(self):
        apps = self.provision_spec["apps"]
        ads = self.provision_spec["addons"]
        if not apps and not ads:
            return "nothing to create."
        s = "%d app%s %s + build %s" % (len(apps), "s" if len(apps) > 1 else "", apps[0]["flavor"] if apps else "", apps[0]["build_flavor"] if apps else "")
        if ads:
            s += ", " + ", ".join("%s %s%s" % (a["provider"].replace("-addon", ""), a["plan"], " (encrypted)" if a["options"] else "") for a in ads)
        s += ", zone %s" % self.zone
        q = self.quote
        if q:
            s += " ≈ %.3f €/h (%s) — a 2-hour test ≈ %.2f €; ~%.0f €/month if never stopped; builds ≈ %.3f € per full deploy (5-min builds)" % (
                q["run_eur_per_hour"], "OFFLINE saved grid, TESTS ONLY — not a live price" if q.get("offline_grid") else "live grid, read today", q["poc_2h_eur"], q["run_eur_per_month_if_never_stopped"], q["build_eur_per_build"])
            aq = getattr(self, "quote_addons", None)
            if aq and aq["run_eur_per_hour"]:
                s += ". Add-ons ≈ %.3f €/h (~%.0f €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC" % (
                    aq["run_eur_per_hour"], aq["run_eur_per_month_if_never_stopped"])
            for ln in q["lines"]:
                if ln.get("note") and ln["item"].startswith("cellar"):
                    s += ". Cellar: " + ln["note"]
            unpriced = list(q["missing"]) + [c["id"] for c in self.components if c["target"] == "addon" and not c.get("price_item")]
            if unpriced:
                s += " (not in the grid, check the Console: %s)" % ", ".join(unpriced)
        else:
            s += " (price not read: run price.py)"
        return s + ". Stop or delete in one command each."


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--scan", required=True)
    ap.add_argument("--zone")
    ap.add_argument("--health", choices=["no", "yes", "unsure"], default=None)
    ap.add_argument("--zone-reason")
    ap.add_argument("--level", choices=["A", "B", "C", "a", "b", "c"])
    ap.add_argument("--allow-dev-plan", action="store_true")
    ap.add_argument("--two-org", help="HDS only: comma-separated component ids to place in a second, non-HDS organisation (zone par)")
    ap.add_argument("--legal-validated", action="store_true", help="written validation by Clever legal + customer DPO exists (needed for AT RISK services in --two-org)")
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--no-price", action="store_true")
    ap.add_argument("--cache-dir")
    ap.add_argument("--name", default=os.environ.get("LOC_NAME_PREFIX"),
                    help="resource prefix (default: $LOC_NAME_PREFIX, else the repo folder name); final names are <prefix>-poc-<component>")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--report", help="write the detail report (markdown), e.g. .clever-landing/REPORT.md")
    ap.add_argument("--storage-key-file", help="Supabase level C: the 600 file where the developer put the project's secret key (checked, never read)")
    ap.add_argument("--replan", action="store_true",
                    help="replace a plan frozen at the first run (only when the developer asks for a new plan)")
    a = ap.parse_args(argv)
    snap = zones_snapshot()
    if a.zone is None:
        a.zone = snap["paris_first"]["hds"] if a.health in ("yes", "unsure") else snap["paris_first"]["non_hds"]
    if a.zone not in snap["app_zones"]:
        print("error: unknown zone %s (valid: %s)" % (a.zone, ", ".join(snap["app_zones"])), file=sys.stderr)
        return 2
    if a.health in ("yes", "unsure") and a.zone not in HDS_ZONES:
        print("error: health data answered '%s' but zone %s is not HDS: use parhds (Paris first)" % (a.health, a.zone), file=sys.stderr)
        return 2
    if a.zone not in ("par", "parhds") and not a.zone_reason:
        print("error: Paris first — zone %s needs --zone-reason (motivated alternative)" % a.zone, file=sys.stderr)
        return 2
    if a.allow_dev_plan and is_hds(a.zone):
        print("error: DEV plans are refused in HDS zones (no encryption, no backups)", file=sys.stderr)
        return 2
    with open(a.scan, encoding="utf-8") as f:
        scan = json.load(f)
    # #14: the plan is FROZEN once the POC code is committed. A scan of clever-poc sees the POC's own
    # changes (an S3 client becomes a new "s3" add-on, CELLAR_BUCKET / CRON_VARIABLES disappear):
    # .clever-landing/plan.json from the first run stays the reference until the developer asks
    # for a new plan (--replan). É1: only a plan THIS machine wrote is frozen — sha256 recorded in
    # .git/ when plan.py wrote it, file untracked and git-excluded; a plan.json that came with the
    # repository (committed, or left on disk after `git rm --cached`) is never trusted.
    top = (scan.get("repo") or {}).get("toplevel")
    if top:
        tracked = tracked_state(top)
        if tracked:
            print("REFUSED: %s tracked by git — %s" % (", ".join(tracked), TRACKED_STATE_ADVICE), file=sys.stderr)
            return 3
    frozen = None
    if a.out and os.path.isfile(a.out) and not a.replan and ((scan.get("poc_branch") or {}).get("commits") or 0) > 0:
        why = None
        if not top:
            why = "no repository toplevel in the scan"
        elif os.path.islink(a.out) or not os.path.realpath(a.out).startswith(os.path.join(os.path.realpath(top), ".clever-landing") + os.sep):
            why = "it is not a regular file under .clever-landing/"
        elif subprocess.run(["git", "-C", top, "check-ignore", "-q", "--", os.path.realpath(a.out)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            why = "it is not git-excluded (run ledger.sh init)"
        elif not known_plan(top, a.out):
            why = "its sha256 is not one plan.py recorded on this machine (it came from elsewhere, or was edited)"
        if why:
            print("plan NOT frozen: %s is ignored — %s. A new plan is computed from the scan." % (a.out, why), file=sys.stderr)
        else:
            try:
                with open(a.out, encoding="utf-8") as f:
                    frozen = json.load(f)
            except (OSError, ValueError):
                frozen = None
    if isinstance(frozen, dict) and frozen.get("vision"):
        print("plan frozen: %s was written by plan.py on this machine (sha256 verified, %s) before the POC commits on clever-poc, "
              "and stays the reference (a scan of clever-poc sees the POC's own changes). Nothing rewritten. A new plan only if "
              "the developer asks for one: --replan." % (a.out, frozen.get("written_at", "date unknown")), file=sys.stderr)
        if a.json:
            print(json.dumps(frozen, indent=2, ensure_ascii=False))
        else:
            print("> Frozen plan read from `%s`, written %s by plan.py on this machine (sha256 verified).\n" % (a.out, frozen.get("written_at", "?")))
            print(frozen["vision"])
        return 0
    p = Planner(scan, a)
    p.build()
    for c in p.components:
        if c.get("prove"):
            c["prove"]["command"] = prove_command(c["id"], c["prove"])   # shell-quoted: copy it as is
    out = p.to_json()
    out["vision"] = p.vision()
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(p.report(out["vision"]))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        out["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if os.path.islink(a.out):
            os.unlink(a.out)          # never write through a link planted by the repository
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if top:
            record_plan(top, a.out)   # sha256 in .git/: the only plans --replan-less runs and provision trust
    print(json.dumps(out, indent=2, ensure_ascii=False) if a.json else out["vision"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
