"""pricing.py — screen 4, the price (parcours v2, spec section 3): the POC per hour and for 2 hours, the production on three
levels on 720-hour months, the exact support / HDS / Premium lines, Cellar outbound, the existing cost.

A pure library: it receives the price grid and the plan's spec as arguments and returns plain dicts. No network, no subprocess,
no file I/O, no print. The hourly computation stays price.py's (price.price_items) and the sizing rules stay sizing.py's: this
module only assembles the levels, so the plan, the price and the provisioning cannot drift apart.

    price_levels(spec, grid, answers) -> dict

  spec     what plan.py hands over:
             {"components": [component, ...],              # in the order the lines are shown
              "zone": "par",                                # optional: the zone the grid was read for, when the grid does not say it
              "existing_price": {"amount", "currency", "source_url", "read_on"}}    # optional, read by the agent on a public page
           an application component:
             {"id", "kind": "app", "framework", "next_image", "singleton",         # the inputs of sizing.size_app()
              "sizing": {"flavor", "min", "max", "why"},    # optional: size_app()'s result, taken as given (a plain-slug flavor, whole instance
                                                            # counts from 1 to 1000, min <= max); not usable -> size_app's, with an assumption
              "db_pool", "db_processes"}                    # optional: connections per process and processes -> sizing.connection_budget()
           an add-on component:
             {"id", "kind": "addon", "service": "postgresql" | "mysql" | "redis" | "s3" | ...,    # the rules' service key
              "price_source": the grid's source ("postgresql", "mysql", "redis", "mongodb", "es", "cellar") or None when the grid does not
                              price it (Keycloak, FS Bucket...: no figure, said so, never free),
              "plan": the plan.py plan (price item "<price_source>:<plan>"; a plan or flavor that is not a plain slug is not priced),
              "plans": optional, Catalog.dedicated_plans() of a PostgreSQL or MySQL add-on: the production plan is then
                       sizing.pick_db_plan() (cheapest encrypted dedicated plan that holds the data x2 and the connections),
              "files_out_gb": Cellar only, GB sent out of Cellar per month}
  grid     price.load()'s result for the zone. A grid with no application price (price.has_prices() is false: {} or None after a
           failed price.load, no usable row) raises PriceUnavailable: no figure, never a remembered price. price.load itself raises
           on such an answer, so the two always agree.
  answers  questions.effective_answers(): data_gb, files_gb (Cellar storage), declared_plans, load.shape, service.rpo_hours / h24,
           health, assumed.

  returns  {"poc": {"eur_h", "eur_2h"},                     # nano on 1 instance per app (XS for Next.js images), the production database plans,
                                                            # Cellar on its free tier (the POC holds a sample, not the files)
            "floor" | "realistic" | "ceiling": {"eur_month", "lines": [line, ...]},
            "services": [str, ...],                         # support, HDS (health or doubt only: the R37 rule and its source), Premium: exact texts
            "hds": {"fee", "uplift", "orgs", "poc"} or None # health yes or not sure (or not answered): the HDS rule (R37); "poc" = {"eur_h", "eur_2h", "fee"}
                                                            # when the POC itself runs in an HDS zone (parhds, contract declared signed), else None
            "existing": {"text"},                           # two lines: same resources, recommended — or "not estimated" on both
            "incomplete": bool,                             # a line without a figure, or an option required but without a public price
            "assumptions": [str, ...],                      # every [assumed] value and every limit of the figures
            "offline_grid": bool}                           # the grid is a saved test file: the figures are not live prices
  levels   floor = every app on 1 instance; realistic = the production minimum (size_app min, usually 2) with the database plans of
           pick_db_plan; ceiling = the autoscaling maximum (size_app max). The database follows the level: its connection budget is
           the sum of pool x processes x instances x 2 of the apps. A singleton (beat, cron, scheduler) is one instance everywhere.
  line     {"id", "kind": "app" | "database" | "addon" | "outbound", "text", "eur_month": float or None, ...}; None = no figure (the
           text says why); "beyond_catalogue": True on a database no plan can serve; "org": the component's organisation ("main" or "secondary")
           when the spec gives it. eur_month of a level = the sum of its lines (grid prices).
  hds      (health yes, not sure or not answered) each level also carries "hds": {"eur_month", "hds_resources", "other_org", "fee", "uplift", "orgs"}:
           communicated by Clever Cloud on 2026-10-27, not on the public pricing page:
           eur_month = 1.4 × the lines of the HDS organisation (every org but "secondary") + €200 × the HDS organisations (1) + the lines of the second,
           non-HDS organisation at grid price. A line without a figure stays out of both (the level is then incomplete, "at least").

Never in the output: a Premium amount (the public formula only, never in a total, never "includes HDS"), outbound traffic on an application
(Cellar only), a price the grid does not hold.
"""
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.dirname(_HERE)):         # lib/ for sizing, scripts/ for price (the hourly computation)
    if _p not in sys.path:
        sys.path.insert(0, _p)
