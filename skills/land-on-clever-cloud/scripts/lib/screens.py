"""screens.py — what the developer sees (parcours v2, spec section 3): three screens rendered from plan.json.

A pure library: Python 3.9 standard library plus the skill's own common.py (untrusted: repository text between « »), pricing.py (the exact
support / HDS / Premium texts) and mdsafe.py (harden: the constructor the documents use). No network, no subprocess, no file I/O, no print. It receives the plan dict plan.py writes to
.clever-landing/plan.json, tolerates any key being missing or of the wrong type (a plan.json from before this version has no "security"
or "must_move"), and returns plain strings. Screen 2, the questions, is questions.py's.

    screen_read(plan)     -> str   screen 1, "What I read", 15 lines at most
    screen_compat(plan)   -> str   screen 3, compatibility, 35 lines at most
    screen_price(plan)    -> str   screen 4, price + migration sketch + "talk to Clever", 35 lines at most
    render(plan, which)   -> str   which is "read", "compat", "price" or "all" (anything else: ValueError)
    render_all(plan)      -> str   the three screens, one blank line between them (what plan.py prints, and plan.json "vision")
    render_full(plan)     -> str   the same three screens without the line limit (REPORT.md holds them: nothing is left out there)
    hds_line(plan)        -> str   the HDS line of screen 3 (plan.py's detail uses it too)
  each screen function also takes limit=N (default: its limit, LIMITS) for the unlimited text above.

  price_parts(plan, lang, full, health) -> dict    the wording of the price block, ONE copy for screen 4 and for the documents (English or French)
  migration_parts(plan) -> (sketch, lines)         the migration sketch rule, ONE copy
  Public helpers for the documents (deliverables.py), the same functions under names that stay: one_line, repo_span, repo_spans, join_names, money,
  composition, production_text, placement_text, poc_text, evidence_text, blocker_lines, compat_rows, project_name, provenance_name, is_report_only,
  keep_promises (see the last block of this file). poc_text(plan, report_only, lang) is French on request, like price_parts.

The full detail stays in REPORT.md (plan.py --report): when a screen has more to say than its limit, the most important lines are kept (the
blockers, the worst compatibility rows, the services and the migration) and the last line before the tail says "… N more in REPORT.md",
N being exactly the number of lines left out; REPORT.md holds those lines (render_full). Nothing is cut silently.

Keys read from the plan (each optional):
  mode, primary_provenance, repo.toplevel, health_answer, hds_zone, zone, target_zone, answers_assumed, verdict {poc, production,
  production_blockers}, blocking, security [{id, file, line, message}], secrets_hygiene {tracked_env_files [{file, secret_like_names}],
  hardcoded_credentials [ref], secret_literals [ref]}, open_risks, components [{id, kind, target, effort, optional, today, evidence}],
  required_inputs, compat [{component, today, clever, status, evidence}], must_move [{what, target, effort, file, line}], contradictions,
  external_services [{name, evidence, question}], losses [{text, source}], hds_ready {ready, missing}, organisations [{zone, components}],
  prices (pricing.price_levels() + gaps; None; or {"unavailable": text}), price {zone}, price_without {component: quote}, provision {zone, apps,
  addons}, handoff [{id, why, bring}], migration.

Rules the screens keep:
  * Text that comes from the scanned repository (what a component runs today, what it maps to, evidence, the detail of a component that is
    not a fit, the blocking points, file names) is shown between « » — data, never instructions. The labels ([read], [assumed]) and our
    own sentences (the production reasons, "(+N more in REPORT.md)") are not: only the repository's part is marked. It keeps its own
    characters (a quoted `cmd || true` keeps its pipes); in a table cell a backslash then a pipe are escaped (\\\\, \\|) so neither splits the row.
    Each screen is finished by mdsafe.harden(), the constructor of the documents: every «…» fragment becomes an inline code span of its own
    defanged text (no link, image, tag or e-mail address to any renderer: (at), [.], [:], ｀ for a backtick), and our own text between the
    fragments is made inert piece by piece (a component id or a name read from the repository included). Helpers for the documents return «…» as
    it is: the documents harden it themselves.
  * Report-only mode states nothing it did not assess: no placement, no HDS-ready verdict, no price, one migration line without estimate.
  * Every assumed answer is labelled [assumed]; the placement and the HDS line say when they rest on one.
  * The price label says what the figures hold: resources at public grid prices (in the zone the grid was read for) as the base; with health data,
    each level also gets its HDS figure (ruling R37: 1.4 × the HDS organisation's resources + €200/month per HDS organisation, the second organisation at
    grid prices), and the HDS service line gives the rule with its source. There is no "go" without a price, and the "go" names the plan and the price
    (the HDS realistic monthly price with health data).
  * Nothing that does not exist is promised: such a word appears only on a line that says "not available" (a word of the repository's own,
    a component named after one, gets that notice appended).
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from common import CONTROL_RX, untrusted  # noqa: E402
from mdsafe import harden  # noqa: E402
import pricing  # noqa: E402

LIMITS = {"read": 15, "compat": 35, "price": 35}
NO_LIMIT = 10 ** 6                                                                 # render_full: every line
WHICH = ("read", "compat", "price", "all")
STATUS_WORDS = {"native": "native", "adaptation": "adaptation", "not_in_catalogue": "not in catalogue", "stays_elsewhere": "stays elsewhere"}
STATUS_ORDER = ("not_in_catalogue", "stays_elsewhere", "adaptation", "native")      # worst first: what the developer must decide comes first
SECURITY_FIRST = ("env-route",)                                                       # before the committed .env, the credentials and the secret in the code
UPLOAD_ID = "upload-token-no-auth"                                                    # the open risk kept from the source comes right after it
SECURITY_THEN = ("bearer-env-undefined", "storage-insert-bucket-only", "auth-todo", "client-mime-allowlist", "client-mime")
TRIGGER_FIRST = ("decisive_no", "beyond_catalogue", "hds_contract", "hds_ambiguous_service")      # the hand-offs that block, then the others in the given order
ZONE_NAMES = {"par": "Paris (par)"}
HEAD_FIX = "To fix before any real data (does not block the POC):"
HEAD_KEPT = "Kept as in the source, open risk (not fixed by this plan; say so in your report):"
HEAD_PLATFORM = "Blocks the POC until settled:"
LABEL_RX = re.compile(r"\A(\[(?:read|assumed|declared)\])(?:\s+(.*))?\Z", re.S)
HDS_WORDS_RX = re.compile(r"\AHDS: the (?:resources above are priced|HDS figures are)")        # pricing.py's assumption: the head of the production block says it
LEVEL_NAMES = (("floor", "Floor"), ("realistic", "Realistic"), ("ceiling", "Ceiling"))
MAX_NAMES = 6                                                               # component ids named on one line, then "+N more"
MAX_WITHOUT = 2                                                             # "go without X" clauses on the printed Reply, then "… N more options in REPORT.md"
AT_LEAST = ", at least: the total is incomplete"
AT_LEAST_BOTH = " — at least: both totals are incomplete"                 # R39: the grid total and the HDS figure, said once after both
NOT_PROMISED_RX = re.compile(r"byok|automatic failover|gpu", re.I)          # what Clever Cloud does not offer: only on a line that says "not available"


# ------------------------------------------------------------------------------------------------ small readers
def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, (list, tuple)) else []


def _text(v, limit=300):
    """One line of our own or plan text: control characters and runs of blanks become one space, cut with an ellipsis."""
    t = " ".join(CONTROL_RX.sub(" ", str(v)).split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _repo(v, limit=80):
    """Text from the scanned repository, between « », as it is: a quoted command keeps its pipes (`|| true`). A table cell escapes them (_cell)."""
    return untrusted(v, limit)


def _cell(text):
    """One cell of a Markdown table: each pipe escaped (\\|), and only the backslashes right before a pipe doubled, so none of them can undo the pipe's
    escape and split the row (a renderer reads the escape by the parity of the backslashes before it). Any other backslash stays as it is: the cell's
    repository text is a code span, which shows backslashes literally (a\\b.log stays a\\b.log). A text without a pipe is unchanged."""
    return re.sub(r"(\\*)\|", lambda m: m.group(1) * 2 + "\\|", str(text))


def _strs(v):
    return [x for x in _list(v) if isinstance(x, str) and x.strip()]


def _w(full, width):
    """The width of one item: REPORT.md (`full`) holds every item whole, the printed screen cuts it with an ellipsis."""
    return NO_LIMIT if full else width


def _names(items, full, n=MAX_NAMES, width=60, sep=", "):
    """"a, b, c (+4 more in REPORT.md)": the first n of `items` (the whole list, each item whole, when `full`: REPORT.md holds it); the
    suffix is ours and is always there when items were left out. Nothing is cut silently."""
    items = [_text(x, _w(full, width)) for x in items]
    shown = items if full else items[:n]
    return sep.join(shown) + (" (+%d more in REPORT.md)" % (len(items) - n) if not full and len(items) > n else "")


def _repo_list(items, full, n=MAX_NAMES, width=60):
    """Like _names for repository text: each item between « » on its own (the cap is per item, never on the joined text, so the suffix and
    the items after the cap survive); the suffix is ours, outside the marks."""
    items = [str(x) for x in items]
    shown = items if full else items[:n]
    return ", ".join(untrusted(x, _w(full, width)) for x in shown) + (
        " (+%d more in REPORT.md)" % (len(items) - n) if not full and len(items) > n else "")


def _eur(v, places=2):
    """A price from the plan as text; "n/a" for anything that is not a finite number (never a made-up 0.00)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return "n/a"
    return "%.*f" % (places, v)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def _ref(file, line):
    file = str(file)
    return "%s:%s" % (file, line) if isinstance(line, int) and not isinstance(line, bool) and line > 0 else file


