"""sizing.py — production sizing rules proven in real tests (parcours v2, spec section 4).

A pure library: Python 3.9 standard library only (copy, math, re), no network, no subprocess, no file I/O. It receives
plain lists and dicts and returns plain dicts; the screens, the price grid and the plan files are somebody else's job.

The rules (each one was observed on the platform, or sourced from the Clever Cloud documentation):
  - nano and pico only for standby or an autoscaling floor (reduced CPU priority): a production app is XS at least;
  - a Next.js app that optimises its images (next/image) is XS at least: in nano it is `Killed`, then 3 min of outage (T6);
  - production runs 2 instances by default (the 1-instance variant is the caller's to show);
  - the old instances stay about 2 min during a deployment: connections count x2 (pool x processes x instances x 2);
  - a database plan follows the real size of the data x2 margin, the connections and the RAM, never the smallest plan by
    default (xxs_tny caps at 512 MB); the plan is created encrypted;
  - beat, cron and scheduler run in a dedicated app with ONE instance, otherwise each instance runs the task (T2).

Inputs the callers hand over:
  component  a dict: `id` (or `name`, `process`), `framework` (a string or a list, as the scan reads it), `next_image`
             (True, False or absent = not known), `singleton` (True to force one instance).
  answers    the dict of questions.effective_answers(): only `load.shape` is read here.
  plans      the list of Catalog.dedicated_plans(): [{"slug", "type", "encryption", "features"}], `features` being a dict
             with lowercased names ("max db size", "memory", "max connection limit") and strings such as "512 MB".
  prod       {"apps": [component + size_app() result, ...], <everything else, e.g. the database plans, is kept as is>}.
"""
import copy
import math
import re

PROD_FLAVOR = "XS"            # nano and pico are for standby or an autoscaling floor only
NEXT_IMAGE_FLAVOR = "XS"      # T6: next/image in nano = Killed, then 3 min of outage
POC_FLAVOR = "nano"
PROD_INSTANCES = 2
PEAK_FACTOR = 2               # assumed autoscaling ceiling for predictable peaks and events: 2 x the production floor
DATA_MARGIN = 2               # room for growth on the real size of the data
ASSUMED_DATA_GB = 1.0         # data size taken when the developer does not know it (x2 margin = 2 GB)
DEPLOY_FACTOR = 2             # old instances stay about 2 min during a deployment: their connections count too
LOAD_SHAPES = ("steady", "predictable_peaks", "events")
SINGLETON_TOKENS = frozenset(["beat", "celerybeat", "cron", "crontab", "scheduler"])
NEXT_NAMES = frozenset(["next", "nextjs", "next.js", "next-js"])

SIZE_KEYS = ("max db size", "max database size")
MEMORY_KEYS = ("memory", "ram")
CONNECTION_KEYS = ("max connection limit", "max connections", "connection limit")

_SIZE_RX = re.compile(r"\A\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B)\s*\Z", re.IGNORECASE)
_TO_GB = {"B": 1.0 / 1024 ** 3, "KB": 1.0 / 1024 ** 2, "MB": 1.0 / 1024, "GB": 1.0, "TB": 1024.0}   # binary, as the catalogue
_COUNT_RX = re.compile(r"\A\s*([0-9]{1,9})\s*\Z")


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


def _strict(v, what):
    f = _num(v)
    if f is None:
        raise ValueError("%s must be a finite number >= 0, got %s" % (what, repr(v)[:40]))
    return f


def _count(v):
    """A whole number of connections from a catalogue value ("45", 45); None when it cannot be read."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, str):
        m = _COUNT_RX.match(v)
        return int(m.group(1)) if m else None
    return None


def _gb(x):
    """18.6 -> "18.6 GB", 0.5 -> "512 MB", 0.6 -> "0.6 GB", 12800 -> "12.5 TB"."""
    if x < 1 and abs(x * 1024 - round(x * 1024)) < 1e-6:
        return "%d MB" % round(x * 1024)
    if x >= 1024:
        return "%g TB" % (x / 1024)
    return "%g GB" % x


def parse_size_gb(value):
    """Size of a catalogue value in GB ("512 MB" -> 0.5, " 1280 GB" -> 1280, "12.5 TB" -> 12800), binary units as the
    catalogue's own progressions show (12.5 TB = twice 6400 GB). None for anything else: "Shared", "Unlimited", a bare
    number (the unit would be a guess), a size of zero. A plan whose size is None is skipped, never guessed."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    m = _SIZE_RX.match(value)
    if not m:
        return None
    gb = float(m.group(1)) * _TO_GB[m.group(2).upper()]
    return gb if gb > 0 else None


