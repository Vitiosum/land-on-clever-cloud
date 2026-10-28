"""provision.py — turn a plan (plan.py --out) into the exact, ordered clever commands, and run them.

Called by scripts/provision.sh. Every command is an argv ARRAY executed with
subprocess.run(argv, shell=False): no shell ever parses a value that came from the scanned
repository (service names, Procfile types, app.json fields are untrusted input). Every value
is validated before use. Every refusal here is an imposed rule of the skill, enforced in code.
"""
import argparse
import json
import os
import re
import shlex
import random
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval  # noqa: E402
from common import (ADDON_VERSION_RX, APP_ID_RX, CONTROL_RX, DEFAULT_DOMAIN_GONE, ENV_NAME_RX, HDS_ZONES, SECRETISH_NAME, SLUG_RX, TOKEN_RX, Catalog,  # noqa: E402
                    TRACKED_STATE_ADVICE, fake_clever, known_plan, selftest_mode, mask, read_ledger, safe_folder, services, tracked_state, zones_snapshot)

ROUTE_PATH_RX = re.compile(r"\A/[A-Za-z0-9/_-]{1,60}\Z")           # a route path read by the scan (plan.py's rule): the only form one of its texts may quote

ENCRYPTABLE = {"postgresql-addon", "mysql-addon", "redis-addon", "mongodb-addon", "es-addon"}


def refuse(msg):
    print("REFUSED: " + msg, file=sys.stderr)
    sys.exit(10)


def a_scripts_dir():
    return os.path.dirname(os.path.abspath(__file__))


def verify_addon(ad, org, want_encryption, zone=None):
    """Reused add-on: read its real plan, zone and encryption, read-only. Never claim 'encrypted'
    without this proof.
    - GET /v4/addon-providers/<p>/addons/<id> for the providers that have it (the encryptable ones:
      PostgreSQL, MySQL, Redis, MongoDB, Elasticsearch) — plan and the encryption feature;
    - GET /v2/organisations/<org>/addons/<id> (or /v2/self/addons/<id>) otherwise — Cellar and the
      others have no v4 endpoint (observed 2026-10-25): plan and zone.
    ok True = verified and identical; ok False = a REAL mismatch (plan, zone, encryption); ok None =
    could not be read (NOT a mismatch: --accept-reused-mismatch does not apply)."""
    rid = find_id("addon", ad["name"], "", org)
    if not rid:     # M2: not in the TARGET organisation's own list — never reused, whatever the flags
        return {"ok": False, "problems": ["add-on %s not found in the target organisation's own list: it is not reused (another owner's add-on is never taken for ours)" % ad["name"]]}
    plan = enc = region = None
    source = None
    if ad["provider"] in ENCRYPTABLE:
        d = _json(["clever", "curl", "https://api.clever-cloud.com/v4/addon-providers/%s/addons/%s" % (ad["provider"], rid)])
        if isinstance(d, dict) and d.get("plan") is not None:
            pl = d.get("plan")
            plan = (pl.get("slug") or pl.get("name")) if isinstance(pl, dict) else pl
            for f in d.get("features") or []:
                if isinstance(f, dict) and str(f.get("name", "")).lower() == "encryption":
                    v = f.get("enabled", f.get("value"))
                    enc = v is True or str(v).lower() in ("true", "enabled", "yes")
            region = d.get("region") or d.get("zone")
            source = "GET /v4/addon-providers/%s/addons/<id>" % ad["provider"]
            own = d.get("ownerId") or d.get("owner_id")
            if own and not owned_by(own, None, None, org):
                return {"ok": False, "id": rid, "problems": ["add-on %s belongs to %s, not to the target organisation: never reused" % (ad["name"], own)]}
    if source is None:
        base = ("https://api.clever-cloud.com/v2/organisations/%s/addons/%s" % (org, rid)) if org and str(org).startswith("orga_") \
            else "https://api.clever-cloud.com/v2/self/addons/%s" % rid
        d = _json(["clever", "curl", base])
        if not isinstance(d, dict) or not d.get("plan"):
            return {"ok": None, "id": rid, "why": "plan and zone could not be read (API v4 and v2): NOT verified — not a mismatch"}
        pl = d.get("plan")
        plan = (pl.get("slug") or pl.get("name")) if isinstance(pl, dict) else pl
        region = d.get("region")
        source = "GET /v2/%s/addons/<id>" % ("organisations/<org>" if org and str(org).startswith("orga_") else "self")
    problems = []
    if not plan or str(plan).lower() != str(ad["plan"]).lower():
        problems.append("plan is %s, the plan expects %s" % (plan or "unknown", ad["plan"]))
    if zone and region and str(region).lower() != str(zone).lower():
        problems.append("zone is %s, the plan expects %s" % (region, zone))
    if want_encryption and enc is False:
        problems.append("reused add-on is NOT encrypted: the screens' 'encrypted' would be false")
    if want_encryption and enc is None and not problems:
        return {"ok": None, "id": rid, "plan": plan, "encrypted": None, "source": source,
                "why": "encryption could not be read (%s): NOT verified — not a mismatch; do not claim 'encrypted'" % source}
    return {"ok": not problems, "id": rid, "plan": plan, "zone": region, "encrypted": enc, "problems": problems, "source": source}


def check_slug(kind, v):
    if not isinstance(v, str) or not SLUG_RX.match(v):
        refuse("%s %r is not a safe name (^[a-z0-9][a-z0-9-]{0,62}$): regenerate the plan with plan.py (it fits names in 63 characters; --name sets a shorter prefix)" % (kind, v))


def check_token(kind, v):
    if not isinstance(v, str) or not TOKEN_RX.match(v):
        refuse("%s %r contains unexpected characters" % (kind, v))


def check_value(kind, v):
    if CONTROL_RX.search(str(v)):
        refuse("%s contains a control or invisible character (Unicode Cc, Cf, Zl, Zp: \\n, \\t, a zero-width joiner, a soft hyphen, a BOM…): refused" % kind)