def _name(plan):
    top = _dict(plan.get("repo")).get("toplevel")
    base = os.path.basename(str(top).rstrip("/")) if isinstance(top, str) and top.strip("/") else ""
    return _repo(base or "this repository", 40)


def _provenance(plan):
    p = plan.get("primary_provenance")
    return _text(p, 40) if isinstance(p, str) and p else "this provenance"


def _report_only(plan):
    return plan.get("mode") == "report-only"


def _assumed(plan):
    return set(_strs(plan.get("answers_assumed")))


def _evidence(ev):
    """"[read] app.py:3" -> "[read] «app.py:3»": the label is ours, what follows comes from the repository. No label: all between « »."""
    if not isinstance(ev, str) or not ev.strip():
        return "[assumed]"
    m = LABEL_RX.match(ev.strip())
    if m:
        return m.group(1) + ((" " + _repo(m.group(2), 90)) if m.group(2) else "")
    return _repo(ev, 90)


def _label(ev):
    m = LABEL_RX.match(ev.strip()) if isinstance(ev, str) else None
    return m.group(1) if m else "[assumed]"


# ------------------------------------------------------------------------------------------------ the layout
def _layout(limit, sections, tail=()):
    """The lines of a screen. `sections` are in display order: {"prio": int, "cap": int or None, "head": [lines], "items": [lines], "foot": [lines],
    "name": (singular, plural) or None}. A section's head and foot lines show only with at least one item (a table's foot is the blank line that ends
    it: GFM reads a text line right after a table as one more row). The space goes to the sections by priority (lower first, then display
    order): first each one's cap, then what is left up to everything. Whatever does not fit is counted in a last line before the
    `tail` lines (always shown) that names what was left out: "… 2 more assumptions in REPORT.md" (one section), "… 3 more in REPORT.md
    (1 hand-off, 2 assumptions)" (several), "… N more in REPORT.md" (a section with no name); N is the number of item lines left out."""
    order = sorted(range(len(sections)), key=lambda i: (sections[i]["prio"], i))
    total = sum(len(s["items"]) for s in sections)

    def allocate(budget):
        take = [0] * len(sections)
        for phase in (0, 1):
            for i in order:
                s = sections[i]
                n = len(s["items"])
                if n == 0:
                    continue
                want = min(n, s["cap"] if phase == 0 and s.get("cap") else n)
                extra = want - take[i]
                if extra <= 0:
                    continue
                head = len(s["head"]) + len(s.get("foot") or []) if take[i] == 0 else 0
                room = budget - head
                if room < 1:
                    continue
                add = min(extra, room)
                take[i] += add
                budget -= head + add
        return take

    budget = limit - len(tail)
    take = allocate(budget)
    if sum(take) < total:                                  # something is left out: keep one line to say how much
        take = allocate(budget - 1)
    lines = []
    for s, k in zip(sections, take):
        if k:
            lines += s["head"] + s["items"][:k] + list(s.get("foot") or [])
    left = total - sum(take)
    if left:
        cut = [(len(s["items"]) - k, s.get("name")) for s, k in zip(sections, take) if len(s["items"]) > k]
        word = lambda n, nm: (nm[0] if n == 1 else nm[1]) if nm else ("line" if n == 1 else "lines")      # noqa: E731
        if len(cut) == 1:
            lines.append("… %d more %sin REPORT.md" % (left, (word(left, cut[0][1]) + " ") if cut[0][1] else ""))
        else:
            lines.append("… %d more in REPORT.md (%s)" % (left, ", ".join("%d %s" % (n, word(n, nm)) for n, nm in cut)))
    return lines + list(tail)