def _features(plan):
    f = plan.get("features")
    return {str(k).strip().lower(): v for k, v in f.items()} if isinstance(f, dict) else {}


def _first(feats, keys):
    for k in keys:
        if k in feats:
            return feats[k]
    return None


def _name(component):
    for k in ("id", "name", "process"):
        v = component.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _is_singleton(component):
    """An explicit `singleton: True` or `singleton: False` wins (a web app named scheduler-api with `singleton: False` stays a
    normal app); without a real boolean, the name decides: a beat / cron / scheduler process (celery-beat, cron, scheduler…)."""
    if not isinstance(component, dict):
        return False
    flag = component.get("singleton")
    if isinstance(flag, bool):
        return flag
    n = _name(component)
    return bool(n) and any(t in SINGLETON_TOKENS for t in re.split(r"[^a-z0-9]+", n.lower()))


def _is_next(component):
    fw = component.get("framework")
    names = [fw] if isinstance(fw, str) else (fw if isinstance(fw, (list, tuple)) else [])
    return any(isinstance(x, str) and x.strip().lower() in NEXT_NAMES for x in names)


def _next_images(component):
    """True when a Next.js component optimises images or when we do not know (the safe side: Next.js optimises them
    unless told not to, and nano is Killed); False only when the component says `next_image: False`."""
    return _is_next(component) and component.get("next_image") is not False


def _next_images_reason(component):
    return ("Next.js image optimisation: XS minimum, in nano it is Killed then 3 min of outage (test T6)"
            + ("" if component.get("next_image") is True else " [assumed: next/image use not confirmed]"))


def _shape(answers):
    load = answers.get("load") if isinstance(answers, dict) else None
    shape = load.get("shape") if isinstance(load, dict) else None
    return shape if shape in LOAD_SHAPES else "steady"


# ---------------------------------------------------------------------------------------------------- applications
def singleton_apps(components):
    """Names of the components that must run alone in a dedicated app with one instance: beat, cron and scheduler
    processes (or any component flagged `singleton: True`). The split into a separate app is the plan's job."""
    out = []
    for c in components if isinstance(components, (list, tuple)) else []:
        if _is_singleton(c):
            n = _name(c)
            if n and n not in out:
                out.append(n)
    return out


def size_app(component, answers):
    """Production sizing of one application component: {"flavor", "min", "max", "why"}.

    XS at least (nano and pico are for standby or an autoscaling floor), 2 instances by default. The load shape of
    `answers` ("predictable_peaks", "events") gives autoscaling headroom, 2 x the floor, an assumed ceiling to confirm
    with a load test. A singleton (beat, cron, scheduler) is exactly one instance: min == max == 1."""
    c = component if isinstance(component, dict) else {}
    if _is_singleton(c):
        return {"flavor": PROD_FLAVOR, "min": 1, "max": 1,
                "why": "singleton (beat/cron/scheduler): exactly 1 instance, another one would run every task twice (test T2); "
                       "tasks stay idempotent; XS: nano and pico are for standby only"}
    why = ["XS at least in production: nano and pico are for standby or an autoscaling floor (reduced CPU priority)"]
    if _next_images(c):
        why.append(_next_images_reason(c))
    low = PROD_INSTANCES
    high = low
    shape = _shape(answers)
    if shape in ("predictable_peaks", "events"):
        high = low * PEAK_FACTOR
        why.append("%s load: autoscaling %d to %d instances, the ceiling is assumed (confirm with a load test); scale-up comes "
                   "about 1 min after the load starts, scale-down about 11 min 30 after: pre-scale at H-1 for a known event"
                   % (shape.replace("_", " "), low, high))
    why.append("%d instances by default in production" % low)
    return {"flavor": PROD_FLAVOR, "min": low, "max": high, "why": "; ".join(why)}


