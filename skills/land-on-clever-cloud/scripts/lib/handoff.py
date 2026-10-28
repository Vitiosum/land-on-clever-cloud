"""handoff.py — screen 4 bis (parcours v2, spec section 3): when the developer must talk to a human at Clever Cloud (why, and what to
bring), and the five-line migration sketch.

A pure library: Python 3.9 standard library plus the skill's own sizing.py (the data size taken when it is not known), no network, no
subprocess, no file I/O, no print. It receives the plan, the effective answers and the prices as plain dicts, tolerates any of their
keys being missing or of the wrong type, and returns plain lists. The screens and the contact request are somebody else's job.

    handoff_triggers(plan, answers, prices, extra_ids=None) -> [{"id", "why", "bring"}, ...]     # canonical order, one line per trigger
    migration_sketch(plan, answers) -> [str, ...]                                                 # five lines, never more

  plan     the dict plan.py writes to .clever-landing/plan.json. Read, each key optional:
             "components"  [{"id", "kind", "target", "effort", "optional", "service", "org", "zone_override", "options", "evidence", "hds"}]
                           a component that is optional, or whose target is "drop", is not planned and is never counted
                           (except an optional database add-on for pitr and replica: see below)
             "blocking"    the blocking points (plan.json today)
             "must_move"   [{"what", "effort", ...}] what has no equivalent as is (the migration sketch's effort counts it)
             "verdict"     the two-level verdict {"poc", "production", "production_blockers": [...]} (a text in a plan.json written
                           before parcours v2)
  answers  questions.effective_answers(): health, hds_contract_signed, data_gb, files_gb, load.shape, service.rpo_hours / downtime / h24, assumed.
           A key that is absent is no signal: nothing fires on it.
  prices   pricing.price_levels() or None. Read: the "lines" of the three levels.

Ruling R14: a trigger fires on an observable predicate and on nothing else. The predicates:

  hds_contract            health is yes or unsure, and hds_contract_signed is not yes
  premium                 service.h24 is true
  pitr                    service.rpo_hours < 24 (the backups of a dedicated database are daily), and the plan has a database component
  replica                 service.downtime is "minutes", and the plan has a database component
  contractual_resilience  service.downtime is "minutes" (no database needed)
  load_test               load.shape is "events" or "predictable_peaks"
  beyond_catalogue        a database pick has why == "beyond catalogue" (sizing.pick_db_plan()): a line of the prices carries it
                          (pricing.price_levels() sets beyond_catalogue and why on that line); the plan is not read for it
  decisive_no             the production blockers are not empty (verdict.production_blockers when the verdict is a dict that has that key,
                          else the plan's "blocking" points, which are the POC's today), or a planned component has target "blocked"
                          (plan.py's verdict "not a fit today")
  hds_ambiguous_service   health is yes or unsure, and a planned component uses a service absent from the HDS zone parhds or marked
                          not_recommended there. What marks it in the plan: org "secondary" or a zone_override outside the HDS zones (placed
                          in the second organisation), an evidence line "NOT available in <HDS zone>" or an option "second non-HDS
                          organisation" (plan.py sets these when an add-on is absent from the zone), and hds == "not_recommended"
                          (plan.py sets it on the services rules/_services.json marks so: Keycloak)
  encrypt_existing_addon, pg_roles_or_extensions, fixed_ip, vpn
                          no plan or scan data carries the signal today: they fire only through extra_ids
  quote_or_discount, retention, public_buyer_or_dpo
                          no signal exists: they fire only through extra_ids
  ON_REQUEST_IDS          the seven ids above that fire only through extra_ids: the only ones plan.py --handoff-extra accepts

extra_ids: ids the caller asks for (a list, or one id as a text). Each one is checked against TRIGGER_IDS; an unknown id, or an
extra_ids that is not a list, raises ValueError. An id both derived and asked for appears once, with the derived reason.

A database component is a planned component whose service is postgresql, mysql or mongodb and whose target is not keep-saas. For pitr and
replica only, an OPTIONAL database add-on (target "addon") counts too: R28 provisions and links it by default, and the reason then says
"unless you go without <id>" (the plan cannot know about a later --without). contractual_resilience needs no database.

The texts are short and factual, and never carry an amount: the HDS figures (ruling R37) are pricing.py's, shown on the price screen, never
in a hand-off line; no Premium amount, only parhds as an HDS zone. A contractual RTO is named only to say it is not available in the standard offer.

    standard_text(tid) -> (why, bring)        # the standard sentences of one trigger id (KeyError for an id that is not in TRIGGER_IDS): what a
                                              # document quotes for "what to bring", and what tells a standard reason from a derived one
"""
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import sizing  # noqa: E402
from controls import CONTROL_RX  # noqa: E402

