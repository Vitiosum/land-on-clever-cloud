#!/usr/bin/env python3
"""check_fixtures.py — regression checks of the provenance choice, the verdict and the Vision on the
fixtures of tests/blanc-fixtures.json (offline: scan.sh + plan.py --no-live --no-price, nothing created).

  python3 -I tests/check_fixtures.py                 # build every fixture as a git repo in a temp folder
  python3 -I tests/check_fixtures.py --corpus DIR    # same expectations on real checkouts DIR/<id> (read only)

For each fixture: expected primary provenance, mode, POC verdict (exact or prefix), production verdict (exact or
prefix), target zone, lines the screens and the detail must contain and must not contain, the losses (every plan says "logs kept 7 days",
plus the fixture's own), the apps on a native runtime when asked, the answers taken by default named, nothing assessed
in report-only mode (no HDS-ready verdict, no migration estimate), no word of a certification (compliant, conforme) and no
HDS zone but parhds in any output, no app without a runtime type in the provision spec, and the SAME plan on two runs (each
run is a new process: python3 -I ignores PYTHONHASHSEED, so set and dict orders differ). A fixture with "answers" runs with
them (--answers, no --health); the others run with --health no. "variants" run the same repository again with other
answers ("answers": null = --health no) and their own expectations, so a rule that depends on an answer is checked on
both sides. The three screens (scripts/lib/screens.py, parcours v2) are checked on every fixture: line limits (15 / 35 / 35),
blockers first, the exact service lines, what the POC proves and does not prove, the migration labelled [estimated] (and not in
report-only mode), the HDS line of each health answer, the production text, repo text between « », nothing promised that does
not exist (BYOK, automatic failover, GPU only on a line that says "not available"), no placement stated in report-only mode.
Prints "PASS …" / "FAIL …" lines. Exit 0 if all pass.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")
sys.path.insert(0, os.path.join(SCRIPTS, "lib"))
NEVER = ("compliant", "conforme", "rbxhds", "grahds")
NOT_PROMISED = ("byok", "automatic failover", "gpu")      # things that do not exist: only on a line that says "not available"
try:
    import screens
    import pricing
except ImportError:             # the check then fails with a message instead of crashing every fixture
    screens = pricing = None


def run(argv, cwd=None, env=None):
    p = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def build(root, files):
    for path, content in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
    run(git + ["init", "-q"], cwd=root)
    run(git + ["add", "-A"], cwd=root)
    run(git + ["commit", "-qm", "fixture"], cwd=root)


def detail_of(report):
    """The detail of REPORT.md (the long form kept out of the screens): the section between "## Details" and the next "## "."""
    head, _, rest = report.partition("\n## Details\n")
    return rest.split("\n## ", 1)[0] if rest else ""


def plan(repo, out, answers=None):
    rc, txt = run(["bash", os.path.join(SCRIPTS, "scan.sh"), repo, "--out", os.path.join(out, "scan.json")])
    if rc != 0:
        return None, None, None, "scan failed: " + txt[-200:]
    who = ["--health", "no"]
    if answers is not None:
        with open(os.path.join(out, "answers.json"), "w", encoding="utf-8") as f:
            json.dump(answers, f)
        who = ["--answers", os.path.join(out, "answers.json")]
    rc, shown = run([sys.executable, "-I", os.path.join(SCRIPTS, "plan.py"), "--scan", os.path.join(out, "scan.json"), "--zone", "par"] + who
                    + ["--no-live", "--no-price", "--out", os.path.join(out, "plan.json"), "--report", os.path.join(out, "REPORT.md")])
    if rc != 0:
        return None, None, None, "plan failed: " + shown[-300:]
    with open(os.path.join(out, "REPORT.md"), encoding="utf-8") as f:
        detail = detail_of(f.read())
    return json.load(open(os.path.join(out, "plan.json"))), shown, detail, None


def stable(p):
    p = dict(p)
    p.pop("catalog_sources", None)
    p.pop("price", None)
    p.pop("written_at", None)
    return json.dumps(p, sort_keys=True)


def check_screens(p, shown):
    """The three screens of plan.json `p` (screens.py, pure) and what plan.py printed (`shown`): the same text, within the limits, with
    the content each screen owes the developer. Returns the list of what is wrong."""
    if screens is None or pricing is None:
        return ["scripts/lib/screens.py is missing: the three screens cannot be checked"]
    bad = []
    rd, cp, pr = screens.screen_read(p), screens.screen_compat(p), screens.screen_price(p)
    for name, text, limit in (("read", rd, 15), ("compat", cp, 35), ("price", pr, 35)):
        n = len(text.splitlines())
        if n > limit:
            bad.append("screen %s has %d lines (> %d)" % (name, n, limit))
        if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text):
            bad.append("screen %s has control characters" % name)
        for line in text.splitlines():
            low = line.lower()
            for w in NOT_PROMISED:
                if w in low and "not available" not in low:
                    bad.append("screen %s names %r on a line that does not say 'not available': %s" % (name, w, line[:100]))
    if shown.strip() != screens.render_all(p).strip():
        bad.append("plan.py printed something else than the three screens")
    report_only = p["mode"] == "report-only"
    health = p.get("health_answer")
    # screen 1: the blockers come first, then the components with their label
    lines = rd.splitlines()
    heads = (screens.HEAD_FIX, screens.HEAD_KEPT, screens.HEAD_PLATFORM)
    blockers = [i for i, l in enumerate(lines) if l in heads]
    if blockers and blockers[0] != 1:
        bad.append("screen read: the blockers do not come right after the title (at line %s)" % blockers)
    if not any(l.startswith("- [read]") or l.startswith("- [assumed]") for l in lines):
        bad.append("screen read: no component with its [read] / [assumed] label")
    # screen 3: verdicts, HDS line of the health answer, losses, production text, repo text between « », placement
    if "POC verdict: %s" % p["verdict"]["poc"] not in cp:
        bad.append("screen compat: the POC verdict is missing")
    if not any(l.startswith("Production verdict: ") for l in cp.splitlines()):
        bad.append("screen compat: the production verdict is missing")
    elif p["verdict"]["production"].split(" — ")[0] not in cp:
        bad.append("screen compat: the production text %r is not shown" % p["verdict"]["production"].split(" — ")[0])
    if report_only:
        want = "HDS-ready architecture: not assessed (report only)"
    elif health in ("yes", "unsure"):
        want = "HDS-ready architecture: "
    else:
        want = "can already run in HDS"
    if want not in cp:
        bad.append("screen compat lacks %r" % want)
    if not report_only and health == "no" and "HDS-ready architecture:" in cp:
        bad.append("screen compat: health no must show the asset, not the HDS-ready verdict")
    if "logs kept 7 days" not in cp:
        bad.append("screen compat: the losses are not shown (logs kept 7 days)")
    for row in p.get("compat") or []:
        for line in cp.splitlines():
            if row["today"][:20] in line and "«" not in line:
                bad.append("screen compat: repo text not between « »: %s" % line[:100])
    if "«HDS contract" in cp or "«report only" in cp or "«HDS-ready" in cp:
        bad.append("screen compat: our own sentence is inside « »")
    for name, text in (("read", rd), ("compat", cp), ("price", pr)):
        for l in text.splitlines():
            if l.startswith("|") and (not l.endswith("|") or l.count("|") != 5):
                bad.append("screen %s: a table row is broken: %s" % (name, l[:100]))
    place = [l for l in cp.splitlines() if l.startswith("Placement: ")]
    if report_only:
        if place != ["Placement: not assessed (report only)"] or "hosted in Paris" in (rd + cp + pr):
            bad.append("screen compat: a placement is stated although nothing was assessed: %s" % place)
    elif not place:
        bad.append("screen compat: no placement line")
    elif "health" in (p.get("answers_assumed") or []) and p.get("target_zone") == p.get("hds_zone") and "[assumed]" not in place[0]:
        bad.append("screen compat: the HDS placement rests on an assumed health answer and does not say so: %s" % place[0])
    # screen 4: the exact service lines, what the POC proves and does not prove, the migration
    for t in (pricing.TEXT_SUPPORT, pricing.TEXT_PREMIUM):
        if t not in pr:
            bad.append("screen price lacks the service line %r" % t[:40])
    if (pricing.TEXT_HDS in pr) != (health != "no"):
        bad.append("screen price: the HDS line must show exactly when health data is yes or not sure (health %s)" % health)
    if "priced in parhds" in pr or "priced in zone" in pr:
        bad.append("screen price: the price label reads as if HDS were included")
    for t in ("Proves: ", "Does not prove: ", "Existing cost, same resources: ", "Existing cost, recommended: "):
        if t not in pr:
            bad.append("screen price lacks %r" % t)
    reply = [l for l in pr.splitlines() if l.startswith("Reply: ")]
    if len(reply) != 1:
        bad.append("screen price: %d Reply lines" % len(reply))
    elif report_only:
        if reply[0] != "Reply: report only (this provenance is not covered yet)":
            bad.append("screen price: report-only Reply is %r" % reply[0])
    elif not isinstance(p.get("prices"), dict):
        # no price was read: never a "go". A blocking point of the POC comes first (settle it), else the price comes first
        want = "Reply: settle the blocking" if p.get("blocking") else "Reply: no go yet"
        if not reply[0].startswith(want):
            bad.append("screen price: no price was read, the Reply should start %r: %r" % (want, reply[0]))
    if report_only:
        if "[estimated]" in pr or "Migration: not assessed (report only)" not in pr:
            bad.append("screen price: report only must say the migration is not assessed, with no estimate")
        if "€/h" in pr or "€/month" in pr.replace(pricing.TEXT_PREMIUM, "").replace(pricing.TEXT_SUPPORT, ""):
            bad.append("screen price: a price is quoted in report-only mode")
    else:
        if "[estimated]" not in pr:
            bad.append("screen price: the migration is not labelled [estimated]")
        if not any(l.startswith("Talk to Clever") for l in pr.splitlines()):
            bad.append("screen price: no 'Talk to Clever' block")
    return bad


def verify(p, shown, detail, e):
    """The expectations `e` on one run: plan.json `p`, what plan.py printed (the three screens) and the detail kept in REPORT.md.
    Every key of `e` is optional but primary and mode."""
    bad = check_screens(p, shown)
    vision = shown + "\n" + detail      # "has" / "no" apply to what the developer sees and to the detail behind it
    if "go_without" in e and screens is not None and screens._go_without(p) != e["go_without"]:
        bad.append("what the developer may leave out is %s, expected %s" % (screens._go_without(p), e["go_without"]))
    if "read_first" in e:
        rd = screens.screen_read(p).splitlines() if screens else []
        if len(rd) < 3 or rd[1] != screens.HEAD_FIX or e["read_first"] not in rd[2]:
            bad.append("screen read does not start with the blocker %r: %s" % (e["read_first"], rd[:3]))
    v = p["verdict"]
    if not (isinstance(v, dict) and isinstance(v.get("poc"), str) and isinstance(v.get("production"), str) and isinstance(v.get("production_blockers"), list)):
        return ["verdict is not {poc, production, production_blockers}: %r" % (v,)]
    if p["primary_provenance"] != e["primary"]:
        bad.append("primary %s, expected %s" % (p["primary_provenance"], e["primary"]))
    if p["mode"] != e["mode"]:
        bad.append("mode %s, expected %s" % (p["mode"], e["mode"]))
    if e.get("verdict") and v["poc"] != e["verdict"]:
        bad.append("verdict %r, expected %r" % (v["poc"], e["verdict"]))
    if e.get("verdict_prefix") and not v["poc"].startswith(e["verdict_prefix"]):
        bad.append("verdict %r, expected %r…" % (v["poc"], e["verdict_prefix"]))
    if e.get("production") and v["production"] != e["production"]:
        bad.append("production verdict %r, expected %r" % (v["production"], e["production"]))
    if e.get("production_prefix") and not v["production"].startswith(e["production_prefix"]):
        bad.append("production verdict %r, expected %r…" % (v["production"], e["production_prefix"]))
    if e.get("target_zone") and p.get("target_zone") != e["target_zone"]:
        bad.append("target zone %r, expected %r" % (p.get("target_zone"), e["target_zone"]))
    for h in e.get("has", []):
        if h not in vision:
            bad.append("Vision lacks %r" % h)
    for n in e.get("no", []):
        if n in vision:
            bad.append("Vision has %r" % n)
    losses = " | ".join(x.get("text", "") for x in p.get("losses") or [] if isinstance(x, dict))
    for h in ["logs kept 7 days"] + e.get("losses_has", []):
        if h not in losses:
            bad.append("losses lack %r (%s)" % (h, losses))
    for n in e.get("losses_no", []):
        if n in losses:
            bad.append("losses have %r" % n)
    if e.get("apps_native"):
        apps = [c for c in p["components"] if c["kind"] in ("web", "worker")]
        if not apps or any(c["target"] != "native" for c in apps):
            bad.append("apps not on a native runtime: %s" % [(c["id"], c["target"]) for c in apps])
    if not p.get("answers_assumed"):
        bad.append("answers_assumed is empty: the defaults taken are not named")
    h = p.get("hds_ready") or {}
    mig = p.get("migration") or []
    if p["mode"] == "report-only":
        # nothing was assessed: no HDS-ready verdict, no migration estimate
        if h.get("ready") is not None or "HDS-ready architecture: not assessed (report only)" not in vision:
            bad.append("report only but an HDS-ready verdict is given: %r" % (h,))
        if not mig or not all("not assessed (report only)" in x for x in mig):
            bad.append("report only but a migration estimate is given: %r" % (mig,))
    elif not isinstance(h.get("ready"), bool) or len(mig) != 5:
        bad.append("planned, but hds_ready %r / a migration sketch of %d lines" % (h, len(mig)))
    text = (vision + json.dumps(p, ensure_ascii=False)).lower()
    for w in NEVER:
        if w in text:
            bad.append("output says %r" % w)
    for a in p["provision"]["apps"]:
        if not a.get("type"):
            bad.append("app %s has no runtime type" % a["alias"])
    return bad


def check(fid, fx, repo, tmp):
    e = fx["expect"]
    p, shown, detail, err = plan(repo, tmp, fx.get("answers"))
    if err:
        return [err]
    bad = verify(p, shown, detail, e)
    p2, _, _, err2 = plan(repo, tmp + "-2", fx.get("answers"))
    if err2 or stable(p) != stable(p2):
        bad.append("two runs gave different plans (nondeterministic)")
    # variants: the same repository with other answers ("answers": null = --health no), each with its own expectations
    for i, var in enumerate(fx.get("variants", [])):
        os.makedirs(tmp + "-v%d" % i, exist_ok=True)
        pv, sv, dv, errv = plan(repo, tmp + "-v%d" % i, var.get("answers"))
        if errv:
            bad.append("%s: %s" % (var["name"], errv))
            continue
        bad += ["%s: %s" % (var["name"], b) for b in verify(pv, sv, dv, dict(var["expect"], primary=e["primary"], mode=e["mode"]))]
    return bad


def main(argv):
    corpus = argv[argv.index("--corpus") + 1] if "--corpus" in argv else None
    data = json.load(open(os.path.join(HERE, "blanc-fixtures.json"), encoding="utf-8"))["fixtures"]
    fails = 0
    with tempfile.TemporaryDirectory(prefix="fixtures-") as t:
        for fid, fx in sorted(data.items()):
            work = os.path.join(t, fid + "-out")
            os.makedirs(work)
            os.makedirs(work + "-2")
            if corpus:
                repo = os.path.join(corpus, fid) if os.path.isdir(os.path.join(corpus, fid)) else os.path.join(os.path.dirname(corpus.rstrip("/")), fid)
                if not os.path.isdir(repo):
                    print("SKIP  %s: no checkout in %s" % (fid, corpus))
                    continue
            else:
                repo = os.path.join(t, fid)
                os.makedirs(repo)
                build(repo, fx["files"])
            bad = check(fid, fx, repo, work)
            if bad:
                fails += 1
                print("FAIL  %s (%s): %s" % ("corpus " + fid if corpus else fid, fx["expect"]["primary"], " · ".join(bad)))
            else:
                print("PASS  %s%s: %s, %s, Vision lines checked, deterministic" % ("corpus " if corpus else "fixture ", fid, fx["expect"]["primary"], fx["expect"]["mode"]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
