#!/usr/bin/env python3
"""price.py — read the LIVE Clever Cloud price grid, never a remembered one.

Usage:
  python3 -I price.py [--zone par] [--json] [--build-minutes 5] ITEM [ITEM ...]
  python3 -I price.py [--zone par] --list apps

ITEM = <source>:<flavor-or-plan>[x<count>][@build]
  apps:nano            one nano application instance (billed while it runs)
  apps:nanox3          three nano instances
  apps:m@build         a dedicated M build instance: billed only while a build runs
                       (estimate shown per build of --build-minutes, default 5)
  postgresql:xxs_tny   a PostgreSQL plan (slug from `clever addon providers show postgresql-addon`)
  redis:s_mono
  cellar:storage       Cellar object storage: priced per GB stored and per GB sent (countable items of
                       the grid); shown with its free tier, 0 EUR/h for a small POC
  cellar:outbound@500  Cellar traffic sent out, 500 GB a month, billed by the grid's tiers (cellar.outbound:
                       0.090 EUR/GB up to 10 TB, then 0.070): a monthly volume charge, not an hourly one, so it is
                       in the monthly total and never in the 2-hour POC. Absent from the grid: NOT IN GRID.
                       Applications have no outbound charge: this item exists for Cellar only.
  cellar:storage@20    Cellar storage of 20 GB kept the whole month (720 h), on the grid's cellar.storage tiers
                       (the first 100 MB are free): same rules as cellar:outbound@<GB>.

Reads https://api.clever-cloud.com/v4/billing/price-system (public, no login, read-only).
Billing is per second; a monthly figure assumes 720 h (30 days, like the official calculator). An item ABSENT
from the grid is reported as such, never assumed free (exit 1). Network/API failure, or an answer that holds no application price (empty, or a changed format): exit 2, no figure.
LOC_PRICE_GRID_FILE=<json> replaces the API with a saved grid: TESTS ONLY. The output then says
"OFFLINE GRID, tests only" (never "live"), a warning goes to stderr, and provision.py /
deploy-target.sh refuse --approve on such a price unless LOC_SELFTEST=1 — and both variables are honoured ONLY
when the clever on PATH is the selftest's fake (marker file next to it); with the real clever-tools the grid
file is refused outright and LOC_SELFTEST is ignored.
Shared by the whole skill: plan.py and modules/supabase/templates/deploy-target.sh call it.
"""
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request

API = "https://api.clever-cloud.com/v4/billing/price-system?zone_id={zone}&currency=EUR"
HOURS_PER_MONTH = 720      # 30 days, like the official calculator
VOLUME_RX = re.compile(r"\Acellar:(outbound|storage)@([0-9]{1,12}(?:\.[0-9]{1,6})?)\Z", re.IGNORECASE)


def offline_grid():
    """The saved grid file when LOC_PRICE_GRID_FILE is set AND the clever on PATH is the selftest's fake;
    with the real clever-tools the variable is refused outright (no figure, exit 2) — even for a dry run."""
    f = os.environ.get("LOC_PRICE_GRID_FILE") or None
    if f:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
        from common import fake_clever
        if not fake_clever():
            raise RuntimeError("LOC_PRICE_GRID_FILE refused: the clever on PATH is the real one — a saved grid is for the selftest only; unset it")
    return f


def source_label(zone):
    f = offline_grid()
    return ("file:%s (OFFLINE GRID, tests only)" % f) if f else API.format(zone=urllib.parse.quote(zone, safe=""))


def has_prices(grid):
    """The ONE test of "this grid can be used": it holds at least one application price (a number > 0). load() raises on a grid
    without one, and pricing.price_levels() refuses it: an answer with no usable row is not a price list."""
    apps = grid.get("apps") if isinstance(grid, dict) else None
    return isinstance(apps, dict) and any(isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p > 0 for p in apps.values())


def price_gaps(missing, unpriced=()):
    """What the grid could not price: ONE rule for plan.py and provision.py. `missing` = items NOT IN GRID (a gap that blocks an
    approval: a missing item is never counted as free); `unpriced` = the add-ons the grid has no price source for (Keycloak, FS Bucket,
    Matomo...), by component id: named wherever a price is shown and bound to the approval token, but they do not block a POC.
    Returns {"missing": sorted, "unpriced": sorted, "all": sorted union, "complete": True when both are empty}."""
    miss, unp = sorted(set(str(x) for x in missing)), sorted(set(str(x) for x in unpriced))
    return {"missing": miss, "unpriced": unp, "all": sorted(set(miss) | set(unp)), "complete": not (miss or unp)}