import price as pricemod  # noqa: E402
import sizing  # noqa: E402

LEVELS = ("floor", "realistic", "ceiling")
MAX_INSTANCES = 1000                                  # a sizing above this is not a sizing: it is refused with an assumption, never priced
SIZED_SOURCES = ("postgresql", "mysql")               # the add-ons sizing.pick_db_plan() knows how to size
DATABASE_SOURCES = ("postgresql", "mysql", "mongodb")
DAILY_BACKUP_RPO_HOURS = 24                           # the backups of a dedicated database: a lower RPO needs PITR
ASSUMED_FILES_GB = 1.0                                # Cellar volume taken when the files stored (and the traffic sent) are not known

TEXT_SUPPORT = "Standard support: included (€0)"
# the HDS tariff, communicated by Clever Cloud on 2026-10-27 (not on the public site): €200/month per HDS organisation + 1.4 × the resources
# placed in it (apps, add-ons, builds, Cellar); a second, non-HDS organisation stays at grid prices. Shown with its source, never as a public price.
HDS_FEE = 200.0                                       # € per month and per HDS organisation
HDS_UPLIFT = 1.4                                      # on the resources placed in the HDS organisation
HDS_ORGS = 1                                          # the plans of this skill have one HDS organisation (the second one is never HDS)
HDS_ZONES = ("parhds", "rbxhds", "grahds")            # a POC in one of them is in the HDS organisation (parhds is the only one the skill proposes)
TEXT_HDS = ("HDS hosting: €200/month per HDS organisation + 1.4 × the resources placed in it (apps, add-ons, builds, Cellar); a second, non-HDS "
            "organisation stays at grid prices — communicated by Clever Cloud on 2026-10-27, not on the public pricing page — confirm on your quote/contract")
TEXT_HDS_FR = ("Hébergement HDS : 200 €/mois par organisation HDS + 1,4 × les ressources qui y sont placées (applications, add-ons, builds, Cellar) ; une "
               "seconde organisation, hors HDS, reste au prix de la grille — communiqué par Clever Cloud le 2026-10-27, absent de la page de tarifs "
               "publique — à confirmer sur votre devis/contrat")
TEXT_PREMIUM = ("Premium (24/7 critical support): €490/month + 1.8 × production consumption, 12-month commitment — "
                "https://www.clever.cloud/clever-cloud-vs-clever-cloud-premium/")

# A flavor or a plan becomes part of a price item "<source>:<slug>[x<count>][@build]": only a plain slug may, and one that does not end
# like a count ("s_monox3") or a suffix ("@build") that price.parse() would read. Anything else is not priced, never mangled.
_SLUG_RX = re.compile(r"\A(?![A-Za-z0-9_]*[xX][0-9]+\Z)[A-Za-z0-9_]{1,24}\Z")
_SOURCE_RX = re.compile(r"\A[a-z0-9]{2,16}\Z")
_ZONE_RX = re.compile(r"\A[a-z0-9]{2,12}\Z")
_URL_RX = re.compile(r"\Ahttps://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]{1,180}\Z")
_DATE_RX = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_CURRENCY_RX = re.compile(r"\A[A-Za-z]{3}\Z")
_TEXT_RX = re.compile(r"[^A-Za-z0-9 _.:/@+()-]")


