#!/usr/bin/env python3
"""questions.py — screen 2: the pre-filled questions, and the answers file they fill.

Usage:
  python3 -I questions.py --scan SCAN.json [--answers ANSWERS.json] [--after-poc] [--json] [--out QUESTIONS.md]

  --scan        output of scan.sh --out (.clever-landing/scan.json)
  --answers     .clever-landing/answers.json, written after the developer's replies (reference/answers-schema.md).
                A value outside the schema or an unknown key is refused (exit 2); a missing file means "no answers yet"
  --after-poc   also ask the questions of phase after_poc (who operates it, DNS, deadline, legal entity): never before the POC
  --json        print {"questions": [...], "answers": <effective answers>, "doubts": [...]} instead of the markdown
  --out         also write the markdown to this file (.clever-landing/questions.md)

The questions, in order (ask=true means "put it to the developer now"):
  health               health data? (yes / no / not sure)
  hds_contract_signed  sub-question of health, only after yes or not sure; not counted in the limits below
  production           what really runs in production: asked ONLY when the scan doubts (a free-tier plan declared, several
                       deployment paths, an example repository); comes with commands to paste to measure the real sizes
  load                 peak, shape, reachable between two peaks
  service              tolerated downtime, acceptable data loss (RPO), 24/7 need
  ops                  after the POC only (--after-poc): who operates it, DNS, deadline, legal entity
At most 5 asked in total, 3 before the price (4 when `production` is asked). Every question the developer does not answer
becomes an assumption named in effective_answers()["assumed"] and shown as [assumed]: never a silent value.
The pre-fill is a suggestion read from the scan ([read]); it is not an answer and is never used as one.
Nothing here reads a secret: the measuring commands hold variable NAMES only.
Repository text is shown as on screens 1, 3 and 4: the markdown is finished by lib/mdsafe.py's harden(), every «…» fragment an inline code
span of its defanged text; the fenced block of commands to paste stays exact.

Exit 0 on success, 2 on usage error or invalid answers, 3 when .clever-landing/ is committed (the repository's word, not ours).
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
from common import CONTROL_RX, ENV_NAME_RX, TRACKED_STATE_ADVICE, services, shq, tracked_state, untrusted  # noqa: E402
from mdsafe import harden  # noqa: E402

HEALTH = ("yes", "no", "unsure")
YESNO = ("yes", "no")
SHAPES = ("steady", "predictable_peaks", "events")
DOWNTIMES = ("minutes", "hours", "day")
ANSWER_KEYS = ("health", "hds_contract_signed", "data_gb", "files_gb", "declared_plans", "load", "service", "after_poc")
SUB_KEYS = {
    "load": ("peak_rps", "shape", "reachable_between_peaks"),
    "service": ("rpo_hours", "downtime", "h24"),
    "after_poc": ("operator", "dns", "deadline", "legal_entity"),
}
DEFAULT_SERVICE = {"rpo_hours": 24, "downtime": "hours", "h24": False}
DEFAULT_SHAPE = "steady"
MAX_ANSWERS_BYTES = 65536
MAX_TEXT = 200
MAX_PLANS = 20

DB_KINDS = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB", "sqlite": "SQLite"}
FREE_TIER = re.compile(r"(^|[:\-_ ])(free|hobby-dev|sandbox|trial)([\-_ ]|$)", re.I)
HEALTH_DEP = re.compile(r"(^|[^a-z])(fhir|hl7|dicom|openehr)([^a-z]|$)", re.I)
UPLOAD_LITERAL = re.compile(r"""["']([A-Za-z0-9_./-]*(?:upload|media|attachment|storage|files)[A-Za-z0-9_./-]*)["']""", re.I)
SAFE_PATH = re.compile(r"\A/?[A-Za-z0-9_.][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)*\Z")      # never a leading dash: not an option of du
PREFERRED_DB_VARS = {
    "postgresql": ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "POSTGRES_URI", "DB_URL", "DB_URI"),
    "mongodb": ("MONGODB_URI", "MONGO_URL", "MONGO_URI", "MONGODB_URL"),
}
LOOSE_DB_VAR = {"postgresql": re.compile(r"\A(DATABASE|POSTGRES|POSTGRESQL|DB|PG)[A-Z0-9_]*(URL|URI)\Z"),
                "mongodb": re.compile(r"\AMONGO[A-Z0-9_]*(URL|URI)\Z")}