def validate(spec, a):
    check_token("zone", spec.get("zone", ""))
    for o in (a.org, a.second_org):
        if o:
            check_token("organisation", o)
    for ap in spec.get("apps", []):
        check_slug("app name", ap.get("name"))
        check_slug("alias", ap.get("alias"))
        for k in ("type", "flavor", "build_flavor"):
            check_token(k, ap.get(k))
        if not str(ap.get("instances", 1)).isdigit():
            refuse("instances must be a number")
        for k, v in (ap.get("env") or {}).items():
            if not ENV_NAME_RX.match(k):
                refuse("environment variable name %r is invalid (^[A-Z_][A-Z0-9_]*$)" % k)
            check_value("value of %s" % k, v)
            if str(v).startswith("-"):      # clever-tools 5.2.0 reads `clever env set NAME -x` as options (cliparse, then minimist): the value would be lost
                refuse("the value of %s starts with '-': the argument parser of clever-tools 5.2.0 would read it as an option and lose it — set it by hand in the Console, or change it" % k)
            if k == "APP_FOLDER" and safe_folder(v) is None:
                refuse("APP_FOLDER %r is not a plain relative folder" % v)
        for n in ap.get("secrets_generate") or []:
            if not ENV_NAME_RX.match(str(n)):
                refuse("secret name %r is invalid" % n)
        sc = ap.get("secrets_copy")
        if sc is not None:
            if not (isinstance(sc, dict) and isinstance(sc.get("names"), list) and sc["names"]):
                refuse("secrets_copy of app %s is not {from, names}" % ap.get("alias"))
            check_slug("secrets_copy source", sc.get("from"))
            if sc["from"] == ap.get("alias"):
                refuse("app %s copies its secrets from itself" % ap.get("alias"))
            for n in sc["names"]:
                if not isinstance(n, str) or not ENV_NAME_RX.match(n):
                    refuse("secret name %r (secrets_copy) is invalid" % n)
    for ad in spec.get("addons", []):
        check_slug("add-on name", ad.get("name"))
        for k in ("provider", "plan"):
            check_token(k, ad.get(k))
        if ad.get("zone"):
            check_token("zone", ad["zone"])
        for o in ad.get("options") or []:
            if not re.fullmatch(r"^[a-z0-9_-]+=[a-z0-9_-]+$", str(o)):
                refuse("add-on option %r is invalid" % o)
        if ad.get("version") is not None and not (isinstance(ad["version"], str) and ADDON_VERSION_RX.match(ad["version"])):
            # a version comes from the repository (app.json, scalingo.json, a compose image, render.yaml): only a plain version token reaches --addon-version, and it is never echoed
            refuse("the version of add-on %s is not a plain version number (like 16 or 8.4): regenerate the plan with plan.py" % ad.get("name"))
        for l in ad.get("link") or []:
            check_slug("link alias", l)


def _json(cmd):
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60).stdout
        return json.loads(out.decode() or "null")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def owned_by(owner, group_id, group_name, org):
    """Is this listing entry owned by the target? personal space → a user_… owner; --org X → X (id or name).
    Without --org the CLI lists the apps of EVERY organisation of the account: a same-named app of a
    colleague must never be taken for ours (it would be linked, re-scaled, reconfigured, force-pushed)."""
    if not org:
        return str(owner or group_id or "").startswith("user_")
    return org in (owner, group_id, group_name)


def list_apps(org):
    """[(app_id, name, createdAt, owner)] of the TARGET owner only, via `clever applications list [--org ORG]
    --format json` ([{id, name, applications:[{app_id, org_id, name…}]}] per organisation)."""
    d = _json(["clever", "applications", "list", "--format", "json"] + (["--org", org] if org else []))
    if not isinstance(d, list):
        return None
    return [(ap.get("app_id") or ap.get("id"), ap.get("name"), ap.get("createdAt"), ap.get("org_id") or o.get("id"))
            for o in d if isinstance(o, dict) for ap in (o.get("applications") or [])
            if isinstance(ap, dict) and owned_by(ap.get("org_id"), o.get("id"), o.get("name"), org)]


def addon_owner(x):
    return x.get("ownerId") or x.get("owner_id") or x.get("orgaId") or x.get("organisationId")


def list_addons(org):
    """[(addonId, name, creationDate)] of the TARGET owner only, via `clever addon list [--org ORG] --format json`.
    Without --org the CLI lists the personal space only (observed 2026-10-25); an entry that names another
    owner is dropped anyway — a colleague's same-named add-on is never taken for ours (M2)."""
    d = _json(["clever", "addon", "list", "--format", "json"] + (["--org", org] if org else []))
    if not isinstance(d, list):
        return None
    return [(x.get("addonId"), x.get("name"), x.get("creationDate")) for x in d if isinstance(x, dict)
            and (addon_owner(x) is None or owned_by(addon_owner(x), None, None, org))]


def existing(org_main, org_second):
    """Read-only: names of the apps and add-ons that already exist, PER ORGANISATION (never a union: a name
    of the second organisation must not mark the main one's resource as existing). None = unreadable."""
    out = {}
    for role, org in (("main", org_main), ("secondary", org_second)):
        if role == "secondary" and not org:
            continue
        la, ld = list_apps(org), list_addons(org)
        out[role] = (None if la is None else {x[1] for x in la}, None if ld is None else {x[1] for x in ld})
    return out


def clever_json_apps(repo):
    """The `apps` entries of the repository's .clever.json that are objects; [] for anything else (a missing file, not JSON, a top-level list, "apps": null…): the file
    is the repository's word, it never ends a run with a traceback."""
    try:
        with open(os.path.join(repo, ".clever.json")) as f:
            d = json.load(f)
    except (OSError, ValueError, RecursionError):
        return []
    apps = d.get("apps") if isinstance(d, dict) else None
    return [x for x in apps if isinstance(x, dict)] if isinstance(apps, list) else []


def linked_aliases(repo):
    return {x["alias"] for x in clever_json_apps(repo) if isinstance(x.get("alias"), str)}