TRIGGER_IDS = ("hds_contract", "quote_or_discount", "premium", "pitr", "replica", "encrypt_existing_addon", "retention", "fixed_ip", "vpn",
               "pg_roles_or_extensions", "contractual_resilience", "load_test", "hds_ambiguous_service", "decisive_no", "public_buyer_or_dpo",
               "beyond_catalogue")
# The triggers no plan or scan data carries (R14): they fire only through extra_ids, so they are the only ones a caller may ask for on the
# developer's word (plan.py --handoff-extra). Every other trigger fires on its own predicate, never on request.
ON_REQUEST_IDS = tuple(i for i in TRIGGER_IDS if i in ("quote_or_discount", "encrypt_existing_addon", "retention", "fixed_ip", "vpn",
                                                       "pg_roles_or_extensions", "public_buyer_or_dpo"))
assert len(ON_REQUEST_IDS) == 7, "an on-request id is misspelled: it fell out of TRIGGER_IDS"
UNLESS_WITHOUT = "unless you go without"              # pitr / replica on an optional database add-on (R28): "; <this> <id>" ends the reason
BEYOND = "beyond catalogue"                           # sizing.pick_db_plan()'s reason when no plan can serve: the one string the hand-off keys on
DAILY_BACKUP_RPO_HOURS = 24                           # the backups of a dedicated database are daily: a lower RPO needs PITR
HDS_ZONES = ("parhds", "rbxhds", "grahds")            # recognised in a plan; only parhds is ever named in a text
LOAD_TEST_SHAPES = ("events", "predictable_peaks")
MAX_NAMES = 5                                         # component ids named in one reason, then "+N more"

COPY_MB_PER_S = 28                                    # database transfer rate measured on the platform
PRUDENCE = 2                                          # the downtime range runs from the plain estimate to twice that
MAX_ESTIMATE_HOURS = 1000                             # beyond this the figure is not a number worth printing
LONG_COPY_MINUTES = 60                                # a copy whose upper estimate passes an hour needs a rehearsal and a planned window
EFFORT_ORDER = ("none", "S", "M", "L", "XL")          # plan.py's ladder, per component
EFFORT_DAYS = {"S": "1–3", "M": "4–8", "L": "9–20"}   # person-days, [estimated]; plan.py's none and S are S, its L and XL are L
EFFORT_BUCKET = {"none": "S", "S": "S", "M": "M", "L": "L", "XL": "L"}
SKETCH_LABELS = ("What moves", "Downtime", "Point of no return", "Rollback", "Effort")
DATABASES = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB"}      # services whose data moves (a cache is rebuilt)
FILE_SERVICES = ("s3", "fs")                          # object storage and FS Bucket: files to copy
APP_TARGETS = ("native", "docker")                    # the targets plan.py provisions an app for: what "moves" as an application

_CONTROL_RX = CONTROL_RX                             # the ONE sanitizer of untrusted display text (controls.py, common.CONTROL_RX): Cc, Cf, Zl, Zp
_NAME_RX = re.compile(r"[^A-Za-z0-9_.-]")
_ABSENT_RX = re.compile(r"NOT available in (?:%s)\b" % "|".join(HDS_ZONES))