def _never_promise(lines):
    """A line that names BYOK, automatic failover or GPU (a word of the repository: a component called gpu-worker) and does not say "not
    available" gets that notice: nothing that does not exist is ever promised, whatever the plan carries."""
    out = []
    for line in lines:
        words = sorted({m.group(0).upper() if m.group(0).lower() != "automatic failover" else "automatic failover" for m in NOT_PROMISED_RX.finditer(line)})
        if not words or "not available" in line.lower():
            out.append(line)
            continue
        note = " — %s: not available on Clever Cloud" % ", ".join(words)
        # a table row keeps its closing pipe: the note goes inside the last cell
        out.append(line[:-1].rstrip() + note + " |" if line.startswith("|") and line.endswith("|") else line + note)
    return out


def _section(prio, items, head=(), cap=None, name=None, foot=()):
    return {"prio": prio, "cap": cap, "head": list(head), "items": list(items), "foot": list(foot), "name": name}


# ------------------------------------------------------------------------------------------------ screen 1
def _blockers(plan, full=False):
    """Screen 1's numbered lists, worst first, as (to_fix, kept, platform):
      to_fix    the weaknesses of the source app, security first (a route named /env, a committed .env with secret-like names, credentials
                in the code, a secret written in the code, an anonymous upload, ...): to fix before any real data; they never block the POC;
      kept      the open risks the plan deliberately keeps as in the source (and the finding they come from): said, not asked to be settled;
      platform  the traps of the platform (the blocking points of the POC): the POC does not go until they are settled.
    The production blockers (a component that is not a fit, a database beyond the catalogue) are on screens 3 and 4. Each line says where
    it was read; the repository's words are between « »."""
    sec = [f for f in _list(plan.get("security")) if isinstance(f, dict) and isinstance(f.get("file"), str)]
    open_risks = _strs(plan.get("open_risks"))

    def finding(f):
        return "[read] Security: %s (%s)" % (_text(f.get("message") or f.get("id") or "security finding", _w(full, 240)), _repo(_ref(f["file"], f.get("line")), _w(full, 80)))

    def rank(f):                                 # known weaknesses by severity, the unknown last; the scan's order inside a rank
        i = f.get("id")
        return SECURITY_THEN.index(i) if i in SECURITY_THEN else len(SECURITY_THEN)

    first = [f for f in sec if f.get("id") in SECURITY_FIRST]
    upload = [f for f in sec if f.get("id") == UPLOAD_ID]
    # a kept open risk is the same weakness as the TODO the template left (plan.py hides that finding too)
    later = sorted([f for f in sec if f.get("id") not in SECURITY_FIRST and f.get("id") != UPLOAD_ID and not (open_risks and f.get("id") == "auth-todo")], key=rank)
    fix = [finding(f) for f in first]
    hyg = _dict(plan.get("secrets_hygiene"))
    for t in _list(hyg.get("tracked_env_files")):
        t = _dict(t)
        names = _strs(t.get("secret_like_names"))
        if names and isinstance(t.get("file"), str):
            fix.append("[read] Security: %s is committed with secret-like names (%s): rotate those values" % (
                _repo(t["file"], _w(full, 60)), _repo_list(names, full, 6, 40)))
    creds = _strs(hyg.get("hardcoded_credentials"))
    if creds:
        fix.append("[read] Security: credentials hard-coded in the code, masked (%s): never reused on Clever" % _repo_list(creds, full, 3, 60))
    lits = _strs(hyg.get("secret_literals"))
    if lits:
        fix.append("[read] Security: a secret is written in the code (%s): read it from the environment" % _repo_list(lits, full, 3, 60))
    kept = []
    if open_risks:
        kept = [finding(f) for f in upload] + ["Open risk: %s" % _text(r, _w(full, 200)) for r in open_risks]
    else:
        fix += [finding(f) for f in upload]
    fix += [finding(f) for f in later]
    platform = ["Platform: %s" % _repo(b, _w(full, 200)) for b in _strs(plan.get("blocking"))]
    number = lambda lines: ["%d. %s" % (i + 1, x) for i, x in enumerate(lines)]
    return number(fix), number(kept), number(platform)


def _rows(plan):
    """The bricks of the landing: plan.json "compat", or (a plan.json from before it) the components that are not dropped."""
    rows = [r for r in _list(plan.get("compat")) if isinstance(r, dict)]
    if rows:
        return rows
    return [{"component": c.get("id", "?"), "today": c.get("today", ""), "clever": c.get("detail", ""), "status": "adaptation",
             "evidence": (_strs(c.get("evidence")) or ["[assumed]"])[0]}
            for c in _list(plan.get("components")) if isinstance(c, dict) and c.get("target") != "drop"]


def screen_read(plan, limit=None):
    """Screen 1, "What I read" (15 lines at most): the blockers first, then the components with their proof ([read] with file:line, or
    [assumed]), the contradictions of the repository and the external services."""
    p = _dict(plan)
    title = "## What I read in %s" % _name(p)
    if _report_only(p):
        title += " (report only: %s is not covered yet)" % _provenance(p)
    lim = LIMITS["read"] if limit is None else limit
    full = lim >= NO_LIMIT
    fix, kept, platform = _blockers(p, full)
    comps = []
    for r in _rows(p):
        m = LABEL_RX.match(str(r.get("evidence") or "").strip())
        ref = (m.group(2) or "") if m else ""
        what = _text(r.get("today"), 120) + (" — " + _text(ref, 80) if ref else "")
        comps.append("- %s %s: %s" % (_label(r.get("evidence")), _text(r.get("component"), 40), _repo(what, 130)))
    for m in _list(p.get("must_move")):                      # what the repository does that has no equivalent as is: also read
        m = _dict(m)
        if m.get("what") and isinstance(m.get("file"), str):
            comps.append("- [read] %s: %s" % (_text(m["what"], 90), _repo(_ref(m["file"], m.get("line")), 80)))
    if not comps:
        comps.append("- Nothing in this repository maps to a Clever Cloud component yet.")
    contra = _strs(p.get("contradictions"))
    ext = []
    for e in _list(p.get("external_services")):
        e = _dict(e)
        if isinstance(e.get("name"), str):
            ext.append("External service: %s %s" % (_text(e["name"], 40), _evidence(e.get("evidence"))))
    sections = [
        _section(0, fix, head=[HEAD_FIX], cap=5, name=("point to fix", "points to fix")),
        _section(0, kept, head=[HEAD_KEPT], cap=2, name=("open risk", "open risks")),
        _section(0, platform, head=[HEAD_PLATFORM], cap=4, name=("blocking point", "blocking points")),
        _section(3, comps, cap=3, name=("component read", "components read")),
        _section(1, ["Contradictions: " + _names(contra, full, 4, 160, " · ")] if contra else [], cap=1, name=("contradictions line", "contradictions lines")),
        _section(2, ext, cap=3, name=("external service", "external services")),
    ]
    return harden("\n".join(_never_promise([title] + _layout(lim - 1, sections))))