TEXT = {
    "health": "Does this application handle health data? (yes / no / not sure)",
    "hds_contract_signed": ("Is an HDS contract with Clever Cloud already signed? (yes / no)\n"
                            "Until it is signed: no real health data. The POC runs in zone `par` with synthetic data; the HDS zone is `parhds`.\n"
                            "HDS hosting: €200/month per HDS organisation + 1.4 × the resources placed in it (apps, add-ons, builds, Cellar); a second, non-HDS "
                            "organisation stays at grid prices — communicated by Clever Cloud on 2026-10-27, not on the public pricing page — confirm on your quote/contract"),   # R37: pricing.TEXT_HDS
    "production": "What really runs in production? Today's plans, the real data size (GB, not the plan's limit), the volume of files (GB).",
    "load": "Load: peak requests per second, shape (steady / predictable_peaks / events), reachable between two peaks? (yes / no)",
    "service": "Service level: tolerated downtime (minutes / hours / day), acceptable data loss in hours (RPO), 24/7 needed? (yes / no)",
    "ops": "After the POC: who operates it, who holds the DNS, the deadline, the legal entity that signs.",
}


# ------------------------------------------------------------------------------------------ answers.json
def _is_num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v) and v >= 0
    except OverflowError:
        return False


def _is_text(v):
    return isinstance(v, str) and len(v) <= MAX_TEXT


def _key_text(k):
    return CONTROL_RX.sub(" ", str(k))[:40]


def validate_answers(data):
    """Problems with an answers.json content, as short messages that never repeat a value (list; empty = valid)."""
    if not isinstance(data, dict):
        return ["the file must hold one JSON object"]
    errs = []
    for k in data:
        if k not in ANSWER_KEYS:
            errs.append("unknown key %r (allowed: %s)" % (_key_text(k), ", ".join(ANSWER_KEYS)))
    # null is "the developer does not know" (answers-schema.md): an unanswered question, its documented default shown [assumed]
    if "health" in data and data["health"] is not None and data["health"] not in HEALTH:
        errs.append("health: one of %s, or null" % " / ".join(HEALTH))
    if "hds_contract_signed" in data and data["hds_contract_signed"] not in YESNO + (None,):
        errs.append("hds_contract_signed: yes / no / null")
    for k in ("data_gb", "files_gb"):
        if k in data and data[k] is not None and not _is_num(data[k]):
            errs.append("%s: a number >= 0 or null" % k)
    if "declared_plans" in data:
        p = data["declared_plans"]
        if not (isinstance(p, list) and len(p) <= MAX_PLANS and all(_is_text(x) for x in p)):
            errs.append("declared_plans: a list of at most %d strings (%d characters each)" % (MAX_PLANS, MAX_TEXT))
    for blk, subs in SUB_KEYS.items():
        if blk not in data:
            continue
        d = data[blk]
        if not isinstance(d, dict):
            errs.append("%s: an object" % blk)
            continue
        for k in d:
            if k not in subs:
                errs.append("%s: unknown key %r (allowed: %s)" % (blk, _key_text(k), ", ".join(subs)))
    ld, sv, ap = (data.get(k) if isinstance(data.get(k), dict) else {} for k in ("load", "service", "after_poc"))
    if "peak_rps" in ld and ld["peak_rps"] is not None and not _is_num(ld["peak_rps"]):
        errs.append("load.peak_rps: a number >= 0 or null")
    if "shape" in ld and ld["shape"] is not None and ld["shape"] not in SHAPES:
        errs.append("load.shape: one of %s, or null" % " / ".join(SHAPES))
    if "reachable_between_peaks" in ld and ld["reachable_between_peaks"] is not None and not isinstance(ld["reachable_between_peaks"], bool):
        errs.append("load.reachable_between_peaks: true / false / null")
    if "rpo_hours" in sv and sv["rpo_hours"] is not None and not _is_num(sv["rpo_hours"]):
        errs.append("service.rpo_hours: a number >= 0 or null")
    if "downtime" in sv and sv["downtime"] is not None and sv["downtime"] not in DOWNTIMES:
        errs.append("service.downtime: one of %s, or null" % " / ".join(DOWNTIMES))
    if "h24" in sv and sv["h24"] is not None and not isinstance(sv["h24"], bool):
        errs.append("service.h24: true / false / null")
    for k, v in ap.items():
        if k in SUB_KEYS["after_poc"] and v is not None and not _is_text(v):
            errs.append("after_poc.%s: a string of at most %d characters" % (k, MAX_TEXT))
    return errs