# What each trigger says. `why` is the reason when the caller asks for the trigger without a derived one; the derived reasons are built below.
# `bring` is what to take to the conversation.
_TEXT = {
    "hds_contract": (
        "An HDS contract with Clever Cloud must be signed before real health data.",
        "the contract model choice, the contract form, the onboarding, the opening of the parhds zone; no real health data before it is signed"),
    "quote_or_discount": (
        "A quote, a discount or a commercial condition is asked for.",
        "the expected volumes and duration, the legal entity that would sign, the date the decision is due"),
    "premium": (
        "24/7 support requested: Premium is the option (public formula on the price screen, never in the totals).",
        "the production environments in scope, who must be reached at night and for what, the 12-month commitment decision"),
    "pitr": (
        "Point-in-time recovery is asked: outside self-service, no public price.",
        "the target RPO, the database size, the restore time you can accept"),
    "replica": (
        "A replica is asked: outside self-service, no public price.",
        "the database size, the downtime and failover time you can accept, whether reads may go to the replica"),
    "encrypt_existing_addon": (
        "An existing add-on is not encrypted; encryption is chosen at creation, so it means a new add-on and a data move.",
        "the add-on id and plan, its data size, the downtime you can accept for the move"),
    "retention": (
        "A retention longer than the standard one is asked for (backups, logs or files).",
        "what must be kept (backups, logs, files), for how long, and the reason behind the duration"),
    "fixed_ip": (
        "A fixed IP address is needed.",
        "who needs it and in which direction (inbound or outbound), how many addresses, from which date"),
    "vpn": (
        "A VPN link to another network is needed.",
        "the peer network and its subnets, the protocol, who runs the other end"),
    "pg_roles_or_extensions": (
        "PostgreSQL roles or extensions beyond the add-on's defaults are needed.",
        "the roles with their rights, the extensions, the PostgreSQL version in use"),
    "contractual_resilience": (
        "A contractual RTO is not available in the standard offer.",
        "the availability and recovery commitment you need (figures and penalties), the services concerned, the legal entity"),
    "load_test": (
        "A load test is run with Clever Cloud support warned beforehand.",
        "the date window, the target peak in requests per second, the routes to hit, who runs the test"),
    "hds_ambiguous_service": (
        "A service is absent from parhds or not recommended there.",
        "for each service: the data that goes through it (even cached, or identity data), the organisation it would run in, the DPO's opinion if you have one"),
    "decisive_no": (
        "A decisive requirement gets a no.",
        "the blocking points from the compatibility screen and the requirement each one breaks: a real no, or a workaround to look at together"),
    "public_buyer_or_dpo": (
        "A public buyer, an information-security officer (RSSI) or a DPO takes part in the decision.",
        "their names and roles, the procurement framework if any, the security questionnaire to answer"),
    "beyond_catalogue": (
        "A database is beyond the largest encrypted dedicated plan in the catalogue.",
        "the real data size and its growth, the peak connections, the read/write profile, what can be archived or split"),
}


def standard_text(tid):
    """(why, bring) of a trigger id: the standard sentences, as the triggers carry them unless the plan derives a reason of its own. KeyError for an id
    that is not in TRIGGER_IDS (a document never quotes a trigger this module does not know)."""
    return _TEXT[tid]


# ------------------------------------------------------------------------------------------------ small readers
def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, (list, tuple)) else []