# ------------------------------------------------------------------------------------------------ screen 3
def hds_line(plan, full=False, lang="en"):
    """The HDS line: "HDS-ready architecture: yes" or "HDS-ready architecture: missing <x>" with health data yes or not sure; the asset
    "can already run in HDS" with health data no; "not assessed (report only)" when nothing was assessed. The skill never qualifies a
    certification: it says what is in place and what is missing. `full`: every missing item, whatever their number (plan.py's detail).
    `lang` "fr" (a document) translates the asset sentence and the suffix about an unanswered question; the "HDS-ready architecture: …" form is a fixed text."""
    fr = lang == "fr"
    p = _dict(plan)
    h = _dict(p.get("hds_ready"))
    zone = _text(p.get("hds_zone") or "parhds", 20)
    ready, missing = h.get("ready"), _names(_strs(h.get("missing")), full, 12, 300, "; ")
    if ready is None:
        return "HDS-ready architecture: not assessed (report only)"
    if p.get("health_answer") in ("yes", "unsure"):
        line = "HDS-ready architecture: " + ("yes" if ready else "missing " + missing)
        return line + ((" (données de santé non renseignées, prises comme « pas sûr » [assumed])" if fr else " (health data not answered, taken as not sure [assumed])")
                       if "health" in _assumed(p) else "")
    if ready:
        return ("peut déjà tourner en HDS (%s) : le même plan y fonctionne, avec un contrat HDS signé avec Clever Cloud" if fr else
                "can already run in HDS (%s): the same plan works there, with an HDS contract signed with Clever Cloud") % zone
    return ("peut déjà tourner en HDS (%s), sauf : %s" if fr else "can already run in HDS (%s), apart from: %s") % (zone, missing)


def _zone_name(zone, hds_zone):
    if zone == hds_zone:
        return "Paris HDS (%s)" % zone
    return ZONE_NAMES.get(zone, _text(zone, 20))


def _placement(p, full=False):
    """Where it would run: the organisation(s) and zone(s) of the plan, marked [assumed] when the health answer is, and "not assessed" in
    report-only mode."""
    if _report_only(p):
        return "Placement: not assessed (report only)"
    hds_zone = p.get("hds_zone") or "parhds"
    orgs = [o for o in _list(p.get("organisations")) if isinstance(o, dict) and isinstance(o.get("zone"), str)]
    orgs = [o for o in orgs if _strs(o.get("components"))]
    if not orgs:
        return "Placement: nothing lands on Clever Cloud in this plan"
    parts = []
    for i, o in enumerate(orgs):
        comps = _names(_strs(o["components"]), full, MAX_NAMES, 40)
        if i == 0:
            parts.append("%s%s — %s" % (_zone_name(o["zone"], hds_zone), " in production" if o["zone"] == hds_zone else "", comps))
        else:
            parts.append("second organisation (not HDS) in %s — %s" % (_zone_name(o["zone"], hds_zone), comps))
    s = "Placement: " + "; ".join(parts)
    health = p.get("health_answer")
    if p.get("target_zone") == hds_zone and health in ("yes", "unsure"):
        s += ". HDS because health data is %s%s" % ("yes" if health == "yes" else "not sure", " [assumed]" if "health" in _assumed(p) else "")
        if p.get("zone") != hds_zone:
            s += "; the POC runs in %s with synthetic data only" % _text(p.get("zone") or "par", 20)
    return s


def _production(text, blockers=()):
    """The production text as plan.py wrote it: "ready", "blocked — <reasons>" or "not assessed — report only (…)". The reasons are our own
    sentences (HDS contract, HDS-ready gaps, PITR, replica), with one exception: a component that is not a fit carries a description built
    from the repository ("<id>: not a fit today — <detail>"): only that detail is shown between « »."""
    t = _text(text, 900)
    for b in blockers:
        head, sep, detail = _text(b, 900).partition(" — ")
        if sep and head + sep + detail in t:
            t = t.replace(head + sep + detail, head + sep + _repo(detail, 160))
    return t