def build(plan, a):
    spec = plan["provision"]
    zone = spec["zone"]
    hds = zone in HDS_ZONES
    snap = zones_snapshot()
    if zone not in snap["app_zones"]:
        refuse("unknown zone %s" % zone)
    if zone not in ("par", "parhds") and not plan.get("zone_reason"):
        refuse("Paris first: zone %s was not motivated (plan.py --zone-reason)" % zone)
    validate(spec, a)
    without = set(x for x in (a.without or "").split(",") if x)
    apps = [x for x in spec["apps"] if x["component"] not in without and x["alias"] not in without]
    addons = [x for x in spec["addons"] if x["component"] not in without]
    if any(x.get("org") == "secondary" for x in apps + addons):
        if not hds:
            refuse("a second non-HDS organisation only makes sense in the HDS path")
        if not a.second_org:
            refuse("components are planned in a second non-HDS organisation: pass --second-org <org-id>")
        if a.second_org == a.org:
            refuse("--second-org must differ from --org (the HDS organisation)")
    if not a.org:
        refuse("--org is required: name the organisation that will own (and pay for) the POC — "
               "an org id/name, or --org personal for the personal space. Never a silent default." +
               (" In HDS it must be the organisation holding the HDS contract." if hds else ""))
    if a.org == "personal":
        a.org = None            # the personal space: no --org flag on the commands
        org_label = "personal space (explicit)"
    else:
        org_label = a.org
    cat = Catalog(cache_dir=a.cache_dir, live=not a.no_live, repo_top=a.repo)
    steps = []
    known = {} if a.no_live else existing(a.org, a.second_org)

    def known_names(o, i):     # i: 0 apps, 1 add-ons — the set of the organisation this resource goes to
        return (known.get("secondary" if o == "secondary" else "main") or (None, None))[i]
    aliases = linked_aliases(a.repo)

    def org_args(o):
        org = a.second_org if o == "secondary" else a.org
        return ["--org", org] if org else []

    env_steps, verify_steps = [], []
    for ap in apps:
        env = ap.get("env") or {}
        for k, v in env.items():
            if SECRETISH_NAME.search(k):
                refuse("%s looks like a secret: secrets never go through clever env set in a plan (use env-safe.sh)" % k)
            if k == "NODE_ENV" and str(v) == "production" and ap.get("type") == "node":
                refuse("NODE_ENV=production before the build drops devDependencies the build needs (Node rule)")
        if ap.get("type") == "node" and env.get("CC_POST_BUILD_HOOK") is None and ap.get("has_build_script"):
            refuse("node app %s has a build script but no CC_POST_BUILD_HOOK: the runtime would not build it" % ap["alias"])
        z = "par" if ap.get("org") == "secondary" else zone
        ka = known_names(ap.get("org"), 0)
        skip = ap["alias"] in aliases or (ka is not None and ap["name"] in ka)
        reuse_app = {"kind": "app", "linked": ap["alias"] in aliases} if skip else None
        orgv = (a.second_org if ap.get("org") == "secondary" else a.org) or ""
        steps.append({"id": "create-app-" + ap["alias"], "skip_if_exists": skip, "reuse": reuse_app, "org": ap.get("org", "main"), "zone": z,
                      "cmd": ["clever", "create", "--type", ap["type"], ap["name"], "--alias", ap["alias"], "--region", z] + org_args(ap.get("org")),
                      "ledger": {"kind": "app", "name": ap["name"], "alias": ap["alias"], "org": orgv, "note": "zone=%s type=%s" % (z, ap["type"])}})
        steps.append({"id": "scale-" + ap["alias"], "cmd": ["clever", "scale", "--alias", ap["alias"], "--flavor", ap["flavor"],
                                                            "--build-flavor", ap["build_flavor"], "--instances", str(ap.get("instances", 1))]})
        for k in sorted(env):
            env_steps.append({"id": "env-%s-%s" % (ap["alias"], k), "cmd": ["clever", "env", "set", k, str(env[k]), "--alias", ap["alias"]]})
        for name in ap.get("secrets_generate") or []:
            fmt = (ap.get("secret_formats") or {}).get(name)
            if fmt is not None and fmt not in ("laravel",):
                refuse("unknown secret format %r for %s" % (fmt, name))
            env_steps.append({"id": "secret-%s-%s" % (ap["alias"], name),
                              "cmd": ["bash", os.path.join(a.scripts, "env-safe.sh"), "generate", name, "--alias", ap["alias"]] + (["--format", fmt] if fmt else [])})
        names = sorted(env) + list(ap.get("secrets_generate") or []) + list((ap.get("secrets_copy") or {}).get("names") or [])
        if names:
            verify_steps.append({"id": "verify-env-" + ap["alias"],
                                 "cmd": ["bash", os.path.join(a.scripts, "env-safe.sh"), "verify", ",".join(names), "--alias", ap["alias"]]})
    app_aliases = {x["alias"] for x in apps}
    for ap in apps:        # a beat / scheduler app gets the web app's secrets COPIED (after they were generated there), never generated a second time (env-safe.sh copy: no value shown)
        sc = ap.get("secrets_copy")
        if not sc:
            continue
        if sc["from"] not in app_aliases:
            refuse("app %s copies its secrets from %s, which is not in this run (--without): leave both out, or neither" % (ap["alias"], sc["from"]))
        env_steps.append({"id": "copy-secrets-%s" % ap["alias"],
                          "cmd": ["bash", os.path.join(a.scripts, "env-safe.sh"), "copy", ",".join(sc["names"]), "--from", sc["from"], "--alias", ap["alias"]]})
    for ad in addons:
        z = ad.get("zone") or zone
        if ad.get("org") != "secondary" and hds and z not in HDS_ZONES:
            refuse("add-on %s would be created outside the HDS zone in the HDS organisation" % ad["name"])
        if ad["plan"] == "dev":
            if hds:
                refuse("plan dev refused in an HDS zone (no encryption, no backups)")
            if not a.allow_dev_plan:
                refuse("plan dev for %s: unencrypted and without backups — it would need a migration later. Use a dedicated plan, or pass --allow-dev-plan for a throwaway trial" % ad["name"])
        opts = list(ad.get("options") or [])
        if ad["provider"] in ENCRYPTABLE and ad["plan"] != "dev" and "encryption=true" not in opts:
            opts.append("encryption=true")   # encrypted from creation: encryption cannot be added later
        avail = cat.available(ad["provider"], z)
        if avail is False:
            refuse("%s is not available in zone %s (%s)" % (ad["provider"], z, cat.label(ad["provider"])))
        links = [l for l in ad.get("link") or [] if l in app_aliases]
        cmd = ["clever", "addon", "create", ad["provider"], ad["name"], "--plan", ad["plan"], "--region", z]
        for o in opts:
            cmd += ["--option", o]
        if ad.get("version"):
            cmd += ["--addon-version", ad["version"]]      # validated above (test T4: --addon-version 16 gave PostgreSQL 16.15)
        if links and ad.get("org") != "secondary":
            cmd += ["--link", links[0]]
        cmd += org_args(ad.get("org"))
        kd = known_names(ad.get("org"), 1)
        skip = kd is not None and ad["name"] in kd
        reuse_ad = {"kind": "addon", "link": links[0] if (links and ad.get("org") != "secondary") else None} if skip else None
        if skip:
            org_ = (a.second_org if ad.get("org") == "secondary" else a.org) or None
            reuse_ad["verify"] = verify_addon(ad, org_, "encryption=true" in opts, z)
        steps.append({"id": "create-addon-" + ad["component"], "skip_if_exists": skip, "reuse": reuse_ad, "yes_gated": True, "org": ad.get("org", "main"),
                      "zone": z, "cmd": cmd, "ledger": {"kind": "addon", "name": ad["name"], "alias": "", "org": (a.second_org if ad.get("org") == "secondary" else a.org) or "",
                                                "note": "provider=%s plan=%s zone=%s" % (ad["provider"], ad["plan"], z)}})
        if ad.get("org") == "secondary":
            steps.append({"id": "note-" + ad["component"], "manual": "%s lives in the second org: its credentials reach the HDS app as variables (env-safe.sh push from a local untracked file), never linked, never displayed" % ad["name"]})
            continue
        for l in links[1:]:
            steps.append({"id": "link-%s-%s" % (ad["component"], l), "cmd": ["clever", "service", "link-addon", ad["name"], "--alias", l]})
    steps += env_steps + verify_steps   # order: apps → add-ons → links → env → verify
    quote, price_note, item_orgs = recompute_price(spec, apps, zone, a, without)
    hds_price = hds_line = None
    if hds and quote:               # R38 (e), R37: the HDS price from the same grid read, bound to the token: the yes names it in an HDS zone
        hds_price, hds_line = hds_quote(quote, item_orgs, zone)
    # ONE rule, shared with plan.py (price.price_gaps): an item the grid does not price (`missing`) makes the price incomplete and blocks the
    # approval; an add-on with no grid price source (`unpriced`: Keycloak, FS Bucket...) is named and bound to the token, but does not block a POC
    gaps = _pricemod().price_gaps(quote["missing"] if quote else [], unpriced_addons(spec, without))
    price_unavailable = quote is None and not a.no_live       # the grid could not be read, or holds no application price (R6)
    price_missing = gaps["missing"]                           # never counted as free
    token = None if (price_unavailable or price_missing) else approval.token([           # no complete price, no token
        json.dumps([s.get("cmd") or s.get("manual") for s in steps], sort_keys=True),
        json.dumps([(s.get("reuse") or {}).get("verify") for s in steps], sort_keys=True),
        price_binding(quote, gaps["unpriced"], hds_line)])      # + the skill's own code; never plan.json's price
    for s in steps:
        if s.get("yes_gated"):
            s["cmd_when_approved"] = s["cmd"] + ["--yes"]
    kinds = {c.get("id"): c.get("kind") for c in plan.get("components", [])}
    singles = {c.get("id") for c in plan.get("components", []) if c.get("singleton") is True}
    # the workers first, the web apps, and a beat / scheduler app LAST: it starts after the app that migrates (a Django beat on DatabaseScheduler fails on an unmigrated database)
    order = ([x["alias"] for x in apps if kinds.get(x["component"]) == "worker" and x["component"] not in singles]
             + [x["alias"] for x in apps if kinds.get(x["component"]) != "worker" and x["component"] not in singles]
             + [x["alias"] for x in apps if x["component"] in singles])
    after_deploy = {x["alias"]: "clever domain rm <default domain> --alias %s" % x["alias"] for x in apps if x["component"] in singles}
    # what a no to that removal leaves: the scheduler app serves the web app's routes on its default domain, an env route included when the plan found one
    env_paths = [f_["path"] for f_ in (plan.get("security") or []) if isinstance(f_, dict) and f_.get("id") == "env-route" and isinstance(f_.get("path"), str)
                 and ROUTE_PATH_RX.match(f_["path"])][:3]
    return {"zone": zone, "hds": hds, "org": a.org, "org_label": org_label, "deploy_order": order, "singleton_aliases": sorted(after_deploy), "domain_rm": after_deploy, "env_route_paths": env_paths, "second_org": a.second_org, "steps": steps, "approval_token": token,
            "price": quote, "price_note": price_note, "price_unavailable": price_unavailable, "price_missing": price_missing,
            "price_hds": hds_price, "price_hds_line": hds_line, "price_item_orgs": item_orgs,
            "price_unpriced": gaps["unpriced"], "no_live": bool(a.no_live), "plan_price": plan.get("price"), "without": sorted(without)}