def _num(v):
    """A finite number >= 0 as a float; None for anything else (None, bool, text, nan, inf, negative)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _name(v):
    """A component id or a plan value, safe to print: odd characters become '?'."""
    return _NAME_RX.sub("?", str(v))[:40]


def _repo_text(v, limit=90):
    """Free text that comes from the scanned repository, shown as data between « »: control characters removed, cut."""
    t = " ".join(_CONTROL_RX.sub(" ", str(v)).replace("«", "<").replace("»", ">").split())
    return "«%s»" % (t if len(t) <= limit else t[: limit - 1] + "…")


def _gb(x):
    return "%g GB" % x


def _names(ids):
    shown = ", ".join(ids[:MAX_NAMES])
    return shown + (" +%d more" % (len(ids) - MAX_NAMES) if len(ids) > MAX_NAMES else "")


def _assumed(answers):
    return set(x for x in _list(answers.get("assumed")) if isinstance(x, str))


def _planned(plan):
    """The components that are planned: dicts that are neither optional nor dropped."""
    return [c for c in _list(plan.get("components")) if isinstance(c, dict) and not c.get("optional") and c.get("target") != "drop"]


def _optional_databases(plan):
    """Ids of the OPTIONAL database add-ons: R28 provisions and links them by default (only --without removes one), so pitr and replica
    count them, and say "unless you go without <id>" (the plan cannot know about a later --without)."""
    return [_name(c.get("id") or "?") for c in _list(plan.get("components")) if isinstance(c, dict) and c.get("optional")
            and c.get("target") == "addon" and isinstance(c.get("service"), str) and c["service"] in DATABASES]


def _database_labels(comps):
    """The labels (PostgreSQL, MySQL, MongoDB) of the database components among `comps`, once each, in order."""
    return list(dict.fromkeys(DATABASES[c["service"]] for c in comps if isinstance(c.get("service"), str) and c["service"] in DATABASES
                              and c.get("target") != "keep-saas"))


# ------------------------------------------------------------------------------------------------ the predicates
def _hds_services(plan):
    """Ids of the planned components on a service absent from the HDS zone, or marked not_recommended there (see the module docstring)."""
    out = []
    for c in _planned(plan):
        zone = c.get("zone_override")
        outside = c.get("org") == "secondary" or (isinstance(zone, str) and zone != "" and zone not in HDS_ZONES)
        absent = any(isinstance(e, str) and _ABSENT_RX.search(e) for e in _list(c.get("evidence")))
        second = any(isinstance(o, dict) and str(o.get("option", "")).lower().startswith("second non-hds") for o in _list(c.get("options")))
        if outside or absent or second or c.get("hds") == "not_recommended":
            out.append(_name(c.get("id") or "?"))
    return out


def _beyond_databases(prices):
    """Ids of the databases no plan can serve: the lines of the prices that carry a pick with why == "beyond catalogue"."""
    out = []
    for level in ("floor", "realistic", "ceiling"):
        for line in _list(_dict(_dict(prices).get(level)).get("lines")):
            if isinstance(line, dict) and (line.get("beyond_catalogue") is True or line.get("why") == BEYOND):
                out.append(_name(line.get("id") or "?"))
    return list(dict.fromkeys(out))


def _blockers(plan):
    """(the blockers, what they are): verdict.production_blockers when the verdict is a dict that has that key ("production blocker"),
    else the plan's "blocking" points ("blocking point": plan.py makes them for the POC as written)."""
    verdict = plan.get("verdict")
    if isinstance(verdict, dict) and "production_blockers" in verdict:
        return [b for b in _list(verdict["production_blockers"]) if b], "production blocker"
    return [b for b in _list(plan.get("blocking")) if b], "blocking point"


def _extra(extra_ids):
    """The ids the caller asks for, checked against TRIGGER_IDS (ValueError on anything else)."""
    if extra_ids is None:
        return set()
    if isinstance(extra_ids, str):
        extra_ids = [extra_ids]
    if not isinstance(extra_ids, (list, tuple, set, frozenset)):
        raise ValueError("extra_ids must be a list of trigger ids")
    for i in extra_ids:
        if not isinstance(i, str) or i not in TRIGGER_IDS:
            raise ValueError("unknown handoff trigger id %s (known: %s)" % (_name(i), ", ".join(TRIGGER_IDS)))
    return set(extra_ids)