def screen_compat(plan, limit=None):
    """Screen 3, compatibility (35 lines at most): the verdict of the POC and of the production (the production text itself), the table
    (today → Clever Cloud → native | adaptation | not in catalogue | stays elsewhere, with the proof), what you lose, what must move, the
    question on each external service, the placement and the HDS line."""
    p = _dict(plan)
    lim = LIMITS["compat"] if limit is None else limit
    full = lim >= NO_LIMIT
    report_only = _report_only(p)
    v = _dict(p.get("verdict")) if isinstance(p.get("verdict"), dict) else {"poc": p.get("verdict")}
    rows = sorted([r for r in _rows(p)], key=lambda r: STATUS_ORDER.index(r.get("status")) if r.get("status") in STATUS_ORDER else len(STATUS_ORDER))
    table = []
    for r in rows:
        status = "not assessed" if report_only else STATUS_WORDS.get(r.get("status"), _text(r.get("status"), 20))
        table.append("| %s: %s | %s | %s | %s |" % tuple(_cell(x) for x in (_text(r.get("component"), 30), _repo(r.get("today"), 48), _repo(r.get("clever"), 56), status,
                                                                         _evidence(r.get("evidence")))))
    losses = ["- " + _text(l.get("text"), 220) for l in _list(p.get("losses")) if isinstance(l, dict) and l.get("text")]
    moves = []
    for m in _list(p.get("must_move")):
        m = _dict(m)
        if m.get("what"):
            moves.append("- %s → %s (%s) %s" % (_text(m["what"], 90), _text(m.get("target"), 70), _text(m.get("effort"), 6), _evidence("[read] " + _ref(m["file"], m.get("line"))) if m.get("file") else ""))
    inputs = _strs(p.get("required_inputs"))
    ask = ["- %s: %s" % (_text(e.get("name"), 40), _text(e.get("question"), 160)) for e in _list(p.get("external_services")) if isinstance(e, dict) and e.get("name") and e.get("question")]
    assumed = [a for a in _strs(p.get("answers_assumed"))]
    title = "## Compatibility of %s" % _name(p)
    fixed = ["POC verdict: %s" % _text(v.get("poc") or "unknown", 160)]
    fixed.append("Production verdict: %s" % _production(v.get("production") or "unknown", _strs(v.get("production_blockers"))))
    sections = [
        _section(0, fixed, name=("verdict line", "verdict lines")),
        _section(1, table, head=["| Component: today | On Clever Cloud | Status | Evidence |", "|---|---|---|---|"], cap=12, name=("table row", "table rows"), foot=[""]),
        _section(0, losses, head=["What you lose or must do differently:" if not report_only else "What you would lose:"], cap=6, name=("loss", "losses")),
        _section(2, moves, head=["Would have to move (indicative):" if report_only else "Must move:"], cap=3, name=("item to move", "items to move")),
        _section(2, ["To provide before the first build (cannot be generated): " + _repo_list(inputs, full, 8, 40)] if inputs else [], name=("input line", "input lines")),
        _section(2, ask, head=["Which data goes through each external service?"], cap=3, name=("question", "questions")),
        _section(0, [_placement(p, full)], name=("placement line", "placement lines")),
        _section(0, [hds_line(p, full)], name=("HDS line", "HDS lines")),
        _section(3, ["Defaults taken [assumed]: " + _names(assumed, full, 10, 30)] if assumed else [], name=("defaults line", "defaults lines")),
    ]
    return harden("\n".join(_never_promise([title] + _layout(lim - 1, sections))))


# ------------------------------------------------------------------------------------------------ screen 4
def _composition(lines, full=False):
    """"web XS × 2 · postgresql xxs_med": the lines of a level, the ones without a figure marked."""
    parts = []
    for l in _list(lines):
        l = _dict(l)
        t = _text(l.get("text"), 120)
        short = re.sub(r"\s*=\s*[0-9.]+ €/month\Z", "", t.split(" — ")[0])
        parts.append(short if l.get("eur_month") is not None else "%s (no figure)" % short)
    shown = parts if full else parts[:MAX_NAMES] + (["+%d more in REPORT.md" % (len(parts) - MAX_NAMES)] if len(parts) > MAX_NAMES else [])
    return " · ".join(shown) or "nothing to price"


def _go_without(p):
    """The components the developer may leave out ("go without X"): the optional ones that are not dropped, and the workers whose
    change is M or L."""
    comps = [c for c in _list(p.get("components")) if isinstance(c, dict) and isinstance(c.get("id"), str)]
    ids = [c["id"] for c in comps if c.get("optional") and c.get("target") != "drop" and c.get("kind") != "job"]
    return ids + [c["id"] for c in comps if c.get("kind") == "worker" and c.get("effort") in ("M", "L") and c["id"] not in ids]


def _reply(p, prices, full=False):
    """The answer expected after the price (spec, step 5), the only place in the skill that writes it: "go" names the plan and the price
    (the realistic level and the POC per hour), "go without X" names its price, and "report only". Without a price there is no "go":
    the price comes first. With a blocking point of the POC: settle it first.
    "go without X": X's priced lines come off the realistic total. When X has no priced line the total does not move and the clause says
    so (no public price for X). "at least" stays whenever a gap other than X's own remains (another add-on without a price, a missing
    item, PITR or a replica to set up); next to an HDS figure it is said once, after both totals ("— at least: both totals are incomplete"). The printed screen offers MAX_WITHOUT such clauses and counts the others; REPORT.md (`full`) lists
    them all."""
    if _report_only(p):
        return "Reply: report only (this provenance is not covered yet)"
    blocking = _strs(p.get("blocking"))
    if blocking:
        return "Reply: settle the blocking %s, then plan again · report only" % _plural(len(blocking), "point")
    if not (isinstance(prices, dict) and isinstance(prices.get("poc"), dict) and isinstance(prices.get("realistic"), dict)):
        return "Reply: no go yet, the price comes first (run price.py, or wait until the grid can be read) · report only"
    real = _dict(prices.get("realistic"))
    poc = _dict(prices.get("poc"))
    total = real.get("eur_month")
    lines = [l for l in _list(real.get("lines")) if isinstance(l, dict)]
    incomplete = bool(prices.get("incomplete"))
    hds_total = _dict(real.get("hds")).get("eur_month") if p.get("health_answer") != "no" else None      # R37: the go names the HDS realistic monthly price
    hds_poc = _dict(_dict(prices.get("hds")).get("poc")).get("eur_h") if p.get("health_answer") != "no" else None
    go = "go (realistic: %s, %s €/month%s%s; POC ≈ %s €/h%s)" % (                    # "at least" once: after both totals when the HDS figure is shown
        _composition(lines, full), _eur(total), AT_LEAST if incomplete and not _is_num(hds_total) else "",
        ("; with HDS hosting %s €/month%s" % (_eur(hds_total), AT_LEAST_BOTH if incomplete else "")) if _is_num(hds_total) else "",
        _eur(poc.get("eur_h"), 3), (", with HDS hosting ≈ %s €/h" % _eur(hds_poc, 3)) if _is_num(hds_poc) else "")
    alias = {a.get("component"): a.get("alias") for a in _list(_dict(p.get("provision")).get("apps")) if isinstance(a, dict)}
    without = _dict(p.get("price_without"))
    # what is left without a figure, by id (a component, or an item the grid does not hold), and the options that are set up with Clever
    gap_ids = set(_strs(_dict(prices.get("gaps")).get("all"))) | {l.get("id") for l in lines if not _is_num(l.get("eur_month")) and isinstance(l.get("id"), str)}
    set_up = any(("PITR" in a or "replica" in a) for a in _strs(prices.get("assumptions")))
    clauses = []
    item = {c.get("id"): c.get("price_item") for c in _list(p.get("components")) if isinstance(c, dict)}
    for x in _go_without(p):
        mine = {m for m in (x, alias.get(x), item.get(x)) if isinstance(m, str)}       # its ids, and the price item the grid could not find
        priced = [l["eur_month"] for l in lines if l.get("id") in mine and _is_num(l.get("eur_month"))]
        # incomplete for a reason that is not X's alone (another gap, PITR / replica, or a reason nothing names)?
        still = incomplete and bool((gap_ids - mine) or set_up or not gap_ids)
        if not priced or not _is_num(total):
            clauses.append("go without %s (no public price for %s: same realistic total%s)" % (_text(x, 30), _text(x, 30), AT_LEAST if still else ""))
            continue
        h = _dict(without.get(x)).get("run_eur_per_hour")
        hx = ""
        if _is_num(hds_total):          # R37: X's lines come off the HDS figure at their own rate (1.4 in the HDS organisation, 1 in the second one)
            off = sum(l["eur_month"] * (1.0 if l.get("org") == "secondary" else pricing.HDS_UPLIFT) for l in lines if l.get("id") in mine and _is_num(l.get("eur_month")))
            hx = ", with HDS hosting ≈ %s €/month" % _eur(hds_total - off)
        clauses.append("go without %s (realistic ≈ %s €/month%s%s%s)" % (
            _text(x, 30), _eur(total - sum(priced)), hx, ((AT_LEAST_BOTH if hx else AT_LEAST) if still else ""), ("; POC ≈ %s €/h" % _eur(h, 3)) if _is_num(h) else ""))
    shown = clauses if full else clauses[:MAX_WITHOUT]
    more = ["… %d more %s in REPORT.md" % (len(clauses) - MAX_WITHOUT, _plural(len(clauses) - MAX_WITHOUT, "option"))] if not full and len(clauses) > MAX_WITHOUT else []
    return "Reply: " + " · ".join([go] + shown + more + ["report only"])