class PriceUnavailable(Exception):
    """The price grid could not be read: no figure, no approval. The screen says exactly this message."""

    MESSAGE = "prices unavailable (grid unreachable)"

    def __init__(self, message=None):
        Exception.__init__(self, message or self.MESSAGE)


# ------------------------------------------------------------------------------------------------ small readers
def _num(v):
    """A finite number >= 0 as a float; None for anything else (None, bool, text, nan, inf, negative)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) and f >= 0 else None


def _whole(v):
    """A whole number of instances from 1 to MAX_INSTANCES (a bool, a float or a number beyond the bound is not one)."""
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_INSTANCES


def _label(v, n=60):
    """A component name or plan from the plan, safe to print: odd characters become '?'."""
    return _TEXT_RX.sub("?", str(v))[:n]


def _money(x):
    return "%.2f €/month" % x


def _gb_text(x):
    """12000.0 -> "12000", 0.5 -> "0.5": a plain decimal, which is also what the price item cellar:outbound@<GB> reads."""
    return ("%.6f" % x).rstrip("0").rstrip(".")


def _cid(c, i):
    for k in ("id", "name"):
        if isinstance(c.get(k), str) and c[k].strip():
            return _label(c[k].strip())
    return "%s%d" % (c.get("kind", "component"), i + 1)


# ---------------------------------------------------------------------------------------------------- applications
def _app_sizing(app, answers):
    """(sizing, refused): the plan's own sizing when it is usable, else size_app's; `refused` is True when a sizing was given and could not
    be used (flavor not a plain slug, instances not whole numbers from 1 to MAX_INSTANCES, min above max): the assumptions say so."""
    s = app.get("sizing")
    if (isinstance(s, dict) and isinstance(s.get("flavor"), str) and _SLUG_RX.match(s["flavor"].strip())
            and _whole(s.get("min")) and _whole(s.get("max")) and s["max"] >= s["min"]):
        return {"flavor": s["flavor"].strip(), "min": s["min"], "max": s["max"], "why": str(s.get("why") or "")}, False
    return sizing.size_app(app, answers), s is not None


def _instances(level, sz):
    return 1 if level == "floor" else (sz["min"] if level == "realistic" else sz["max"])


def _connections(apps, level):
    """(connections, unusable): the connections the databases must allow at this level, the sum of pool x processes x instances x 2
    of the apps that say their pool (db_pool); None when no app does (the connections are then not checked). `unusable` is True when a
    pool or process count cannot be turned into a number of connections (not a finite number, or a product that overflows): never a
    crash, the connections are not checked and the assumptions say so."""
    total, known = 0, False
    for app, sz in apps:
        pool = _num(app.get("db_pool"))
        if pool is None:
            if app.get("db_pool") is not None:
                return None, True
            continue
        procs = _num(app.get("db_processes"))
        if procs is None and app.get("db_processes") is not None:
            return None, True
        try:
            total += sizing.connection_budget(pool, 1 if procs is None else procs, _instances(level, sz))
        except ValueError:
            return None, True
        known = True
    return (total if known else None), False


# ------------------------------------------------------------------------------------------------------ one level
def _entries(level, apps, addons, grid, answers):
    """What one level buys, before it is priced: a list of entries, each with the price `item` (None = nothing to ask the grid)."""
    entries = []
    for i, (app, sz) in enumerate(apps):
        n = _instances(level, sz)
        entries.append({"id": _cid(app, i), "kind": "app", "flavor": sz["flavor"], "instances": n, "org": _org(app),
                        "item": "apps:%s%s" % (sz["flavor"].lower(), "x%d" % n if n > 1 else ""),
                        "label": "%s × %d" % (sz["flavor"], n)})
    for j, ad in enumerate(addons):
        cid = _cid(ad, j)
        n0 = len(entries)
        service = _label(ad.get("service") or ad.get("price_source") or "add-on", 30)
        source = ad["price_source"] if isinstance(ad.get("price_source"), str) and _SOURCE_RX.match(ad["price_source"]) else None
        plan = ad["plan"].strip() if isinstance(ad.get("plan"), str) and _SLUG_RX.match(ad["plan"].strip()) else None
        if source is None:
            entries.append({"id": cid, "kind": "addon", "service": service, "plan": plan, "item": None,
                            "label": "%s%s" % (service, " " + plan if plan else ""), "why_none": "not in the price grid (confirm in the Console)"})
        elif source == "cellar":
            stored, stored_assumed = _stored_gb(answers)
            entries.append({"id": cid, "kind": "addon", "service": service, "plan": plan, "gb": stored, "assumed": stored_assumed,
                            "item": "cellar:storage@" + _gb_text(stored), "poc_item": "cellar:storage",       # the POC keeps the free tier
                            "label": "Cellar storage %s GB" % _gb_text(stored) + (" [assumed]" if stored_assumed else "")})
            gb, assumed = _outbound_gb(ad, answers)
            entries.append({"id": cid, "kind": "outbound", "gb": gb, "assumed": assumed, "item": "cellar:outbound@" + _gb_text(gb),
                            "label": "outbound %s GB/month" % _gb_text(gb) + (" [assumed]" if assumed else "")})
        elif source in SIZED_SOURCES and isinstance(ad.get("plans"), list) and ad["plans"]:
            pick = sizing.pick_db_plan(ad["plans"], _num(answers.get("data_gb")), _connections(apps, level)[0],
                                       prices=grid.get(source) if isinstance(grid.get(source), dict) else None)
            if pick["plan"] is not None and not (isinstance(pick["plan"], str) and _SLUG_RX.match(pick["plan"])):
                pick = {"plan": None, "why": "the plan name read in the catalogue is not a plain slug", "detail": ""}
            e = {"id": cid, "kind": "database", "service": service, "why": pick["why"], "sized_db": True,
                 "fallback_item": "%s:%s" % (source, plan.lower()) if plan else None}     # the POC prices the plan.py plan when none fits
            if pick["plan"] is None:
                e.update(plan=None, item=None, label=service, why_none="no figure: " + pick["why"] + (" (%s)" % pick["detail"] if pick["detail"] else ""),
                         beyond_catalogue=pick["why"] == "beyond catalogue")
            else:
                e.update(plan=pick["plan"], item="%s:%s" % (source, pick["plan"].lower()), label="%s %s" % (service, pick["plan"]))
            entries.append(e)
        elif plan is None:
            entries.append({"id": cid, "kind": "database" if source in DATABASE_SOURCES else "addon", "service": service, "plan": None, "item": None,
                            "label": service, "why_none": "no plan chosen, or a plan name that is not a plain slug: not priced"})
        else:
            entries.append({"id": cid, "kind": "database" if source in DATABASE_SOURCES else "addon", "service": service, "plan": plan,
                            "item": "%s:%s" % (source, plan.lower()), "label": "%s %s" % (service, plan), "unsized_db": source in SIZED_SOURCES})
        for e in entries[n0:]:
            e["org"] = _org(ad)
    return entries


def _org(c):
    """The organisation of a component as the plan gives it ("main", "secondary"), or None when it says nothing (then: the HDS organisation)."""
    return c["org"] if isinstance(c.get("org"), str) and c["org"] in ("main", "secondary") else None


def _stored_gb(answers):
    """GB kept in Cellar and whether that figure is an assumption: the files the developer gave (files_gb), else 1 GB."""
    stored = _num(answers.get("files_gb"))
    return (stored, False) if stored is not None else (ASSUMED_FILES_GB, True)


def _outbound_gb(addon, answers):
    """GB sent out of Cellar per month and whether that figure is an assumption: the volume the plan gives (files_out_gb), else the
    files stored read once a month, else 1 GB."""
    given = _num(addon.get("files_out_gb"))
    if given is not None:
        return given, False
    return _stored_gb(answers)[0], True


def _cellars(entries):
    """(id, GB stored, stored is assumed, GB sent, sent is assumed) of each Cellar add-on of the entries."""
    out = []
    for e in entries:
        if e["kind"] == "addon" and e.get("poc_item") == "cellar:storage":
            sent = next((x for x in entries if x["kind"] == "outbound" and x["id"] == e["id"]), None)
            out.append((e["id"], e["gb"], e["assumed"], sent["gb"] if sent else 0.0, bool(sent and sent["assumed"])))
    return out


def _zone(spec, grid):
    """The zone the figures were read for: the grid says it (price.load stamps it), else the caller's spec; None when neither does."""
    meta = grid.get("_meta")
    for z in (meta.get("zone") if isinstance(meta, dict) else None, spec.get("zone")):
        if isinstance(z, str) and _ZONE_RX.match(z):
            return z
    return None