def handoff_triggers(plan, answers, prices, extra_ids=None):
    """The conversations to have with a human at Clever Cloud: [{"id", "why", "bring"}], in the order of TRIGGER_IDS, once each.
    See the module docstring for the predicate of each trigger. Raises ValueError only for a bad extra_ids."""
    asked = _extra(extra_ids)
    p, a = _dict(plan), _dict(answers)
    assumed = _assumed(a)
    sv, ld = _dict(a.get("service")), _dict(a.get("load"))
    database = bool(_database_labels(_planned(p)))
    optional_dbs = [] if database else _optional_databases(p)            # R28: provisioned unless the developer goes without them
    unless = "; %s %s" % (UNLESS_WITHOUT, _names(optional_dbs)) if optional_dbs else ""
    why = {}

    health = a.get("health")
    if health in ("yes", "unsure"):
        signed = "HDS contract not declared signed" + (" [assumed]" if "hds_contract_signed" in assumed else "")
        if a.get("hds_contract_signed") != "yes":
            why["hds_contract"] = "health data: %s%s; %s." % ("yes" if health == "yes" else "not sure", " [assumed]" if "health" in assumed else "", signed)
        services = _hds_services(p)
        if services:
            why["hds_ambiguous_service"] = "health data (%s) and a service absent from parhds or not recommended there: %s." % (
                "yes" if health == "yes" else "not sure", _names(services))
    if sv.get("h24") is True:
        why["premium"] = _TEXT["premium"][0]
    rpo = _num(sv.get("rpo_hours"))
    if (database or optional_dbs) and rpo is not None and rpo < DAILY_BACKUP_RPO_HOURS:
        why["pitr"] = "RPO of %g h asked, under the %d h of the daily backups: PITR is needed (no public price, outside self-service)%s." % (
            rpo, DAILY_BACKUP_RPO_HOURS, unless)
    if sv.get("downtime") == "minutes":
        if database or optional_dbs:
            why["replica"] = "Downtime tolerated is minutes: a replica is needed (no public price, outside self-service)%s." % unless
        why["contractual_resilience"] = "Downtime tolerated is minutes: a contractual RTO is not available in the standard offer."
    if ld.get("shape") in LOAD_TEST_SHAPES:
        why["load_test"] = "Load is %s: a load test is run with Clever Cloud support warned beforehand." % ld["shape"].replace("_", " ")
    beyond = _beyond_databases(prices)
    if beyond:
        data = _num(a.get("data_gb"))
        why["beyond_catalogue"] = "Database %s: no encrypted dedicated plan in the catalogue holds the data or the connections%s." % (
            _names(beyond), " (data: %s)" % _gb(data) if data is not None else "")
    blockers, noun = _blockers(p)
    blocked = [_name(c.get("id") or "?") for c in _planned(p) if c.get("target") == "blocked"]
    if blockers or blocked:
        parts = []
        if blockers:
            first = ", first: %s" % _repo_text(blockers[0]) if isinstance(blockers[0], str) else ""
            parts.append("%d %s%s in the plan%s" % (len(blockers), noun, "" if len(blockers) == 1 else "s", first))
        if blocked:
            parts.append("%d component%s not a fit today: %s" % (len(blocked), "" if len(blocked) == 1 else "s", _names(blocked)))
        why["decisive_no"] = "; ".join(parts) + "."

    return [{"id": tid, "why": why.get(tid) or _TEXT[tid][0], "bring": _TEXT[tid][1]} for tid in TRIGGER_IDS if tid in why or tid in asked]


# ------------------------------------------------------------------------------------------------ the migration sketch
def _up(x):
    """The next whole number up, tolerating the float noise of an exact figure."""
    return int(math.ceil(x - 1e-9))


def _downtime_range(gb):
    """The downtime of copying `gb` GB of database: from the plain estimate to PRUDENCE times that. Never a crash on any size."""
    low = gb * 1000.0 / COPY_MB_PER_S / 60.0                     # minutes
    high = low * PRUDENCE
    if not math.isfinite(high) or high > MAX_ESTIMATE_HOURS * 60:
        return "over %d h" % MAX_ESTIMATE_HOURS, high
    if high <= 1:
        return "under 1 min", high
    if high < 120:
        return "%d–%d min" % (_up(low), _up(high)), high
    return "%.1f–%.1f h" % (_up(low / 60.0 * 10) / 10.0, _up(high / 60.0 * 10) / 10.0), high


def _effort(comps, high_minutes, moves=()):
    """(S | M | L, the reason), computed on the FINAL plan: the heaviest of the components' efforts, the efforts of their changes (a change added after
    the component was planned — the health route, the DATABASE_URL mapping — counts) and the must-move items; raised to M when the data copy is long.
    The reason names what drives it: "heaviest planned change: S, 2 changes in 1 file", "must move: M". "no code change planned" only when nothing is."""
    rank = lambda xs: max(xs, key=EFFORT_ORDER.index) if xs else "none"          # noqa: E731
    changes = [ch for c in comps for ch in _list(c.get("changes")) if isinstance(ch, dict)]
    code = rank([c.get("effort") for c in comps if c.get("effort") in EFFORT_ORDER] + [ch.get("effort") for ch in changes if ch.get("effort") in EFFORT_ORDER])
    move = rank([m.get("effort") for m in _list(moves) if isinstance(m, dict) and m.get("effort") in EFFORT_ORDER])
    top = rank([code, move])
    reasons = []
    if changes:
        files = len({str(ch.get("file")) for ch in changes})
        reasons.append("heaviest planned change: %s, %d change%s in %d file%s" % (code if code != "none" else "S", len(changes), "" if len(changes) == 1 else "s",
                                                                                 files, "" if files == 1 else "s"))
        top = rank([top, "S"])
    elif code != "none":
        reasons.append("heaviest planned change: %s" % code)
    if move != "none":
        reasons.append("must move: %s" % move)
    level, reason = EFFORT_BUCKET[top], "; ".join(reasons) or "no code change planned"
    if high_minutes is not None and high_minutes > LONG_COPY_MINUTES and level == "S":
        level, reason = "M", "a copy of over an hour needs a rehearsal and a planned window"
    return level, reason