def _handoff(p):
    items = [h for h in _list(p.get("handoff")) if isinstance(h, dict) and h.get("why")]
    first = [h for tid in TRIGGER_FIRST for h in items if h.get("id") == tid]
    rest = [h for h in items if h not in first]
    return ["- %s%s" % (_text(h["why"], 260), (" Bring: " + _text(h["bring"], 260)) if h.get("bring") else "") for h in first + rest]


def _poc_text(p, report_only, lang="en"):
    """(proves, does not prove): what the POC establishes and what it leaves open. `lang` is "en" (the screens) or "fr" (a document)."""
    fr = lang == "fr"
    health = p.get("health_answer") in ("yes", "unsure")
    if report_only:
        return (("rien pour l'instant — rien n'est créé en mode rapport seul.", "la production, le coût ou la durée de la migration — ce rapport ne les évalue pas.") if fr else
                ("nothing yet — nothing is created in report-only mode.", "production, cost or migration time — this report does not assess them."))
    proves = ("l'application se construit et démarre sur Clever Cloud et fonctionne sur son vrai chemin de code (écriture puis lecture en base, route de santé, contrôles de sécurité)."
              if fr else "the app builds and starts on Clever Cloud and works on its real code path (database write then read, health route, security checks).")
    flavors = sorted({a["flavor"] for a in _list(_dict(p.get("provision")).get("apps")) if isinstance(a, dict) and isinstance(a.get("flavor"), str)})
    size = "/".join(_text(f, 12) for f in flavors) or "nano"
    if fr:
        open_ = "la charge ou le dimensionnement de production (le POC tourne en %s × 1 par application), le volume réel de données, la coupure de la bascule (une répétition chronométrée la mesure)" % size
        if health:
            open_ += ", les données de santé réelles (données synthétiques seulement tant que le contrat HDS n'est pas signé)"
    else:
        open_ = "production load or sizing (the POC runs %s × 1 per app), the real data volume, the cutover downtime (a timed rehearsal measures it)" % size
        if health:
            open_ += ", real health data (synthetic data only until the HDS contract is signed)"
    return proves, open_ + "."


# the words of the price block: (English, French). The screens are English; a document (deliverables.py) asks for French with price_parts(lang="fr"). ONE definition
# of each sentence, so the screen and the documents cannot drift apart.
PRICE_WORDS = {
    "report_only": ("Price: none — report only (%s is not covered yet): nothing is created and no price is quoted.",
                    "Prix : aucun — rapport seul (%s n'est pas encore couvert) : rien n'est créé et aucun prix n'est annoncé."),
    "unavailable_reason": ("prices unavailable (grid unreachable)", "prix indisponibles (grille injoignable)"),
    "unavailable": (": no figure and no approval token until the grid can be read (provision.sh refuses).",
                    " : aucun chiffre ni jeton d'approbation tant que la grille n'est pas lisible (provision.sh refuse)."),
    "not_read": ("Price: not read (--no-price): run price.py for the figures; nothing is approved without them.",
                 "Prix : non lu (--no-price) : lancez price.py pour les chiffres ; rien n'est validé sans eux."),
    "poc": ("POC: ≈ %s €/h — a 2-hour test ≈ %s € (zone %s, billed per second%s)%s",
            "POC : ≈ %s €/h — un test de 2 heures ≈ %s € (zone %s, facturé à la seconde%s)%s"),
    "poc_addons": ("; add-ons keep billing after `clever stop` until deleted", " ; les add-ons restent facturés après `clever stop` tant qu'ils ne sont pas supprimés"),
    "head": ("Production (resources at public grid prices%s as the base; %sPremium is not included):",
             "Production (ressources aux prix publics de la grille%s comme base ; %sPremium n'est pas inclus) :"),
    "head_zone": (" in zone %s", " en zone %s"),
    # R37: the HDS rule (communicated by Clever Cloud on 2026-10-27, not on the public pricing page: the service line says so)
    "hds_clause": ("with HDS hosting: 1.4 × the HDS organisation's resources + €200/month per HDS organisation; ",
                   "avec l'hébergement HDS : 1,4 × les ressources de l'organisation HDS + 200 €/mois par organisation HDS ; "),
    "hds_level": (" — with HDS hosting %s €/month", " — avec l'hébergement HDS %s €/mois"),
    "poc_hds": ("POC with HDS hosting: ≈ %s €/h — a 2-hour test ≈ %s € (1.4 × the HDS organisation's resources) + €200/month for the HDS organisation",
                "POC avec l'hébergement HDS : ≈ %s €/h — un test de 2 heures ≈ %s € (1,4 × les ressources de l'organisation HDS) + 200 €/mois pour l'organisation HDS"),
    "legend": ("Floor: every app on 1 instance. Realistic: the production minimum (usually 2 instances per app) with the database plan sized on the data. Ceiling: the autoscaling maximum.",
               "Plancher : chaque application sur 1 instance. Réaliste : le minimum de production (en général 2 instances par application) avec le plan de base dimensionné sur "
               "les données. Plafond : le maximum de l'autoscaling."),
    "unpriced": ("NOT PRICED (no public grid price), not in the totals: %s — confirm them in the Console.",
                 "NON CHIFFRÉ (pas de prix public dans la grille), hors des totaux : %s — à confirmer dans la Console."),
    "incomplete": ("INCOMPLETE: the totals are lower bounds — see the lines without a figure and the assumptions.",
                   "INCOMPLET : les totaux sont des minimums — voir les lignes sans chiffre et les hypothèses."),
    "outbound": ("Outbound traffic: billed for Cellar only, on the grid's tiers; nothing for applications.",
                 "Trafic sortant : facturé pour Cellar seulement, selon les paliers de la grille ; rien pour les applications."),
    "existing_same": ("Existing cost, same resources: not estimated", "Coût existant, à ressources égales : non estimé"),
    "existing_reco": ("Existing cost, recommended: not estimated", "Coût existant, recommandé : non estimé"),
}
PRICE_UNIT = ("€/month", "€/mois")
PRICE_LEVELS_FR = {"floor": "Plancher", "realistic": "Réaliste", "ceiling": "Plafond"}