def _price_level(level, entries, grid):
    """Price the entries of a level: the lines, the total, whether a line has no figure."""
    result = pricemod.price_items(grid, [e["item"] for e in entries if e.get("item")])
    cursor = iter(result["lines"])           # price_items answers one line per item, in order
    lines, total, gap = [], 0.0, False
    for e in entries:
        line = {"id": e["id"], "kind": e["kind"], "eur_month": None}
        for k in ("flavor", "instances", "service", "plan", "why", "gb", "assumed", "org"):
            if k in e and e[k] is not None:
                line[k] = e[k]
        if e.get("beyond_catalogue"):
            line["beyond_catalogue"] = True
        if e.get("item"):
            priced = next(cursor)
            if priced.get("in_grid") and priced.get("eur_per_month_if_never_stopped") is not None:        # a @build line has no monthly figure
                line["eur_month"] = round(priced["eur_per_month_if_never_stopped"], 2)
        if line["eur_month"] is not None:
            total += line["eur_month"]
            line["text"] = "%s: %s = %s" % (e["id"], e["label"], _money(line["eur_month"]))
        else:
            gap = True
            why = e.get("why_none") or "not in the price grid (confirm in the Console)"
            line["text"] = "%s: %s — %s" % (e["id"], e["label"], why if why.startswith("no figure") else "no figure: " + why)
        lines.append(line)
    return {"eur_month": round(total, 2), "lines": lines}, gap