def _pricemod():
    """price.py, the one place that reads the grid and computes the hourly price (also used by plan.py and pricing.py)."""
    d = os.path.dirname(a_scripts_dir())
    if d not in sys.path:
        sys.path.insert(0, d)
    import price as pricemod
    return pricemod


def _price_sources():
    return {v.get("provider"): v.get("price_source") for v in services().values() if v.get("provider")}


def unpriced_addons(spec, without):
    """Component ids of the add-ons the grid has no price source for (rules/_services.json: price_source null), the same ids plan.py lists."""
    src = _price_sources()
    return sorted({str(ad.get("component") or ad.get("name") or "add-on") for ad in spec.get("addons") or []
                   if ad.get("component") not in without and not src.get(ad.get("provider"))})


def price_binding(quote, unpriced, hds_line=None):
    """What the approval token binds about the price: the quote of the commands (without the time it was read), the sorted list of add-ons the
    grid does not price and, in an HDS zone, the HDS price line printed (R38): a yes covers exactly what the developer was shown."""
    return json.dumps({"quote": {k: v for k, v in (quote or {}).items() if k != "read_at"}, "not_priced": sorted(unpriced), "hds": hds_line}, sort_keys=True)


def hds_quote(quote, item_orgs, zone):
    """R37 on the dry run's own quote (R38 e): 1.4 × the HDS organisation's items + the second organisation's items at grid price — per hour, for 2 hours and per
    build — and the fee per HDS organisation; pricing.py's constants are the one source. Returns (dict, the line printed and bound to the token)."""
    import pricing
    run = build = 0.0
    for l, org in zip(quote.get("lines") or [], item_orgs):
        if not l.get("in_grid"):
            continue
        k = 1.0 if org == "secondary" else pricing.HDS_UPLIFT
        if l.get("build_only"):
            build += k * float(l.get("eur_per_build") or 0)
        else:
            run += k * float(l.get("eur_per_hour") or 0)
    h = {"run_eur_per_hour": round(run, 6), "poc_2h_eur": round(run * 2, 2), "build_eur_per_build": round(build, 4), "fee_eur_per_month": pricing.HDS_FEE,
         "uplift": pricing.HDS_UPLIFT, "orgs": pricing.HDS_ORGS}
    line = ("HDS price (zone %s, %g × the HDS organisation's items, the second organisation at grid price): %.4f EUR/h running · 2 h ≈ %.2f EUR · builds ≈ %.3f EUR each "
            "· + €%d/month per HDS organisation — communicated by Clever Cloud on 2026-10-27, not on the public pricing page — confirm on your quote/contract") % (
        zone, pricing.HDS_UPLIFT, h["run_eur_per_hour"], h["poc_2h_eur"], h["build_eur_per_build"], int(pricing.HDS_FEE))
    return h, line


