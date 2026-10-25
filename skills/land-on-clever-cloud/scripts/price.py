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

Reads https://api.clever-cloud.com/v4/billing/price-system (public, no login, read-only).
Billing is per second; a monthly figure assumes 730 h. An item ABSENT from the grid is
reported as such, never assumed free (exit 1). Network/API failure: exit 2, no figure.
LOC_PRICE_GRID_FILE=<json> replaces the API with a saved grid: TESTS ONLY. The output then says
"OFFLINE GRID, tests only" (never "live"), a warning goes to stderr, and provision.py /
deploy-target.sh refuse --approve on such a price unless LOC_SELFTEST=1 — and both variables are honoured ONLY
when the clever on PATH is the selftest's fake (marker file next to it); with the real clever-tools the grid
file is refused outright and LOC_SELFTEST is ignored.
Shared by the whole skill: plan.py and modules/supabase/templates/deploy-target.sh call it.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

API = "https://api.clever-cloud.com/v4/billing/price-system?zone_id={zone}&currency=EUR"
HOURS_PER_MONTH = 730


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


def quote(zone, items, build_minutes=5, grid=None):
    """Return a dict: lines, totals. Raises on network failure (caller decides)."""
    grid = grid if grid is not None else load(zone)
    lines, run_h, build_h, missing = [], 0.0, 0.0, []
    for item in items:
        src, flavor, count, build = parse(item)
        price = grid.get(src, {}).get(flavor)
        if price is None:
            missing.append(item)
            lines.append({"item": item, "in_grid": False})
            continue
        h = price * count
        line = {"item": item, "in_grid": True, "eur_per_hour": round(h, 6), "count": count, "build_only": build}
        if src == "cellar":
            cnt = grid.get("_countable", {})
            st = cnt.get("cellar.storage", {}).get("tiers", [])
            ob = cnt.get("cellar.outbound", {}).get("tiers", [])
            free = next((q for q, pr in st if pr == 0 and q), None)
            nxt = next((pr for q, pr in st if pr > 0), None)
            line["note"] = "free up to %s MB stored; then ~%.4f EUR/GB/month; outbound ~%.2f EUR/GB" % (
                int(free / 1e6) if free else "?", (nxt or 0) * HOURS_PER_MONTH, ob[0][1] if ob else 0)
        if build:
            build_h += h
            line["eur_per_build"] = round(h * build_minutes / 60.0, 4)
        else:
            run_h += h
            line["eur_per_month_if_never_stopped"] = round(h * HOURS_PER_MONTH, 2)
        lines.append(line)
    return {
        "zone": zone, "source": source_label(zone), "offline_grid": bool(offline_grid()), "lines": lines, "missing": missing,
        "run_eur_per_hour": round(run_h, 6),
        "run_eur_per_month_if_never_stopped": round(run_h * HOURS_PER_MONTH, 2),
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
        rows = sorted(((f, p) for f, p in grid.get(src, {}).items() if not f.startswith("custom")), key=lambda kv: kv[1])
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