# ----------------------------------------------------------------------------------------------------------- texts
def service_texts(health, lang="en"):
    """The service lines of the price screen, exact: standard support, the HDS line (health data yes, not sure or not answered: the R37 rule with its
    source, in English or French) and the public Premium formula. screens.py shows them even where no grid was read: they come from no grid."""
    out = [TEXT_SUPPORT]
    if health != "no":
        out.append(TEXT_HDS_FR if lang == "fr" else TEXT_HDS)
    out.append(TEXT_PREMIUM)
    return out


def _hds_level(level):
    """R37 on one priced level: 1.4 × the HDS organisation's lines + the fee per HDS organisation + the second organisation's lines at grid price."""
    lines = [l for l in level["lines"] if l.get("eur_month") is not None]
    hds = sum(l["eur_month"] for l in lines if l.get("org") != "secondary")
    other = sum(l["eur_month"] for l in lines if l.get("org") == "secondary")
    return {"eur_month": round(HDS_UPLIFT * hds + HDS_FEE * HDS_ORGS + other, 2), "hds_resources": round(hds, 2), "other_org": round(other, 2),
            "fee": HDS_FEE, "uplift": HDS_UPLIFT, "orgs": HDS_ORGS}


def _existing_price(v):
    if not isinstance(v, dict):
        return None
    amount = _num(v.get("amount"))
    cur, url, day = v.get("currency"), v.get("source_url"), v.get("read_on")
    if (amount is None or not (isinstance(cur, str) and _CURRENCY_RX.match(cur)) or not (isinstance(url, str) and _URL_RX.match(url))
            or not (isinstance(day, str) and _DATE_RX.match(day))):
        return None
    return {"amount": amount, "currency": cur.upper(), "url": url, "day": day}