def recompute_price(spec, apps, zone, a, without):
    """É1: the price shown and bound to the token is computed HERE from the commands that will run (flavors,
    instances, add-on plans), never read from plan.json. --no-live: not computed (said so)."""
    if a.no_live:
        return None, "price NOT recomputed (--no-live): read it with price.py before any yes", []
    items, orgs = [], []             # orgs: the organisation of each item ("main" / "secondary"), for the HDS price (R38 e)
    for ap in apps:
        n = int(ap.get("instances", 1) or 1)
        items.append("apps:%s%s" % (str(ap["flavor"]).lower(), ("x%d" % n) if n > 1 else ""))
        orgs.append(ap.get("org") or "main")
    if apps:
        items.append("apps:%s%s@build" % (str(apps[0]["build_flavor"]).lower(), ("x%d" % len(apps)) if len(apps) > 1 else ""))
        orgs.append("main" if any((ap.get("org") or "main") != "secondary" for ap in apps) else "secondary")
    src = _price_sources()
    for ad in spec.get("addons") or []:
        if ad.get("component") in without:
            continue
        ps = src.get(ad.get("provider"))
        if ps == "cellar":
            items.append("cellar:storage")
            orgs.append(ad.get("org") or "main")
        elif ps:
            items.append("%s:%s" % (ps, str(ad["plan"]).lower()))
            orgs.append(ad.get("org") or "main")
    try:
        q = _pricemod().quote(zone, items, 5)       # raises when the grid is unreachable or holds no application price
    except Exception as exc:     # grid unreachable: no price, said so
        return None, "price NOT recomputed (grid unavailable: %s): no yes on an unknown price" % type(exc).__name__, orgs
    q["read_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return q, None, orgs


HINT_RX = re.compile(r"git add \.|git commit -m|git push clever|Your application has been successfully created", re.I)
ID_RX = {"app": re.compile(r"\bapp_[0-9a-f]{8}-[0-9a-f-]{27,}\b"), "addon": re.compile(r"\baddon_[0-9a-f]{8}-[0-9a-f-]{27,}\b")}


def find_id(kind, name, out, org):
    """Real id (app_… / addon_…): from the command output, else from the read-only listings."""
    m = ID_RX[kind].search(out or "")
    if m:
        return m.group(0)
    lst = (list_apps if kind == "app" else list_addons)(org or None) or []
    for x in lst:
        if x[1] == name and ID_RX[kind].match(str(x[0] or "")):
            return x[0]
    return None


def created_at(kind, rid, org):
    for x in (list_apps if kind == "app" else list_addons)(org or None) or []:
        if x[0] == rid:
            c = x[2]
            if isinstance(c, (int, float)):
                return time.strftime("%Y-%m-%d", time.gmtime(c / 1000.0))
            return str(c)[:10] if c else "unknown"
    return "unknown"


RATE_RX = re.compile(r"performed that request too much|too many requests|\b429\b", re.I)


def run_step(argv, repo):
    try:
        p = subprocess.run(argv, cwd=repo, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=1800, shell=False)
        return p.stdout.decode("utf-8", "replace"), p.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc), 127


def rl_state(a, update=None):
    f = os.path.join(a.repo, ".clever-landing", "ratelimit.json")
    st = {"blocked_runs": 0}
    try:
        st = json.load(open(f))
    except (OSError, ValueError):
        pass
    if update is not None:
        st.update(update)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "w") as fh:
            json.dump(st, fh)
    return st