def _fail(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(2)


def _no_constant(name):
    raise ValueError("constant %s is not JSON" % name)


def load_answers(path):
    """Read and validate answers.json. An invalid value, an unknown key or an unreadable file ends the run with exit code 2;
    a file that does not exist yet means no answers (the caller then shows every default as [assumed])."""
    if not os.path.exists(path):
        print("note: %s not found: no answers yet, every default is shown as [assumed]" % path, file=sys.stderr)
        return {}
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_ANSWERS_BYTES + 1)
    except OSError as exc:
        _fail("cannot read %s: %s" % (path, exc.strerror or "error"))
    if len(raw) > MAX_ANSWERS_BYTES:
        _fail("%s is larger than %d bytes" % (path, MAX_ANSWERS_BYTES))
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except ValueError:
        _fail("%s is not valid JSON" % path)
    errs = validate_answers(data)
    if errs:
        _fail("%s is invalid (schema: reference/answers-schema.md): %s" % (path, "; ".join(errs)))
    return data


def _num(v):
    return v if _is_num(v) else None


def effective_answers(scan, answers):
    """The answers completed with the documented defaults, plus `assumed`: the names of the fields that are a default or
    unknown, so every screen can label them [assumed]. Never fails: a value outside the schema counts as not answered.

    health unanswered -> "unsure" (the safe side); hds_contract_signed unanswered while health is not "no" -> "no";
    service -> 24 h RPO, hours of downtime, no 24/7; load.shape -> steady. data_gb / files_gb stay None when unknown
    (the sizing states the figure it assumes) and are named in `assumed` when the scan shows a database / files. `service_assumed` lists the fields of
    the service level that took their default (rpo_hours, downtime, h24)."""
    a = answers if isinstance(answers, dict) else {}
    facts = read_facts(scan)
    out, assumed = {}, []
    out["health"] = a["health"] if a.get("health") in HEALTH else "unsure"
    if a.get("health") not in HEALTH:
        assumed.append("health")
    contract = a.get("hds_contract_signed") if a.get("hds_contract_signed") in YESNO else None
    if contract is None and out["health"] != "no":
        contract = "no"
        assumed.append("hds_contract_signed")
    out["hds_contract_signed"] = contract
    for key, present in (("data_gb", facts["databases"]), ("files_gb", facts["files"])):
        out[key] = _num(a.get(key))
        if out[key] is None and present:
            assumed.append(key)
    plans = a.get("declared_plans")
    out["declared_plans"] = [str(x)[:MAX_TEXT] for x in plans[:MAX_PLANS] if isinstance(x, str)] if isinstance(plans, list) else []
    ld = a.get("load") if isinstance(a.get("load"), dict) else {}
    shape = ld.get("shape") if ld.get("shape") in SHAPES else None
    reach = ld.get("reachable_between_peaks")
    out["load"] = {"peak_rps": _num(ld.get("peak_rps")), "shape": shape or DEFAULT_SHAPE,
                   "reachable_between_peaks": reach if isinstance(reach, bool) else None}
    if shape is None:
        assumed.append("load")
    sv = a.get("service") if isinstance(a.get("service"), dict) else {}
    service = {"rpo_hours": sv["rpo_hours"] if _is_num(sv.get("rpo_hours")) else DEFAULT_SERVICE["rpo_hours"],
               "downtime": sv["downtime"] if sv.get("downtime") in DOWNTIMES else DEFAULT_SERVICE["downtime"],
               "h24": sv["h24"] if isinstance(sv.get("h24"), bool) else DEFAULT_SERVICE["h24"]}
    out["service"] = service
    # RT D7: which fields of the service level are a default — the price screen words its assumption per missing field
    out["service_assumed"] = [k for k, given in (("rpo_hours", _is_num(sv.get("rpo_hours"))), ("downtime", sv.get("downtime") in DOWNTIMES),
                                                 ("h24", isinstance(sv.get("h24"), bool))) if not given]
    if out["service_assumed"]:
        assumed.append("service")
    ap = a.get("after_poc") if isinstance(a.get("after_poc"), dict) else {}
    out["after_poc"] = {k: ap[k] for k in SUB_KEYS["after_poc"] if _is_text(ap.get(k))}
    out["assumed"] = [k for k in ("health", "hds_contract_signed", "data_gb", "files_gb", "load", "service") if k in assumed]
    return out