def _existing(spec, answers, floor, realistic, incomplete):
    """Two lines, always: the existing cost against the Clever floor (one instance per app, the closest to the same resources) and
    against the recommended (realistic) level. Figures only from declared plans AND a price read on a public page (link and date)."""
    plans_in = answers.get("declared_plans")
    declared = [_label(x, 40) for x in (plans_in if isinstance(plans_in, (list, tuple)) else []) if isinstance(x, str) and x.strip()]
    ep = _existing_price(spec.get("existing_price"))
    if not declared or ep is None:
        return {"text": "Existing cost, same resources: not estimated\nExisting cost, recommended: not estimated"}
    plans = ", ".join(declared)[:160]
    base = "%.2f %s/month [declared] (plans: %s; %s, read on %s)" % (ep["amount"], ep["currency"], plans, ep["url"], ep["day"])
    partial = " (incomplete: see the assumptions)" if incomplete else ""
    return {"text": "Existing cost, same resources: %s — Clever floor, 1 instance per app: %s%s\n"
                    "Existing cost, recommended: %s — Clever realistic, production minimum: %s%s"
                    % (base, _money(floor), partial, base, _money(realistic), partial)}


SERVICE_DEFAULT_TEXT = {"rpo_hours": ("acceptable data loss (RPO)", "24 h"), "downtime": ("tolerated downtime", "hours"), "h24": ("24/7 need", "no 24/7")}   # questions.DEFAULT_SERVICE