def load(zone):
    f = offline_grid()
    if f:
        print("price.py: WARNING — LOC_PRICE_GRID_FILE is set: prices come from a saved file (OFFLINE GRID, tests only), not from the live grid", file=sys.stderr)
        data = json.load(open(f, encoding="utf-8"))
    else:
        with urllib.request.urlopen(API.format(zone=urllib.parse.quote(zone, safe="")), timeout=20) as r:
            data = json.load(r)
    grid = {}
    for row in data.get("runtime", []):
        if row.get("time_unit") != "PT1H":
            continue
        grid.setdefault(row["source"].lower(), {})[row["flavor"].lower()] = float(row["price"])
    for c in data.get("countable", []):
        if c.get("service") in ("cellar.storage", "cellar.outbound"):
            tiers = [(p.get("max_quantity"), float(p.get("price") or 0)) for p in c.get("price_plans", [])]
            grid.setdefault("_countable", {})[c["service"]] = {"tiers": tiers, "per_bytes": (c.get("data_quantity_for_price") or {}).get("quantity")}
    if "cellar.storage" in grid.get("_countable", {}):
        grid.setdefault("cellar", {})["storage"] = 0.0       # first tier (free) for a POC; details below
    if not has_prices(grid):
        raise RuntimeError("the answer holds no application price (empty, or a format this script does not read)")
    grid["_meta"] = {"zone": zone}              # which zone this grid is for, and whether it is a saved test file: pricing.py labels its figures with it
    if f:
        grid["_meta"]["offline"] = True
    return grid


def parse(item):
    build = item.endswith("@build")
    if build:
        item = item[: -len("@build")]
    source, _, rest = item.partition(":")
    count = 1
    if "x" in rest and rest.rsplit("x", 1)[1].isdigit():
        rest, n = rest.rsplit("x", 1)
        count = int(n)
    return source.lower(), rest.lower(), count, build


def tier_cost(countable, gb):
    """EUR for `gb` GB (the grid's unit: 10^9 bytes) on the graduated tiers of one countable item of the grid,
    {"tiers": [(max_quantity or None, price per `per_bytes`)], "per_bytes": N} as load() reads it. None when the item is
    absent or cannot be read (a tier that is missing is never replaced by a remembered price)."""
    if not isinstance(countable, dict):
        return None
    per, tiers = countable.get("per_bytes"), countable.get("tiers")
    if isinstance(per, bool) or not isinstance(per, (int, float)) or not per > 0 or not tiers:
        return None
    total = float(gb) * 1e9
    cost, prev = 0.0, 0.0
    for upper, price in tiers:
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0 or not math.isfinite(price):
            return None
        top = math.inf if upper is None else float(upper)
        if top <= prev:
            return None
        if total > prev:
            cost += (min(total, top) - prev) / float(per) * price
        if total <= top:
            return cost
        prev = top
    return None        # more traffic than the last tier covers and no open-ended tier: no figure


def price_items(grid, items, build_minutes=5):
    """The hourly computation, pure: price `items` on a grid already read (no network, no environment). Returns
    {"lines", "missing", "run_h", "build_h", "volume_month"}: one line per item in the order given, `run_h` the EUR/h of the
    items that run, `build_h` of the @build ones, `volume_month` the EUR a month of the items billed per volume
    (cellar:outbound@<GB>, cellar:storage@<GB>). quote() and pricing.py both build on it."""
    lines, run_h, build_h, volume, missing = [], 0.0, 0.0, 0.0, []
    for item in items:
        vol = VOLUME_RX.match(item)
        if vol:
            kind, gb = vol.group(1).lower(), float(vol.group(2))
            cnt = grid.get("_countable")
            cost = tier_cost(cnt.get("cellar." + kind) if isinstance(cnt, dict) else None, gb)
            if cost is not None and kind == "storage":
                cost *= HOURS_PER_MONTH            # the storage tiers are priced per GB and per hour
            if cost is None:
                missing.append(item)
                lines.append({"item": item, "in_grid": False})
            else:
                volume += cost
                lines.append({"item": item, "in_grid": True, "eur_per_hour": 0.0, "count": 1, "build_only": False,
                              "volume_kind": kind, "volume_gb": gb, "eur_per_month_if_never_stopped": round(cost, 2)})
            continue
        src, flavor, count, build = parse(item)
        table = None if src.startswith("_") else grid.get(src)        # "_meta" and "_countable" are not price lists
        price = table.get(flavor) if isinstance(table, dict) else None
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0:
            price = None
        if price is None:
            missing.append(item)
            lines.append({"item": item, "in_grid": False})
            continue
        try:
            h = price * count
            finite = math.isfinite(h * HOURS_PER_MONTH)
        except OverflowError:          # a count too big for a float
            finite = False
        if not finite:                 # a price this size is no price: reported as missing, never an exception or an infinite figure
            missing.append(item)
            lines.append({"item": item, "in_grid": False})
            continue
        line = {"item": item, "in_grid": True, "eur_per_hour": round(h, 6), "count": count, "build_only": build}
        if src == "cellar":
            try:
                cnt = grid.get("_countable", {})
                st = cnt.get("cellar.storage", {}).get("tiers", [])
                ob = cnt.get("cellar.outbound", {}).get("tiers", [])
                free = next((q for q, pr in st if pr == 0 and q), None)
                nxt = next((pr for q, pr in st if pr > 0), None)
                line["note"] = "free up to %s MB stored; then ~%.4f EUR/GB/month; outbound ~%.2f EUR/GB" % (
                    int(free / 1e6) if free else "?", (nxt or 0) * HOURS_PER_MONTH, ob[0][1] if ob else 0)
            except (AttributeError, TypeError, ValueError, IndexError):
                pass                               # tiers this script cannot read: no note, the price itself stands
        if build:
            build_h += h
            line["eur_per_build"] = round(h * build_minutes / 60.0, 4)
        else:
            run_h += h
            line["eur_per_month_if_never_stopped"] = round(h * HOURS_PER_MONTH, 2)
        lines.append(line)
    return {"lines": lines, "missing": missing, "run_h": run_h, "build_h": build_h, "volume_month": volume}