def poc_plans(prod):
    """The POC derived from a production sizing: everything but the apps is kept as is (the database plans stay the
    production ones), each app drops to nano (XS for Next.js with images) on one instance, no autoscaling.
    Returns a new dict; the input is never changed."""
    out = copy.deepcopy(prod) if isinstance(prod, dict) else {}
    apps = out.get("apps")
    if not isinstance(apps, list):
        return out
    for i, app in enumerate(apps):
        if not isinstance(app, dict):
            continue
        flavor = NEXT_IMAGE_FLAVOR if _next_images(app) else POC_FLAVOR
        was = ""
        if isinstance(app.get("flavor"), str) and app.get("min") == app.get("max") and isinstance(app.get("min"), int):
            was = " (production: %s, %d instance%s)" % (app["flavor"], app["min"], "" if app["min"] == 1 else "s")
        elif isinstance(app.get("flavor"), str) and isinstance(app.get("min"), int) and isinstance(app.get("max"), int):
            was = " (production: %s, %d to %d instances)" % (app["flavor"], app["min"], app["max"])
        reason = "POC: %s on 1 instance, no autoscaling%s" % (flavor, was)
        if flavor == NEXT_IMAGE_FLAVOR:
            reason += "; " + _next_images_reason(app)
        app.update({"flavor": flavor, "min": 1, "max": 1, "why": reason})
    return out


# ------------------------------------------------------------------------------------------------------ databases
def connection_budget(pool, procs, instances):
    """Connections the database must allow: pool x processes x instances x 2. The old instances stay about 2 min during
    a deployment, so their connections count too. `instances` is the maximum the app can reach (autoscaling ceiling).
    Raises ValueError for a value that is not a finite number >= 0, or for a product that is not finite (a guess here
    would size the database wrong)."""
    p, q, n = _strict(pool, "pool"), _strict(procs, "processes"), _strict(instances, "instances")
    total = p * q * n * DEPLOY_FACTOR
    if not math.isfinite(total):
        raise ValueError("pool x processes x instances x %d is not a finite number" % DEPLOY_FACTOR)
    return int(math.ceil(total))


def _price(prices, slug):
    """Hourly price of a plan in `prices` ({slug: eur_per_hour}; the grid keys are lowercased slugs): a number > 0 or None."""
    for key in (slug, slug.lower()):
        p = _num(prices.get(key))
        if p is not None and p > 0:
            return p
    return None


def _order(row):
    """Size, then memory, then connection limit, then slug; what cannot be read sorts last."""
    return (row["size"], math.inf if row["mem"] is None else row["mem"],
            math.inf if row["conns"] is None else row["conns"], row["slug"])


def _result(plan, why, detail=""):
    return {"plan": plan, "why": why, "detail": detail}