def _assumptions(ctx):
    out = ["monthly figures: 720-hour month (30 days), as the official calculator; EUR excluding taxes; billing is per second"]
    assumed = ctx["assumed"]
    if "health" in assumed:
        out.append("health data not answered: treated as not sure [assumed], so the HDS line is shown")
    if ctx["hds"]:
        out.append("HDS: the HDS figures are 1.4 × the resources of the HDS organisation (priced %s) + €200/month per HDS organisation; the second, "
                   "non-HDS organisation stays at grid prices (communicated by Clever Cloud on 2026-10-27, not on the public pricing page)"
                   % ("in zone " + ctx["zone"] if ctx["zone"] else "on the grid given"))
        out.append("builds: billed per build (not in the monthly levels), × 1.4 in the HDS organisation; the dry run of provision.sh gives the price of one")
    if ctx["apps"] and not ctx["headroom"]:
        out.append("%s load%s, no autoscaling: the ceiling equals the realistic level"
                   % (ctx["shape"].replace("_", " "), " [assumed]" if "load" in assumed else ""))
    elif ctx["headroom"]:
        out.append("autoscaling ceiling is an assumption ([assumed] 2 × the production minimum): confirm it with a load test")
    for cid in ctx["refused"]:
        out.append("%s: the sizing given is not usable (flavor not a plain slug, or instances not whole numbers from 1 to %d, or min above max): "
                   "the default sizing is used [assumed]" % (cid, MAX_INSTANCES))
    for name in ctx["singletons"]:
        out.append("%s: beat, cron or scheduler runs alone, exactly 1 instance on every level" % _label(name))
    missing = [k for k in ctx["service_assumed"] if k in SERVICE_DEFAULT_TEXT]
    if "service" in assumed and (not missing or len(missing) == len(SERVICE_DEFAULT_TEXT)):
        out.append("service level not answered: 24 h RPO, hours of downtime, no 24/7 [assumed]")
    elif "service" in assumed:          # RT D7: answered in part — only the missing fields are assumptions
        out.append("service level partly answered: " + "; ".join("%s not answered: %s [assumed]" % SERVICE_DEFAULT_TEXT[k] for k in missing))
    if ctx["h24"]:
        out.append("24/7 support requested: see the Premium option, it is never in the totals")
    if ctx["sized_db"] and ctx["data_gb"] is None:
        out.append("database size not answered: %g GB [assumed], x%d margin" % (sizing.ASSUMED_DATA_GB, sizing.DATA_MARGIN))
    if ctx["sized_db"] and ctx["conns_unusable"]:
        out.append("database connections not checked: a pool or process count is not a usable number [assumed]")
    elif ctx["sized_db"] and not ctx["conns_known"]:
        out.append("database connections not checked: no app gave its pool size [assumed]")
    for cid in ctx["unsized"]:
        out.append("%s: production plan not sized (no catalogue read), the plan.py plan is kept on every level [assumed]" % cid)
    for cid in ctx["no_figure"]:
        out.append("%s: no figure, left out of the totals (see its line)" % cid)
    for cid, stored, stored_assumed, sent, sent_assumed in ctx["cellars"]:
        if stored_assumed:
            out.append("%s: Cellar storage volume not given, %s GB kept all month [assumed]" % (cid, _gb_text(stored)))
        if sent_assumed:
            out.append("%s: Cellar outbound volume not given, %s GB a month [assumed] (the files stored read once a month, else %s GB)"
                       % (cid, _gb_text(sent), _gb_text(ASSUMED_FILES_GB)))
    if ctx["pitr"]:
        out.append("RPO of %g h is under the %d h of the daily backups: PITR is needed, it has no public price and is not in the totals "
                   "(a configuration outside self-service)" % (ctx["rpo"], DAILY_BACKUP_RPO_HOURS))
        if ctx["rpo"] == 0:
            out.append("RPO of 0 h: a replica is needed as well; no public price, not in the totals")
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# ------------------------------------------------------------------------------------------------------ the screen
def price_levels(spec, grid, answers):
    """The price screen's data. See the module docstring for the shape of `spec`, `grid` and `answers`.
    Raises PriceUnavailable when the grid cannot be read."""
    if not pricemod.has_prices(grid):
        raise PriceUnavailable()
    spec = spec if isinstance(spec, dict) else {}
    ans = answers if isinstance(answers, dict) else {}
    raw = spec.get("components")
    comps = [c for c in (raw if isinstance(raw, (list, tuple)) else []) if isinstance(c, dict)]
    apps_raw = [c for c in comps if c.get("kind") == "app"]
    addons = [c for c in comps if c.get("kind") == "addon"]
    sized = [_app_sizing(c, ans) for c in apps_raw]
    apps = [(c, sz) for c, (sz, _) in zip(apps_raw, sized)]
    refused = [_cid(c, i) for i, (c, (_, bad)) in enumerate(zip(apps_raw, sized)) if bad]

    out, incomplete, entries_by_level = {}, False, {}
    for level in LEVELS:
        entries_by_level[level] = _entries(level, apps, addons, grid, ans)
        out[level], gap = _price_level(level, entries_by_level[level], grid)
        incomplete = incomplete or gap

    # the POC: nano on one instance per app (sizing.poc_plans), the production database plans, redis and the others as the plan has them
    prod = {"apps": [dict(c, **sz) for c, sz in apps]}
    poc_items = ["apps:%s" % a["flavor"].lower() for a in sizing.poc_plans(prod)["apps"]]
    poc_other = [c.get("org") == "secondary" for c, _ in apps]        # by item: in the second (non-HDS) organisation
    for e in entries_by_level["realistic"]:
        item = e.get("poc_item") or e.get("item") or e.get("fallback_item")
        if item and e["kind"] not in ("app", "outbound"):       # the apps come from poc_plans, the traffic is a monthly volume charge
            poc_items.append(item)
            poc_other.append(e.get("org") == "secondary")
    poc = pricemod.price_items(grid, poc_items)
    incomplete = incomplete or bool(poc["missing"])

    service = ans.get("service") if isinstance(ans.get("service"), dict) else {}
    rpo = _num(service.get("rpo_hours"))
    rpo = float(DAILY_BACKUP_RPO_HOURS) if rpo is None else rpo
    has_db = any(ad.get("price_source") in DATABASE_SOURCES for ad in addons)
    pitr = has_db and rpo < DAILY_BACKUP_RPO_HOURS
    incomplete = incomplete or pitr
    health = ans.get("health")
    load = ans.get("load") if isinstance(ans.get("load"), dict) else {}
    real = entries_by_level["realistic"]
    ctx = {
        "assumed": {x for x in (ans["assumed"] if isinstance(ans.get("assumed"), (list, tuple)) else []) if isinstance(x, str)},
        "hds": health != "no", "apps": bool(apps),
        "headroom": any(sz["max"] > sz["min"] for _, sz in apps), "shape": load.get("shape") if load.get("shape") in sizing.LOAD_SHAPES else "steady",
        "singletons": sizing.singleton_apps(apps_raw), "h24": service.get("h24") is True, "data_gb": _num(ans.get("data_gb")),
        "sized_db": any(e.get("sized_db") for e in real), "conns_known": _connections(apps, "realistic")[0] is not None,
        "conns_unusable": any(_connections(apps, lv)[1] for lv in LEVELS), "zone": _zone(spec, grid),
        "unsized": [e["id"] for e in real if e.get("unsized_db")],
        "no_figure": [l["id"] for l in out["realistic"]["lines"] if l["eur_month"] is None],
        "cellars": _cellars(real),
        "pitr": pitr, "rpo": rpo, "refused": refused,
        "service_assumed": [k for k in (ans.get("service_assumed") if isinstance(ans.get("service_assumed"), list) else []) if isinstance(k, str)],
    }
    result = {
        "poc": {"eur_h": round(poc["run_h"], 6), "eur_2h": round(poc["run_h"] * 2, 2)},
        "floor": out["floor"], "realistic": out["realistic"], "ceiling": out["ceiling"],
        "services": service_texts(health),
        "existing": _existing(spec, ans, out["floor"]["eur_month"], out["realistic"]["eur_month"], incomplete),
        "incomplete": bool(incomplete),
        "assumptions": _assumptions(ctx),
        "offline_grid": isinstance(grid.get("_meta"), dict) and bool(grid["_meta"].get("offline")),
        "hds": None,
    }
    if health != "no":                  # R37: the HDS figures beside the grid ones (yes, not sure, or not answered: the safe side)
        for level in LEVELS:
            out[level]["hds"] = _hds_level(out[level])
        poc_hds = None
        poc_zone = spec["zone"] if isinstance(spec.get("zone"), str) and _ZONE_RX.match(spec["zone"]) else ctx["zone"]     # the POC's own zone (plan.py's)
        if poc_zone in HDS_ZONES:       # the POC runs in the HDS organisation (contract declared signed): its €/h × 1.4, the fee on top
            hds_h = pricemod.price_items(grid, [i for i, o in zip(poc_items, poc_other) if not o])["run_h"]
            oth_h = pricemod.price_items(grid, [i for i, o in zip(poc_items, poc_other) if o])["run_h"]
            eur_h = HDS_UPLIFT * hds_h + oth_h
            poc_hds = {"eur_h": round(eur_h, 6), "eur_2h": round(eur_h * 2, 2), "fee": HDS_FEE}
        result["hds"] = {"fee": HDS_FEE, "uplift": HDS_UPLIFT, "orgs": HDS_ORGS, "poc": poc_hds}
    return result