def migration_sketch(plan, answers):
    """Five lines, labelled [estimated] where they hold an estimate: what moves, the downtime, the point of no return, the rollback, the effort.
    The downtime is the database only (data_gb at COPY_MB_PER_S, doubled for prudence); the files are copied before the switch.
    The point of no return is the first write to the new database or the first upload to Cellar after the switch, and a rollback
    copies those back; with no database and no files it is "none". Apps counted are those planned native or docker.
    No database and no files: "no data to migrate". An unknown data size is sizing.ASSUMED_DATA_GB and says [assumed]."""
    p, a = _dict(plan), _dict(answers)
    comps = _planned(p)
    assumed = _assumed(a)
    data, files = _num(a.get("data_gb")), _num(a.get("files_gb"))
    labels = _database_labels(comps)
    has_db = data is not None or "data_gb" in assumed or bool(labels)
    has_files = files is not None or "files_gb" in assumed or any(c.get("service") in FILE_SERVICES for c in comps)
    apps = sum(1 for c in comps if c.get("kind") in ("web", "worker") and c.get("target") in APP_TARGETS)    # as plan.py provisions them

    app_txt = "the app" if apps == 0 else "%d app%s" % (apps, "" if apps == 1 else "s")
    parts = [app_txt]
    high = None
    first_writes, copied_back = [], []              # what makes the switch irreversible, and what a rollback must copy back
    if has_db:
        size_assumed = data is None
        size = sizing.ASSUMED_DATA_GB if size_assumed else data
        size_txt = _gb(size) + (" [assumed]" if size_assumed else "")
        parts.append("%s (%s)" % ("/".join(labels) or "database", size_txt))
        figure, high = _downtime_range(size)
        downtime = "%s [estimated]: %s at %d MB/s, ×%d for prudence; measured later by a timed rehearsal" % (figure, size_txt, COPY_MB_PER_S, PRUDENCE)
        first_writes.append("the first write accepted by the new database")
        copied_back.append("the writes made on the new database")
    else:
        downtime = "no database to copy, only the DNS switch (propagation within the TTL) [estimated]"
    if has_files:
        parts.append("files (%s) to Cellar" % (_gb(files) if files is not None else "size not given"))
        first_writes.append("the first file uploaded to Cellar")
        copied_back.append("the files uploaded to Cellar")
        if not has_db:
            downtime = "no database to copy; the files are copied before the switch, so only the DNS switch (TTL) [estimated]"
    if not (has_db or has_files):
        parts = ["%s (code and configuration) only" % app_txt]
        downtime = "no data to migrate, only the DNS switch (propagation within the TTL) [estimated]"
        no_return = "none as long as the old platform stays online and is not deleted"
        rollback = "point the domain back to the old platform; nothing to copy back"
    else:
        no_return = "%s after the switch; until then the source stays intact" % " or ".join(first_writes)
        rollback = ("before that point, point the domain back to the old platform (TTL lowered beforehand); after it, %s must be copied back"
                    % " and ".join(copied_back))
    level, reason = _effort(comps, high, p.get("must_move"))
    effort = "%s, %s person-days [estimated] (%s)" % (level, EFFORT_DAYS[level], reason)
    return ["%s: %s" % (label, text) for label, text in zip(SKETCH_LABELS, (", ".join(parts), downtime, no_return, rollback, effort))]