def pick_db_plan(plans, data_gb, conns_needed, prices=None):
    """Encrypted dedicated plan that holds the data with a x2 margin and allows `conns_needed` connections.
    Always returns {"plan": slug or None, "why": text, "detail": text}; `detail` is "" unless the reason is "beyond catalogue".

    `plans` is the list of Catalog.dedicated_plans() (PostgreSQL / MySQL plans: "max db size", "memory", "max connection
    limit"). Skipped, never guessed: a shared plan, the DEV plan, a plan without encryption, a plan whose max size cannot be
    read, and, when connections are needed, a plan whose connection limit cannot be read.

    data_gb None (or anything but a finite number >= 0): the size is unknown, 1 GB is assumed, the reason says
    [assumed], and the plan is never the smallest one by default (on PostgreSQL xxs_tny caps at 512 MB). A known size
    gets the x2 margin as is: a tiny database can still land on a small plan, by its real size and not by default.

    conns_needed: connections the plan must allow (see connection_budget). None, or 0, means connections are not checked
    and the reason says so. Anything else that is not a finite number >= 0 (negative, nan, inf, bool, text) raises
    ValueError, like connection_budget.

    prices: optional {slug: eur_per_hour} (the lowercased slugs of the price grid work too). Given, the CHEAPEST plan that
    fits wins; a plan without a price (or a price that is not a number > 0) is skipped; when no plan that fits has a price
    the unpriced order is used and the reason says so. A non-dict raises ValueError. Not given, "smallest" is the order by
    max size, then memory, then connection limit, then slug: a proxy for the price, not the price itself, so a caller that
    holds the grid should pass it. No RAM floor is applied: the spec gives no sourced figure; the reason states the RAM
    and the connection limit of the plan chosen, so the reader sees them.

    The reason is exactly "beyond catalogue" when no plan can serve the request, whether the data is larger than the largest
    plan or only the connections are out of reach; `detail` says which and by how much. The price screen and the human
    hand-off key on that exact reason. With no usable plan at all the reason says so and is not "beyond catalogue"."""
    if conns_needed is None:
        conns, unchecked = 0, True
    else:
        conns = int(math.ceil(_strict(conns_needed, "conns_needed")))
        unchecked = conns == 0
    if prices is not None and not isinstance(prices, dict):
        raise ValueError("prices must be a dict {slug: eur_per_hour} or None, got %s" % type(prices).__name__)
    given = _num(data_gb)
    assumed = given is None
    data = ASSUMED_DATA_GB if assumed else given
    need = data * DATA_MARGIN
    rows = []
    for p in plans if isinstance(plans, (list, tuple)) else []:
        if not isinstance(p, dict) or not isinstance(p.get("slug"), str) or not p["slug"]:
            continue
        if p["slug"] == "dev" or str(p.get("type") or "").strip().lower() == "shared" or not p.get("encryption"):
            continue
        feats = _features(p)
        size = parse_size_gb(_first(feats, SIZE_KEYS))
        if size is None:
            continue
        rows.append({"slug": p["slug"], "size": size, "mem": parse_size_gb(_first(feats, MEMORY_KEYS)),
                     "conns": _count(_first(feats, CONNECTION_KEYS))})
    if not rows:
        return _result(None, "no encrypted dedicated plan with a readable max size in the catalogue read")
    if assumed:
        basis = "data size unknown: [assumed] %g GB taken, x%d margin = %s" % (data, DATA_MARGIN, _gb(need))
    else:
        basis = "%g GB of data x%d margin = %s" % (data, DATA_MARGIN, _gb(need))
    fitting = [r for r in rows if r["size"] >= need]
    if not fitting:
        largest = max(rows, key=lambda r: (r["size"], r["slug"]))
        return _result(None, "beyond catalogue", "%s, more than the largest encrypted dedicated plan read (%s, max DB size %s)"
                       % (basis, largest["slug"], _gb(largest["size"])))
    if conns > 0:
        readable = [r for r in fitting if r["conns"] is not None]
        if not readable:
            return _result(None, "no connection limit readable on the plans that fit the data: %d connections cannot be checked" % conns)
        fitting = [r for r in readable if r["conns"] >= conns]
        if not fitting:
            most = max(readable, key=lambda r: (r["conns"], -r["size"]))
            return _result(None, "beyond catalogue", "%d connections needed, at most %d (%s) on the plans that hold %s"
                           % (conns, most["conns"], most["slug"], _gb(need)))
    best, price = None, None
    if prices is not None:
        priced = [(pr, r) for pr, r in ((_price(prices, r["slug"]), r) for r in fitting) if pr is not None]
        if priced:
            price, best = min(priced, key=lambda x: (x[0], _order(x[1])))
    if best is not None:
        rule = "cheapest encrypted dedicated plan that fits (%g EUR/h)" % price
    else:
        best = min(fitting, key=_order)
        rule = "smallest encrypted dedicated plan that fits (ordered by max size, then RAM; %s)" % (
            "no price for any plan that fits" if prices is not None else "no prices given")
    spec = ["%s RAM" % _gb(best["mem"]) if best["mem"] is not None else "RAM not readable",
            "max %d connections" % best["conns"] if best["conns"] is not None else "connection limit not readable",
            "max DB size " + _gb(best["size"])]
    why = "%s -> %s (%s): %s" % (basis, best["slug"], ", ".join(spec), rule)
    why += "; connections not checked" if unchecked else "; %d connections needed" % conns
    smallest = min(rows, key=_order)
    if assumed and smallest["size"] < need:
        why += "; not the smallest plan by default (%s caps at %s)" % (smallest["slug"], _gb(smallest["size"]))
    return _result(best["slug"], why)