def quote(zone, items, build_minutes=5, grid=None):
    """Return a dict: lines, totals. Raises on network failure (caller decides)."""
    grid = grid if grid is not None else load(zone)
    core = price_items(grid, items, build_minutes)
    run_h, build_h = core["run_h"], core["build_h"]
    return {
        "zone": zone, "source": source_label(zone), "offline_grid": bool(offline_grid()), "lines": core["lines"], "missing": core["missing"],
        "run_eur_per_hour": round(run_h, 6),
        "run_eur_per_month_if_never_stopped": round(run_h * HOURS_PER_MONTH + core["volume_month"], 2),
        "volume_eur_per_month": round(core["volume_month"], 2),
        "poc_2h_eur": round(run_h * 2, 2),
        "build_eur_per_hour": round(build_h, 6),
        "build_minutes_assumed": build_minutes,
        "build_eur_per_build": round(build_h * build_minutes / 60.0, 4),
    }


def human(q):
    kind = "OFFLINE saved grid, TESTS ONLY — not a live price" if q.get("offline_grid") else "live public grid"
    out = ["zone=%s — %s (%s), EUR excl. taxes; credits/discounts: see the Console" % (q["zone"], kind, q["source"])]
    for ln in q["lines"]:
        if not ln["in_grid"]:
            out.append("  %-26s NOT IN GRID — unknown slug or not priced here: confirm in the Console before quoting" % ln["item"])
        elif ln.get("volume_gb") is not None:
            out.append("  %-26s per volume   ~%.2f EUR/month for %g GB %s (billed per GB by the grid's tiers, not per hour; not in the 2-hour POC)" % (
                ln["item"], ln["eur_per_month_if_never_stopped"], ln["volume_gb"], "sent" if ln["volume_kind"] == "outbound" else "stored for the month"))
        elif ln["build_only"]:
            out.append("  %-26s %.4f EUR/h while building only (~%.4f EUR per %d-min build)" % (
                ln["item"], ln["eur_per_hour"], ln["eur_per_build"], q["build_minutes_assumed"]))
        else:
            out.append("  %-26s %.4f EUR/h   ~%.2f EUR/month if never stopped%s" % (
                ln["item"], ln["eur_per_hour"], ln["eur_per_month_if_never_stopped"], ("  (" + ln["note"] + ")") if ln.get("note") else ""))
    out.append("  RUNNING TOTAL              %.4f EUR/h   ~%.2f EUR/month   | a 2-hour POC ~%.2f EUR" % (
        q["run_eur_per_hour"], q["run_eur_per_month_if_never_stopped"], q["poc_2h_eur"]))
    if q["build_eur_per_hour"]:
        out.append("  + builds                   ~%.4f EUR per build of %d min" % (q["build_eur_per_build"], q["build_minutes_assumed"]))
    return "\n".join(out)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    zone, as_json, minutes = "par", False, 5
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--zone", "--build-minutes", "--list") and i + 1 >= len(argv):
            print("error: %s needs a value (see --help)" % a, file=sys.stderr)
            return 2
        if a == "--zone":
            zone = argv[i + 1]
            i += 2
        elif a == "--json":
            as_json = True
            i += 1
        elif a == "--build-minutes":
            if not argv[i + 1].isdigit():
                print("error: --build-minutes must be a whole number", file=sys.stderr)
                return 2
            minutes = int(argv[i + 1])
            i += 2
        else:
            args.append(a)
            i += 1
    try:
        grid = load(zone)
    except Exception as exc:  # network down, API changed: say it, do not guess
        msg = "price grid unavailable (%s). Do not quote a price: point the developer to the Console pricing." % exc
        print(json.dumps({"error": msg}) if as_json else msg)
        return 2
    if args[:1] == ["--list"]:
        src = args[1].lower()
        rows = [] if src.startswith("_") else sorted(((f, p) for f, p in grid.get(src, {}).items() if not f.startswith("custom")), key=lambda kv: kv[1])
        if as_json:
            print(json.dumps({"zone": zone, "source": src, "flavors": [{"slug": f, "eur_per_hour": p} for f, p in rows]}, indent=2))
        else:
            for f, p in rows:
                print("%s:%-16s %.4f EUR/h  ~%.2f EUR/month" % (src, f, p, p * HOURS_PER_MONTH))
        return 0
    q = quote(zone, args, minutes, grid)
    print(json.dumps(q, indent=2) if as_json else human(q))
    return 1 if q["missing"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