# ------------------------------------------------------------------------------------------ what the scan says
def _list(v):
    return v if isinstance(v, list) else []


def _dict(v):
    return v if isinstance(v, dict) else {}


def _where(h):
    """file:line from the scan, as data (a question's source); every text that shows it wraps it with untrusted(): a fragment, never markup."""
    h = _dict(h)
    if not h.get("file"):
        return None
    return "%s:%s" % (_tok(h["file"], 100), h["line"]) if isinstance(h.get("line"), int) and h["line"] > 0 else _tok(h["file"], 100)


def _human(n):
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return "%.1f %s" % (n / float(size), unit) if n < 10 * size else "%d %s" % (n // size, unit)
    return "%d B" % n


def _tok(v, n=30):
    """A short token from the scan (a plan, a service type), control characters removed."""
    return CONTROL_RX.sub(" ", str(v))[:n]


def _safe_path(p):
    p = str(p)
    return p if len(p) <= 120 and SAFE_PATH.match(p) and ".." not in p.split("/") else None


def read_facts(scan):
    """What the scan says, in the shape the questions need. Every key of the scan may be missing."""
    scan = _dict(scan)
    roots = [r for r in _list(scan.get("roots")) if isinstance(r, dict)]
    declared = _dict(scan.get("declared"))
    svc = services()
    f = {"databases": [], "files": False, "file_evidence": None, "dumps": [], "plans": [], "free_plans": [], "doubts": [],
         "crons": [], "env_names": [], "upload_dirs": [], "s3": False, "health_hint": None}

    def add_db(kind, label, evidence):
        if kind not in [d["kind"] for d in f["databases"]]:
            f["databases"].append({"kind": kind, "label": label, "evidence": evidence})

    stores = _dict(scan.get("datastores_in_code"))
    for kind in ("postgresql", "mysql", "mongodb"):
        hits = _list(stores.get(kind))
        if hits:
            add_db(kind, DB_KINDS[kind], _where(hits[0]))
    comp = _dict(scan.get("compose"))
    for s in _list(comp.get("services")):
        img = _dict(s).get("image")
        if not isinstance(img, str):
            continue
        for kind in ("postgresql", "mysql", "mongodb"):
            if any(re.search(rx, img) for rx in _dict(svc.get(kind)).get("images", [])):
                add_db(kind, DB_KINDS[kind], _where({"file": comp.get("file"), "line": s.get("line")}))
    for r in roots:
        uses = _list(r.get("sqlite_uses"))
        if uses:
            add_db("sqlite", DB_KINDS["sqlite"], _where(uses[0]))
    for src in ("scalingo_json", "app_json"):
        sj = _dict(scan.get(src))
        for a in _list(sj.get("addons")):
            low = str(a).lower()
            for kind, key in (("postgresql", "postgres"), ("mysql", "mysql"), ("mongodb", "mongo")):
                if key in low:
                    add_db(kind, DB_KINDS[kind], sj.get("file"))
            f["plans"].append({"source": str(sj.get("file") or src), "text": "add-on %s" % untrusted(a, 50), "plan": _tok(a, 60)})
        for proc, spec in sorted(_dict(sj.get("formation")).items()):
            spec = _dict(spec)
            size = spec.get("size")
            if size:
                f["plans"].append({"source": str(sj.get("file") or src), "text": "%s size %s" % (untrusted(proc, 20), untrusted(size, 20)), "plan": _tok(size, 40)})
    if scan.get("supabase"):
        add_db("postgresql", "PostgreSQL (Supabase)", "supabase/")
    for p in _list(declared.get("plans")):
        p = _dict(p)
        if not p.get("plan"):
            continue
        if p.get("kind") in ("postgres", "postgresql"):
            add_db("postgresql", DB_KINDS["postgresql"], _tok(p.get("source"), 40))
        f["plans"].append({"source": _tok(p.get("source"), 40), "text": "%s %s: %s" % (_tok(p.get("kind") or "?", 20), untrusted(p.get("name") or "?", 30), untrusted(p.get("plan"), 40)),
                           "plan": _tok(p.get("plan"), 40)})
    for d in _list(declared.get("dumps")):
        d = _dict(d)
        if d.get("file") and isinstance(d.get("bytes"), int):
            f["dumps"].append({"file": _tok(d["file"], 120), "bytes": d["bytes"], "kind": d.get("kind")})
            if d.get("kind") == "sqlite" or str(d["file"]).lower().endswith((".db", ".sqlite", ".sqlite3")):
                add_db("sqlite", DB_KINDS["sqlite"], _tok(d["file"], 100))
    f["dumps"].sort(key=lambda d: (-d["bytes"], d["file"]))
    for p in f["plans"]:
        if FREE_TIER.search(p["plan"]):
            f["free_plans"].append(p)

    # files kept by the application
    for r in roots:
        uses = _list(r.get("upload_uses"))
        if uses and not f["file_evidence"]:
            f["files"], f["file_evidence"] = True, _where(uses[0])
        for h in uses + _list(r.get("disk_writes")):
            for m in UPLOAD_LITERAL.finditer(str(_dict(h).get("text", ""))):
                p = _safe_path(m.group(1))
                if p and p not in f["upload_dirs"]:
                    f["upload_dirs"].append(p)
        for n in _list(r.get("env_names")) + list(_dict(r.get("env_reads"))):
            if isinstance(n, str) and ENV_NAME_RX.match(n) and n not in f["env_names"]:
                f["env_names"].append(n)
    for c in _list(scan.get("couplings")):
        if _dict(c).get("kind") == "files" and not f["files"]:
            f["files"], f["file_evidence"] = True, _where(c)
    if _list(stores.get("s3")):
        f["files"], f["s3"] = True, True
        f["file_evidence"] = f["file_evidence"] or _where(stores["s3"][0])
    hyg = _dict(scan.get("secrets_hygiene"))
    for e in _list(hyg.get("tracked_env_files")) + _list(hyg.get("env_templates")):
        for n in _list(_dict(e).get("names")):
            if isinstance(n, str) and ENV_NAME_RX.match(n) and n not in f["env_names"]:
                f["env_names"].append(n)
    f["env_names"].sort()

    # scheduled work: a load shape the scan can suggest
    for _ in _list(_dict(scan.get("vercel_json")).get("cron_list")):
        f["crons"].append("vercel.json")
    for c in _list(_dict(scan.get("vm")).get("crontab")):
        f["crons"].append(_tok(_dict(c).get("file") or "crontab", 80))
    for r in roots:
        for s in _list(r.get("schedulers")):
            f["crons"].append(_where(s) or "scheduler")

    # a health-data standard in the dependencies: only a hint, never an answer
    for r in roots:
        deps = _list(_dict(r.get("python")).get("deps")) + _list(_dict(r.get("node")).get("deps"))
        for d in deps:
            if isinstance(d, str) and HEALTH_DEP.search(d):
                f["health_hint"] = "dependency %s" % untrusted(d, 30)
                break

    # doubts: what makes "what really runs in production" worth asking
    if f["free_plans"]:
        p = f["free_plans"][0]
        f["doubts"].append("a free-tier plan is declared (%s, %s)" % (untrusted(p["source"], 40), untrusted(p["plan"], 40)))
    paths = sorted({_tok(p.get("id"), 30) for p in _list(scan.get("provenances")) if isinstance(p, dict) and p.get("confidence") == "strong"
                    and p.get("id") != "supabase" and not (p.get("id") == "docker-compose" and _dict(scan.get("compose")).get("dev_reasons"))})
    if len(paths) >= 2:
        f["doubts"].append("several deployment paths in the repository (%s)" % ", ".join(paths[:4]))
    if any(str(_dict(c).get("what", "")).startswith("DOKKU_SCALE") for c in _list(scan.get("couplings"))):
        f["doubts"].append("DOKKU_SCALE starts only some of the processes (the others run at 0)")
    for h in _list(declared.get("example_hints"))[:1]:
        h = _dict(h)
        f["doubts"].append("looks like an example repository (%s: %s)" % (_tok(h.get("where"), 30), untrusted(h.get("text"), 50)))
    return f


# ------------------------------------------------------------------------------------------ measuring commands
def _db_var(kind, names):
    names = [n for n in names if isinstance(n, str) and ENV_NAME_RX.match(n)]
    for n in PREFERRED_DB_VARS.get(kind, ()):
        if n in names:
            return n
    rx = LOOSE_DB_VAR.get(kind)
    for n in names:
        if rx and rx.match(n):
            return n
    return PREFERRED_DB_VARS[kind][0] if kind in PREFERRED_DB_VARS else None


def measure_commands(facts):
    """Commands the developer pastes where the production data lives, to measure the real sizes. They hold variable NAMES only
    (each one matched against ENV_NAME_RX) and folder names checked against a strict pattern: the answer is a number, never a URL."""
    cmds = []
    kinds = [d["kind"] for d in facts["databases"]]
    names = facts["env_names"]
    if "postgresql" in kinds:
        cmds.append('psql "$%s" -tAc "select pg_size_pretty(pg_database_size(current_database()))"  # the URL, password included, is on the command line (visible to ps): '
                    'a machine nobody else uses' % _db_var("postgresql", names))
    if "mysql" in kinds:
        cmds.append('mysql -h "$MYSQL_HOST" -u "$MYSQL_USER" "$MYSQL_DATABASE" -e "select round(sum(data_length+index_length)/1073741824,3) as gb from information_schema.tables where table_schema=database()"  # the client reads the password from MYSQL_PWD')
    if "mongodb" in kinds:
        cmds.append("echo 'print(db.stats(1073741824).dataSize)' | mongosh \"$%s\" --quiet  # the URL, password included, is on the command line (visible to ps): "
                    "a machine nobody else uses" % _db_var("mongodb", names))
    if "sqlite" in kinds:
        files = [d["file"] for d in facts["dumps"] if d.get("kind") == "sqlite" and _safe_path(d["file"])]
        cmds.append("du -h -- %s" % (shq(files[0]) if files else "/path/to/your/database.db"))
    if facts["s3"]:
        var = next((n for n in names if isinstance(n, str) and ENV_NAME_RX.match(n) and "BUCKET" in n), "S3_BUCKET")
        cmds.append('aws s3 ls "s3://$%s" --recursive --summarize --human-readable | tail -2' % var)
    if facts["files"] and not (facts["s3"] and not facts["upload_dirs"]):
        cmds.append("du -sh -- %s" % (shq(facts["upload_dirs"][0]) if facts["upload_dirs"] else "/path/to/your/uploads"))
    return cmds


# ------------------------------------------------------------------------------------------ the questions
def _q(qid, prefill, source, ask, phase="before_price"):
    return {"id": qid, "text": TEXT[qid], "prefill": prefill, "source": source, "ask": bool(ask), "phase": phase}


def _production_prefill(facts):
    parts, src = [], []
    if facts["databases"]:
        parts.append("database: " + ", ".join("%s (%s)" % (d["label"], untrusted(d["evidence"], 110)) if d["evidence"] else d["label"] for d in facts["databases"][:2]))
        src += [d["evidence"] for d in facts["databases"][:2] if d["evidence"]]
    if facts["dumps"]:
        d = facts["dumps"][0]
        parts.append("largest committed dump: %s (%s)" % (untrusted(d["file"], 50), _human(d["bytes"])))
        src.append(d["file"])
    if facts["files"]:
        parts.append("files kept by the application" + (" (%s)" % untrusted(facts["file_evidence"], 110) if facts["file_evidence"] else ""))
    plans = facts["plans"]
    if plans:
        shown = ", ".join("%s %s" % (untrusted(p["source"], 40), p["text"]) for p in plans[:2])
        parts.append("declared plans: " + shown + (", +%d more" % (len(plans) - 2) if len(plans) > 2 else ""))
        src += sorted({p["source"] for p in plans})
    seen = []
    for s in src:
        if s not in seen:
            seen.append(s)
    return ("; ".join(parts) or None), (", ".join(seen[:4]) or None)


def _answered_summary(a):
    parts = []
    if "declared_plans" in a and isinstance(a["declared_plans"], list):
        parts.append("plans: " + (", ".join(untrusted(x, 40) for x in a["declared_plans"][:3]) or "none given"))
    for k, label in (("data_gb", "data"), ("files_gb", "files")):
        if k in a:
            parts.append("%s: %s" % (label, "%s GB" % a[k] if a[k] is not None else "don't know"))
    return "; ".join(parts)


def build_questions(scan, answers, after_poc=False):
    """The questions of screen 2, in order. Each one: {"id", "text", "prefill", "source", "ask", "phase"}.
    prefill is what the scan suggests ([read]) or the recorded answer (source "answers.json") or the default (source "default");
    ask is true only when the developer must be asked now. after_poc=True is the only way an after_poc question is asked."""
    a = answers if isinstance(answers, dict) else {}
    f = read_facts(scan)
    qs = []
    h = a.get("health")
    if h in HEALTH:
        qs.append(_q("health", h, "answers.json", False))
    else:
        qs.append(_q("health", "unsure" if f["health_hint"] else None, f["health_hint"], True))
    if h in ("yes", "unsure"):
        c = a.get("hds_contract_signed")
        qs.append(_q("hds_contract_signed", (c if c is not None else "don't know") if "hds_contract_signed" in a else None,
                     "answers.json" if "hds_contract_signed" in a else None, "hds_contract_signed" not in a))
    answered = any(k in a for k in ("data_gb", "files_gb", "declared_plans"))
    if answered:
        qs.append(_q("production", _answered_summary(a), "answers.json", False))
    else:
        pre, src = _production_prefill(f)
        qs.append(_q("production", pre, src, bool(f["doubts"])))
    ld = a.get("load")
    if isinstance(ld, dict):
        qs.append(_q("load", "shape: %s, peak: %s req/s" % (ld.get("shape", DEFAULT_SHAPE), ld.get("peak_rps") if ld.get("peak_rps") is not None else "unknown"), "answers.json", False))
    elif f["crons"]:
        qs.append(_q("load", "shape: predictable_peaks (%d scheduled job(s) in %s), peak: unknown" % (len(f["crons"]), untrusted(f["crons"][0], 80)), f["crons"][0], True))
    else:
        qs.append(_q("load", "shape: %s, peak: unknown" % DEFAULT_SHAPE, "default", True))
    sv = a.get("service")
    if isinstance(sv, dict):
        e = effective_answers(scan, a)["service"]
        qs.append(_q("service", "data loss up to %s h, downtime: %s, %s" % (e["rpo_hours"], e["downtime"], "24/7" if e["h24"] else "no 24/7"), "answers.json", False))
    else:
        d = DEFAULT_SERVICE
        qs.append(_q("service", "business hours, data loss up to %d h, downtime: %s, no 24/7" % (d["rpo_hours"], d["downtime"]), "default", True))
    qs.append(_q("ops", None, None, bool(after_poc) and "after_poc" not in a, phase="after_poc"))
    return qs


# ------------------------------------------------------------------------------------------ the screen
def _label(q):
    if q["prefill"] is None:
        return None
    return {"default": "[assumed]", "answers.json": "[answered]"}.get(q["source"], "[read]")


def render_markdown(questions, eff, facts):
    asked = [q for q in questions if q["ask"]]
    L = ["# Questions — confirm or correct what was read (a skipped one stays an assumption)", ""]
    for n, q in enumerate(asked, 1):
        lines = q["text"].split("\n")
        L.append("%d. %s" % (n, lines[0]))
        L += ["   " + x for x in lines[1:]]
        lab = _label(q)
        if lab:
            for part in (q["prefill"].split("; ") if q["id"] == "production" else [q["prefill"]]):
                L.append("   %s %s" % (lab, part) + (" (%s)" % untrusted(q["source"], 60) if q["id"] == "health" and q["source"] not in (None, "default", "answers.json") else ""))
        else:
            L.append("   Nothing in the repository answers this.")
        if q["id"] == "production":
            if facts["doubts"]:
                L.append("   Asked because: " + "; ".join(facts["doubts"]))      # each doubt is bounded and its repository text already closed with »
            cmds = measure_commands(facts)
            if cmds:
                L.append("   Paste where production runs; reply with the numbers only (never a URL or a password):")
                L.append("   ```")          # no language tag: a plain <pre><code> to any renderer
                L += ["   " + c for c in cmds]
                L.append("   ```")
    skipped = [q for q in questions if not q["ask"] and q["phase"] == "before_price" and q["source"] != "answers.json"]
    for q in skipped:
        if q["id"] == "production" and q["prefill"]:
            L.append("Not asked, nothing contradicts the repository: %s [assumed]" % q["prefill"])
    shown = [k for k in eff["assumed"] if k != "hds_contract_signed" or any(q["id"] == k for q in questions)]
    if shown:
        L.append("Left unanswered, these stay [assumed]: %s." % ", ".join(shown))
    if not any(q["id"] == "ops" and q["ask"] for q in questions):
        L.append("Asked after the POC: who operates it, who holds the DNS, the deadline, the legal entity that signs.")
    L.append("Replies go in .clever-landing/answers.json (reference/answers-schema.md), then run this again with --answers.")
    # the documents' constructor (lib/mdsafe.py), as on screens 1, 3 and 4: every «…» fragment a code span of its defanged text, our text between them
    # inert (a file name or a folder name read from the repository included); the fenced block of commands to paste stays exact
    out, fenced = [], False
    for line in L:
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
        else:
            out.append(line if fenced else harden(line))
    return "\n".join(out)


# ------------------------------------------------------------------------------------------ command line
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--scan", required=True)
    ap.add_argument("--answers")
    ap.add_argument("--after-poc", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        with open(a.scan, encoding="utf-8") as f:
            scan = json.load(f)
    except (OSError, ValueError):
        _fail("cannot read a scan from %s (run scan.sh --out first)" % a.scan)
    if not isinstance(scan, dict):
        _fail("%s is not a scan" % a.scan)
    top = _dict(scan.get("repo")).get("toplevel")
    if isinstance(top, str) and top:
        tracked = tracked_state(top)
        if tracked:
            print("REFUSED: %s tracked by git — %s" % (", ".join(tracked), TRACKED_STATE_ADVICE), file=sys.stderr)
            return 3
    answers = load_answers(a.answers) if a.answers else {}
    qs = build_questions(scan, answers, after_poc=a.after_poc)
    eff = effective_answers(scan, answers)
    facts = read_facts(scan)
    md = render_markdown(qs, eff, facts)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        if os.path.islink(a.out):
            os.unlink(a.out)          # never write through a link planted by the repository
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    if a.json:
        print(json.dumps({"questions": qs, "answers": eff, "doubts": facts["doubts"]}, indent=2, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