def price_parts(plan, lang="en", full=False, health=None):
    """The wording of the price block as data, ONE copy for screen 4 and for the documents: a dict of lines (every list may be empty).
      state         "report-only" | "unavailable" (grid unreachable) | "absent" (price not read) | "ok"
      status        the sentence that says why there is no price, or (ok) the POC line: per hour, 2 hours, zone
      reason        (unavailable) the short reason alone, "prices unavailable (grid unreachable)" ("prix indisponibles (grille injoignable)")
      head          (ok) "Production (resources at public grid prices in zone … as the base; [with HDS hosting: 1.4 × the HDS organisation's resources +
                    €200/month per HDS organisation; ]Premium is not included):" — the HDS clause with health data yes or not sure (R37)
      levels        (ok) the floor / realistic / ceiling lines with their composition; legend: what each level means
      flags         (ok) NOT PRICED / INCOMPLETE
      services      support, HDS (health data yes or not sure) and Premium, exact texts; empty when the grid is unreachable
      existing      the two existing-cost lines (figures only from declared plans and a public page, else "not estimated")
      outbound      (ok) the Cellar-only outbound sentence when the plan prices outbound traffic
      assumptions   (ok) the assumptions, each cut to one line, without the HDS sentence the label already says
    `lang` is "en" (the screens) or "fr" (a document); the repository's and plan.py's own sentences are never translated. `health` replaces the plan's
    health answer (a document uses its own); `full` keeps every name and composition item."""
    p = _dict(plan)
    fr = 1 if lang == "fr" else 0
    w = lambda key: PRICE_WORDS[key][fr]
    prices = p.get("prices")
    prices = prices if isinstance(prices, dict) else None
    unavailable = prices is not None and isinstance(prices.get("unavailable"), str)
    ok = prices is not None and not unavailable and isinstance(prices.get("poc"), dict)
    report_only = _report_only(p)
    plan_health = p.get("health_answer") if health is None else health
    out = {"state": "ok", "status": [], "reason": "", "head": "", "levels": [], "legend": w("legend"), "flags": [], "services": [], "existing": [], "outbound": [], "assumptions": []}
    if report_only:
        out["state"], out["status"] = "report-only", [w("report_only") % _provenance(p)]
    elif unavailable:
        out["state"], out["reason"] = "unavailable", _text(prices["unavailable"], 120)
        if fr and out["reason"] == pricing.PriceUnavailable.MESSAGE:       # the one reason pricing.py writes, in French; any other text stays as written
            out["reason"] = w("unavailable_reason")
        out["status"] = [out["reason"] + w("unavailable")]
    elif not ok:
        out["state"], out["status"] = "absent", [w("not_read")]
    else:
        poc = prices["poc"]
        off = " [OFFLINE saved grid, TESTS ONLY — not live prices]" if prices.get("offline_grid") else ""
        note = w("poc_addons") if _list(_dict(p.get("provision")).get("addons")) else ""
        out["status"] = [w("poc") % (_eur(poc.get("eur_h"), 3), _eur(poc.get("eur_2h")), _text(p.get("zone") or "par", 12), note, off)]
        hp = _dict(_dict(prices.get("hds")).get("poc"))
        if plan_health != "no" and _is_num(hp.get("eur_h")):            # R37: a POC in the HDS organisation (parhds, contract declared signed)
            out["status"].append(w("poc_hds") % (_eur(hp["eur_h"], 3), _eur(hp.get("eur_2h"))))
    live = ok and not report_only
    if live:
        zone = _dict(p.get("price")).get("zone") or p.get("zone")       # the zone the grid was read for (the quote's), else the POC's
        out["head"] = w("head") % ((w("head_zone") % _text(zone, 12)) if isinstance(zone, str) and zone else "", w("hds_clause") if plan_health != "no" else "")
        for key, name in LEVEL_NAMES:
            lv = _dict(prices.get(key))
            hv = _dict(lv.get("hds")).get("eur_month")
            hds_part = w("hds_level") % _eur(hv) if plan_health != "no" and _is_num(hv) else ""
            out["levels"].append("%-9s %s %s%s — %s" % (PRICE_LEVELS_FR[key] if fr else name, _eur(lv.get("eur_month")), PRICE_UNIT[fr], hds_part,
                                                       _composition(lv.get("lines"), full)))
        unpriced = _strs(_dict(prices.get("gaps")).get("all"))
        if unpriced:
            out["flags"].append(w("unpriced") % _names(unpriced, full, MAX_NAMES, 40))
        if prices.get("incomplete"):
            out["flags"].append(w("incomplete"))
        if any(isinstance(l, dict) and l.get("kind") == "outbound" for l in _list(_dict(prices.get("realistic")).get("lines"))):
            out["outbound"] = [w("outbound")]
        out["assumptions"] = [_text(a, 230) for a in _strs(prices.get("assumptions")) if not HDS_WORDS_RX.match(a)]
    if not unavailable:
        if health is None and ok and _strs(prices.get("services")) and not fr:
            out["services"] = [_text(x, 400) for x in _strs(prices.get("services"))]
        else:
            out["services"] = pricing.service_texts(plan_health, lang)
    # the two "existing cost" lines are always shown (spec): a figure only when the plan declares its plans and a public page gave the price
    ex = [_text(l, 400) for l in str(_dict(prices.get("existing") if prices else None).get("text") or "").split("\n") if l.strip()] if live else []
    out["existing"] = ex if len(ex) == 2 else [w("existing_same"), w("existing_reco")]
    return out