def blocked_by_api(a, step):
    """Exit 12: the Clever API refuses creations. Light diagnostic: a read works, a create does not."""
    st = rl_state(a)
    n = st.get("blocked_runs", 0) + 1
    rl_state(a, {"blocked_runs": n, "last_step": step, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    reads = list_apps(a.org) is not None
    print("BLOCKED %s: the Clever API refuses this creation (rate limit). Diagnostic: reads %s, creation refused." % (
        step, "OK" if reads else "also failing (check login/network)"))
    print("        The limit seems shared by the organisation or the account and can last HOURS (seen > 8 h).")
    if n >= 2:
        print("        2nd blocked run: STOP retrying (each try may extend the window). Report 'blocked by the Clever API: "
              "app-creation limit; reads OK' and ask Clever support; re-run the same --approve later (existing resources are skipped).")
    else:
        print("        Re-run the same --approve later; after a 2nd blocked run, stop and contact Clever support.")
    return 12


def in_ledger(a, rid):
    return any(r["id"] == rid for r in read_ledger(a.repo))


def record(a, ledger, kind, rid, name, note, org):
    if rid and in_ledger(a, rid):
        return
    subprocess.run(["bash", ledger, "add", kind, rid or name, name, note, org], cwd=a.repo, shell=False)


def reuse_existing(s, a, ledger):
    """A resource with the plan's name already exists: link it (apps) / link it to the app (add-ons),
    and record it in the ledger as `reused`, so teardown-plan lists it exactly."""
    L_ = s.get("ledger") or {}
    kind = L_.get("kind")
    rid = find_id(kind, L_.get("name"), "", L_.get("org")) if kind else None
    if not rid:
        print("FAIL  %s: %s exists but its id could not be read (clever %s list --org …): stop" % (s["id"], L_.get("name"), "applications" if kind == "app" else "addon"))
        return 11
    if kind == "app" and not (s.get("reuse") or {}).get("linked"):
        argv = ["clever", "link", rid, "--alias", L_["alias"]] + (["--org", L_["org"]] if L_.get("org") else [])
        print("LINK  %s: %s" % (s["id"], " ".join(shlex.quote(x) for x in argv)))
        out, rc = run_step(argv, a.repo)
        if rc != 0:
            print("FAIL  link: " + mask(out.strip().splitlines()[-1] if out.strip() else "exit %d" % rc, 160))
            return 11
    if kind == "addon" and (s.get("reuse") or {}).get("link"):
        argv = ["clever", "service", "link-addon", L_["name"], "--alias", s["reuse"]["link"]]
        print("LINK  %s: %s" % (s["id"], " ".join(shlex.quote(x) for x in argv)))
        out, rc = run_step(argv, a.repo)
        if rc != 0 and not re.search(r"already", out, re.I):
            print("FAIL  link-addon: " + mask(out.strip().splitlines()[-1] if out.strip() else "exit %d" % rc, 160))
            return 11
    print("REUSE %s: %s (%s) — recorded in the ledger as reused" % (s["id"], L_.get("name"), rid))
    record(a, ledger, kind, rid, L_["name"], "reused; existed before this run (created %s); %s" % (created_at(kind, rid, L_.get("org")), L_.get("note", "")), L_.get("org", ""))
    return 0


def default_domain_rm(repo, alias):
    """`clever domain rm <default domain> --alias <alias>`: the default domain of an app is app-<uuid>.cleverapps.io (seen in tests T1 and T5), the uuid being the one in
    .clever.json; when the file does not give it, a placeholder."""
    dom = "<default domain>"
    for x in clever_json_apps(repo):
        if x.get("alias") == alias and isinstance(x.get("app_id"), str) and APP_ID_RX.match(x["app_id"]):
            dom = "app-%s.cleverapps.io" % x["app_id"][4:]
    return "clever domain rm %s --alias %s" % (shlex.quote(dom) if dom[0] != "<" else dom, alias)


def next_steps(r, a):
    """The exact next commands, from the repository state (shallow or not) and the deploy order."""
    out = subprocess.run(["git", "-C", a.repo, "rev-parse", "--is-shallow-repository"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL).stdout.decode().strip()
    shallow = out == "true"
    dep = os.path.join(a.scripts, "deploy.sh")
    def g(*args):
        return subprocess.run(["git", "-C", a.repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    # #7: say what is TRUE of the repository, never assume the branch exists
    if not g("rev-parse", "--verify", "-q", "clever-poc"):
        state = "branch clever-poc does not exist yet: create it and commit the plan's changes first (SKILL.md step 6a)"
    else:
        base = next((b for b in ("main", "master", "trunk") if g("rev-parse", "--verify", "-q", b)), None)
        ahead = g("rev-list", "--count", "%s..clever-poc" % base) if base else ""
        dirty = g("status", "--porcelain", "--untracked-files=no")
        state = ("branch clever-poc has %s commit(s) ahead of %s" % (ahead, base) if ahead and ahead != "0" else
                 "branch clever-poc exists but has no commit of its own yet: commit the plan's changes first") + \
                ("; uncommitted changes in the working tree are NOT deployed" if dirty else "")
    lines = ["Next (%s), in this order:" % state]
    if shallow:
        lines.append("  # shallow clone: (re)build the snapshot first — after EVERY new commit on clever-poc")
        lines.append("  ROOT=$(git commit-tree \"clever-poc^{tree}\" -m \"Clever POC snapshot\"); git branch -f clever-poc-deploy \"$ROOT\"")
        lines.append("  # (no git identity: git -c user.name=\"Clever POC\" -c user.email=clever-poc@localhost commit-tree …)")
    for al in r.get("deploy_order") or []:
        lines.append("  bash %s --alias %s%s" % (shlex.quote(dep), al, " --branch clever-poc-deploy" if shallow else ""))
    lines.append("  then prove.sh for each app (plan.json → components[].prove; a beat app has no domain: give prove.sh the web app's)")
    singles = [al for al in r.get("deploy_order") or [] if al in (r.get("singleton_aliases") or [])]
    if singles:
        # a beat / scheduler app republishes the web app's routes on its own default domain, and nothing serves users from it; removing that domain is IRREVERSIBLE:
        # asked first, never part of the sequence above
        lines.append("Ask the developer first — IRREVERSIBLE, run only after an explicit yes (not part of the sequence above):")
        for al in singles:
            lines.append("  %s    # nothing serves users from %s (it runs a scheduler): list its domain with `clever domain --alias %s`" % (default_domain_rm(a.repo, al), al, al))
            lines.append("    %s" % scheduler_domain_note(r, al))
    return lines


def scheduler_domain_note(r, alias):
    """What the developer is told before the yes on a scheduler app's domain removal: it cannot be undone, and what a no leaves."""
    paths = [p_ for p_ in (r.get("env_route_paths") or []) if isinstance(p_, str) and ROUTE_PATH_RX.match(p_)]
    return ("%s; declined: %s stays reachable on its default domain, serving the web app's routes%s" % (
        DEFAULT_DOMAIN_GONE, alias, (", the route that returns the environment (%s) included — the web app's proof checks it there (prove.sh --expect-closed): "
                                     "NOT PROVEN while it answers" % ", ".join(paths)) if paths else ""))


def execute(r, a):
    """Run each step as an argv list, shell=False, stop at the first failure, record in the ledger."""
    ledger = os.path.join(a.scripts, "ledger.sh")
    scale = float(os.environ.get("LOC_BACKOFF_SCALE", "1")) if fake_clever() else 1.0   # tests only (fake clever): shortens the waits
    if rl_state(a).get("blocked_runs", 0) >= 2 and not a.force_retry:
        print("REFUSED: 2 runs were already blocked by the Clever API rate limit (.clever-landing/ratelimit.json). "
              "Wait (hours) or contact support; --force-retry to try once more.")
        return 12
    n, created = 0, 0
    for s in r["steps"]:
        n += 1
        if s.get("manual"):
            print("MANUAL %s: %s" % (s["id"], s["manual"]))
            continue
        if s.get("skip_if_exists"):
            rc_ = reuse_existing(s, a, ledger)
            if rc_:
                return rc_
            continue
        argv = s["cmd_when_approved"] if s.get("yes_gated") else s["cmd"]
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            refuse("step %s is not an argv list" % s["id"])
        if argv[:3] == ["clever", "env", "import"]:
            refuse("clever env import")
        is_create = s["id"].startswith("create-")
        if is_create and created:
            time.sleep(3 * scale)            # space consecutive creates (API rate limit)
        print("RUN   %s: %s" % (s["id"], " ".join(shlex.quote(x) for x in argv)))
        out, rc = run_step(argv, a.repo)
        waited = 0.0
        for wait in (20, 40, 80, 160, 320, 640):      # bounded by --max-wait (default 300 s in total)
            if rc == 0 or not RATE_RX.search(out):
                break
            wait = min(wait * random.uniform(0.8, 1.25), max(0.0, a.max_wait - waited))   # jitter
            if wait <= 0:
                break
            L_ = s.get("ledger")
            if is_create and L_ and find_id(L_["kind"], L_["name"], "", L_["org"]):
                print("      rate-limited, but the resource exists: continuing")
                out, rc = "", 0
                break
            print("      Clever API rate limit: waiting %d s (total so far %d s, max %d s), then retrying the same step" % (wait, waited, a.max_wait))
            time.sleep(wait * scale)
            waited += wait
            out, rc = run_step(argv, a.repo)
        if is_create and rc == 0:
            created += 1
        shown = [l for l in out.strip().splitlines() if not HINT_RX.search(l)]   # clever create suggests `git add .`: ignored (rule 12)
        for line in shown[-5:]:
            print("      " + mask(line, 160))
        if rc != 0 and RATE_RX.search(out):
            return blocked_by_api(a, s["id"])
        if rc != 0:
            print("FAIL  %s (exit %d). Stopping. Nothing is rolled back: see ledger.sh teardown-plan" % (s["id"], rc))
            return 11
        if s.get("ledger"):
            L_ = s["ledger"]
            rid = find_id(L_["kind"], L_["name"], out, L_["org"])
            print("      id: %s" % (rid or "not found (record by name)"))
            record(a, ledger, L_["kind"], rid, L_["name"], L_["note"], L_["org"])
    if rl_state(a).get("blocked_runs"):
        rl_state(a, {"blocked_runs": 0})
    print("done: %d step(s)." % n)
    for line in next_steps(r, a):
        print(line)
    return 0


def human(r, approved):
    L = ["# provision plan — zone %s%s — %s" % (r["zone"], " (HDS)" if r["hds"] else "", "WILL RUN" if approved else "DRY RUN (nothing executed)")]
    L.append("organisation: %s%s" % (r.get("org_label") or r["org"], ("   second non-HDS org (par): %s" % r["second_org"]) if r["second_org"] else ""))
    for i, s in enumerate(r["steps"], 1):
        if s.get("manual"):
            L.append("%2d. MANUAL: %s" % (i, s["manual"]))
            continue
        cmd = s["cmd_when_approved"] if (approved and s.get("yes_gated")) else s["cmd"]
        tag = ""
        if s.get("skip_if_exists"):
            ru = s.get("reuse") or {}
            if ru.get("kind") == "app":
                tag = " [exists in %s: reused — %s, recorded in the ledger as reused]" % (
                    r.get("org_label") or r["org"], "already linked" if ru.get("linked") else "will be LINKED with clever link <app_id> --alias")
            else:
                tag = " [exists: reused — %srecorded in the ledger as reused]" % (("linked to %s, " % ru["link"]) if ru.get("link") else "")
                v = ru.get("verify")
                red, end = ("\033[31m", "\033[0m") if sys.stdout.isatty() else ("", "")
                if v is None:
                    tag += "\n      %s!! reused add-on: plan and encryption NOT verified (--no-live): do not claim 'encrypted'%s" % (red, end)
                elif v.get("ok"):
                    tag += "\n      verified (%s): plan %s%s, encryption %s" % (v["source"], v["plan"], (", zone %s" % v["zone"]) if v.get("zone") else "",
                                                                       "enabled" if v.get("encrypted") else "not required")
                elif v.get("ok") is None:
                    tag += "\n      %s!! %s%s" % (red, v.get("why"), end)
                else:
                    for pb in v.get("problems") or [v.get("why")]:
                        tag += "\n      %s!! %s%s" % (red, pb, end)
        tag += " [org %s]" % s["org"] if s.get("org") == "secondary" else ""
        L.append("%2d. %s%s" % (i, " ".join(shlex.quote(c) for c in cmd), tag))
    for al in r.get("singleton_aliases") or []:
        L.append("ASK FIRST (IRREVERSIBLE, only after an explicit yes; not a step of this plan) — after the first deploy of %s (a scheduler app, deployed last): %s — nothing serves "
                 "users from it. %s" % (al, r["domain_rm"][al], scheduler_domain_note(r, al)))
    if not r.get("plan_known", True):
        L.append("!! WARNING: this plan.json was not written by plan.py on this machine (sha256 unknown): it may come from the repository — "
                 "do not show it as the developer's plan; run plan.py again (--execute refuses it)")
    if r.get("price_gap"):
        L.append("!! PRICE GAP: %s — refused with --approve" % r["price_gap"])
    p = r.get("price")
    if not p and r.get("price_note"):
        L.append(r["price_note"])
    if p and r.get("price_missing"):
        L.append("price INCOMPLETE: not in the price grid: %s — a missing item is never counted as free, so no price and no approval token are issued and --approve is refused. "
                 "Confirm those items in the Console, or run plan.py again" % ", ".join(r["price_missing"]))
    elif p:
        L.append("price (%s by provision.py at %s, from the commands above — never from plan.json): %.4f EUR/h running · 2 h ≈ %.2f EUR · ~%.2f EUR/month if never stopped · builds ≈ %.3f EUR each" % (
            "OFFLINE saved grid, TESTS ONLY, read" if p.get("offline_grid") else "grid read", p.get("read_at", "an unknown time"), p["run_eur_per_hour"], p["poc_2h_eur"], p["run_eur_per_month_if_never_stopped"], p["build_eur_per_build"]))
    if p and r.get("price_hds_line") and not r.get("price_missing"):
        L.append(r["price_hds_line"])
    if r.get("price_unpriced"):          # the price above and the token exist only with a complete price (R6): never promised without them
        priced = bool(p) and not r.get("price_missing") and not r.get("price_unavailable") and bool(r.get("approval_token") or approved)
        L.append("NOT PRICED (no public grid price): %s — %s" % (", ".join(r["price_unpriced"]),
                 "billed on top of the price above, confirm in the Console; the approval token covers exactly this list" if priced else
                 "billed on top of any price, confirm in the Console (no complete price here, so no approval token)"))
    if r.get("price_unavailable"):
        L.append("prices unavailable (grid unreachable): no price and no approval token are issued, and --approve is refused — no yes on an unknown price. "
                 "Run the dry run again when the price grid answers")
    elif r.get("price_missing"):
        pass                                  # said above, with the items
    elif not approved:
        L.append("approval token: %s — a CONSISTENCY check, not a proof of consent: it binds these commands, the price above (recomputed here), "
                 "the skill's scripts and rules, and the verified state of reused add-ons. Show this list and price to the developer; only after their "
                 "explicit yes naming plan and price%s, run again with --approve %s%s" % (
                     r["approval_token"], " (in an HDS zone: the HDS price line above)" if r.get("price_hds_line") else "", r["approval_token"],
                     " (this dry run used --no-live: --approve is refused with it outside the selftest — run the dry run again without --no-live)" if r.get("no_live") else ""))
    return "\n".join(L)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--scripts", required=True)
    ap.add_argument("--org")
    ap.add_argument("--second-org")
    ap.add_argument("--without")
    ap.add_argument("--allow-dev-plan", action="store_true")
    ap.add_argument("--approve")
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--cache-dir")
    ap.add_argument("--emit", choices=["human", "json"], default="human")
    ap.add_argument("--execute", action="store_true", help="run the steps (needs --approve)")
    ap.add_argument("--max-wait", type=float, default=300, help="max total wait per step on the API rate limit (s)")
    ap.add_argument("--force-retry", action="store_true", help="try again after 2 runs blocked by the rate limit")
    ap.add_argument("--accept-unverified-reuse", action="store_true", help="reuse add-ons whose plan/zone/encryption could not be read (developer agreed)")
    ap.add_argument("--accept-reused-mismatch", action="store_true", help="reuse add-ons whose plan/encryption differ from the plan (developer agreed)")
    a = ap.parse_args(argv)
    with open(a.plan, encoding="utf-8") as f:
        plan = json.load(f)
    if plan.get("mode") == "report-only":
        refuse("this plan is report-only (provenance not covered yet): nothing to provision")
    tracked = tracked_state(a.repo)
    if tracked:
        refuse("%s tracked by git: the skill's local state (plan, ledger, links, rate-limit memory) never comes from the repository — %s" % (
            ", ".join(tracked), TRACKED_STATE_ADVICE))
    r = build(plan, a)
    approved = bool(a.approve)
    if approved and r["price_unavailable"]:
        refuse("prices unavailable (grid unreachable): no approval token can be issued on an unknown price, so nothing is provisioned — "
               "run the dry run again when the price grid answers, show the price, and ask again")
    if approved and r["price_missing"]:
        refuse("price incomplete (not in the price grid: %s): a missing item is never counted as free, so no approval token can be issued and nothing is "
               "provisioned — confirm those items in the Console, run plan.py again, show the price, and ask again" % ", ".join(r["price_missing"]))
    r["plan_known"] = known_plan(a.repo, a.plan)
    pp, q = r.get("plan_price") or {}, r.get("price") or {}
    if not r["price_missing"] and pp.get("run_eur_per_hour") is not None and q.get("run_eur_per_hour") is not None:        # a partial quote gives no gap figure
        gap = abs(float(pp["run_eur_per_hour"]) - float(q["run_eur_per_hour"]))
        if gap > max(0.0005, 0.02 * float(q["run_eur_per_hour"])):
            r["price_gap"] = "the plan says %.4f EUR/h, the commands it would run cost %.4f EUR/h on the grid" % (float(pp["run_eur_per_hour"]), float(q["run_eur_per_hour"]))
            if approved:
                refuse("price gap: %s — the plan was not computed for these commands; run plan.py again" % r["price_gap"])
    if approved and (q.get("offline_grid") or pp.get("offline_grid")) and not selftest_mode("approval on an offline grid"):
        refuse("the plan's price comes from LOC_PRICE_GRID_FILE (OFFLINE GRID, tests only): never approved — unset it, run plan.py again on the live grid")
    # É1: provision trusts only a plan.json that plan.py wrote on this machine (sha256 recorded in .git/)
    if a.execute and not r["plan_known"] and not selftest_mode("--execute of a plan.json plan.py did not write"):
        refuse("%s was not written by plan.py on this machine (its sha256 is unknown: it came with the repository, or was edited): "
               "run plan.py again, show its screens, then the dry run" % a.plan)
    # --no-live skips the price AND the checks on existing apps and reused add-ons: a dry-run convenience, never a way to approve
    if (approved or a.execute) and a.no_live and not selftest_mode("--approve with --no-live"):
        refuse("--no-live is for dry runs only: it skips the price and the checks on existing apps and reused add-ons, so --approve is refused with it "
               "— run the dry run without --no-live, show the price, and ask again")
    if approved and a.approve != r["approval_token"]:
        refuse("approval token mismatch: the commands, the plan's price, the skill's scripts or a reused add-on's state changed since the dry run — run the dry run again, show it, and ask again")
    unread = [s for s in r["steps"] if (s.get("reuse") or {}).get("verify") and s["reuse"]["verify"].get("ok") is None]
    if a.execute and unread and not a.accept_unverified_reuse:
        refuse("reused add-on(s) could not be verified (%s): %s — this is NOT a mismatch, so --accept-reused-mismatch does not apply. "
               "Check them (clever addon list --org <org>, the Console), then re-run with --accept-unverified-reuse only after the developer accepts "
               "an add-on whose plan, zone or encryption is unverified (and say it on the compatibility screen: never 'encrypted')" % (
                   ", ".join(s["id"] for s in unread), "; ".join(s["reuse"]["verify"].get("why", "") for s in unread)))
    bad = [s for s in r["steps"] if ((s.get("reuse") or {}).get("verify") or {"ok": True}).get("ok") is False]
    if a.execute and bad and not a.accept_reused_mismatch:
        refuse("reused add-on(s) differ from the plan (%s): %s — delete/rename them or re-run with --accept-reused-mismatch after the developer accepts (and say it on the compatibility screen)" % (
            ", ".join(s["id"] for s in bad), "; ".join(p_ for s in bad for p_ in (s["reuse"]["verify"].get("problems") or [s["reuse"]["verify"].get("why", "")]))))
    if a.execute:
        if not approved:
            refuse("--execute needs --approve <token from the dry run>")
        return execute(r, a)
    if a.emit == "json":
        print(json.dumps(r, indent=2))
    else:
        print(human(r, approved))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