def migration_parts(plan):
    """(sketch, lines): the five lines of the migration sketch when there are exactly five and the plan assesses (sketch is True: they are labelled
    [estimated]), else at most the first line, which says what was not assessed (sketch is False). One rule for screen 4 and the documents."""
    p = _dict(plan)
    mig = _strs(p.get("migration"))
    if _report_only(p) or len(mig) != 5:
        return False, [_text(m, 240) for m in mig[:1]]
    return True, [_text(m, 240) for m in mig]


def screen_price(plan, limit=None):
    """Screen 4, price (35 lines at most): the POC per hour and for 2 hours with what it proves and does not prove, the production on three
    levels, the exact service lines, the existing cost, what the grid does not price, the migration sketch [estimated], the "talk to Clever Cloud"
    block (one line per hand-off trigger: the reason and what to bring) and the answer expected. The wording of the price block is price_parts()'s."""
    p = _dict(plan)
    lim = LIMITS["price"] if limit is None else limit
    full = lim >= NO_LIMIT
    report_only = _report_only(p)
    prices = p.get("prices")
    prices = prices if isinstance(prices, dict) else None
    unavailable = prices is not None and isinstance(prices.get("unavailable"), str)
    ok = prices is not None and not unavailable and isinstance(prices.get("poc"), dict)
    title = "## Price of %s on Clever Cloud" % _name(p)
    proves, open_ = _poc_text(p, report_only)
    parts = price_parts(p, "en", full)
    sections = [_section(0, parts["status"], name=("price status line", "price status lines")),
                _section(0, ["Proves: " + proves, "Does not prove: " + open_], name=("proof line", "proof lines"))]
    if ok and not report_only:
        sections.append(_section(0, parts["levels"], head=[parts["head"]], name=("price level", "price levels")))
        sections.append(_section(0, parts["flags"], name=("price note", "price notes")))
    if not unavailable:
        sections.append(_section(0, parts["services"], name=("service line", "service lines")))
    sections.append(_section(1, parts["existing"], name=("existing-cost line", "existing-cost lines")))
    if ok and not report_only:
        if parts["outbound"]:
            sections.append(_section(2, parts["outbound"], name=("outbound line", "outbound lines")))
        sections.append(_section(3, ["- " + a for a in parts["assumptions"]], head=["Assumptions:"], name=("assumption", "assumptions")))
    sketch, mig = migration_parts(p)
    sections.append(_section(0, ["- " + m for m in mig], head=["Migration sketch [estimated]:"], name=("migration line", "migration lines")) if sketch
                    else _section(0, mig, name=("migration line", "migration lines")))
    ho = _handoff(p)
    sections.append(_section(1, ho, head=["Talk to Clever Cloud (a person is needed):"], name=("hand-off", "hand-offs")) if ho else
                    _section(1, ["Talk to Clever Cloud: no hand-off trigger from your answers (a quote, a discount or Premium always go through a person)."], name=("hand-off line", "hand-off lines")))
    tail = [_reply(p, prices if ok else None, full)] + ([] if full else ["Details: `.clever-landing/REPORT.md`"])
    return harden("\n".join(_never_promise([title] + _layout(lim - 1, sections, tail))))


# ------------------------------------------------------------------------------------------------ all of them
def render(plan, which):
    """One screen ("read", "compat", "price") or the three ("all"); ValueError for anything else."""
    if which == "read":
        return screen_read(plan)
    if which == "compat":
        return screen_compat(plan)
    if which == "price":
        return screen_price(plan)
    if which == "all":
        return render_all(plan)
    raise ValueError("unknown screen %s (known: %s)" % (_text(which, 20), ", ".join(WHICH)))


def render_all(plan):
    """The three screens, one blank line between them: what plan.py prints and plan.json "vision" holds."""
    return "\n\n".join([screen_read(plan), screen_compat(plan), screen_price(plan)])


def render_full(plan):
    """The three screens without the line limit: REPORT.md holds them, so "… N more in REPORT.md" is true."""
    return "\n\n".join([screen_read(plan, NO_LIMIT), screen_compat(plan, NO_LIMIT), screen_price(plan, NO_LIMIT)])


# ------------------------------------------------------------------------------------------------ public helpers for the documents
# deliverables.py writes its documents with the helpers the screens use. These are the same functions under names that are meant to stay: a rename of a
# private helper above does not silently change a document, and the selftest checks that every name below exists.
one_line = _text                 # one line of our own or plan text: control characters and runs of blanks become one space, cut with an ellipsis
cell = _cell                     # one cell of a Markdown table: each pipe escaped, a backslash doubled only right before a pipe
repo_span = _repo                # text from the scanned repository, between « »
repo_spans = _repo_list          # several of them, each between « », with the "(+N more in REPORT.md)" suffix unless full
join_names = _names              # ids or words of ours, joined, the same suffix
money = _eur                     # a price from the plan as text, "n/a" for anything that is not a finite number
composition = _composition       # "web XS × 2 · postgresql xxs_med": the lines of a price level
production_text = _production    # the production verdict as plan.py wrote it, the repository's detail between « »
placement_text = _placement      # the "Placement: …" line
poc_text = _poc_text             # (what the POC proves, what it does not prove)
evidence_text = _evidence        # "[read] «file:line»": the label is ours, the rest is the repository's
blocker_lines = _blockers        # (to fix before real data, kept as in the source, platform traps): numbered lines
compat_rows = _rows              # the bricks of the landing
project_name = _name             # the repository's name, between « »
provenance_name = _provenance    # the provenance that was read
is_report_only = _report_only    # the plan is a report, not a plan
keep_promises = _never_promise   # a line that names BYOK, automatic failover or GPU and does not say "not available" gets that notice
