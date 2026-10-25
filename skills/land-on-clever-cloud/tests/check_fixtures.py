#!/usr/bin/env python3
"""check_fixtures.py — regression checks of the provenance choice, the verdict and the Vision on the
fixtures of tests/blanc-fixtures.json (offline: scan.sh + plan.py --no-live --no-price, nothing created).

  python3 -I tests/check_fixtures.py                 # build every fixture as a git repo in a temp folder
  python3 -I tests/check_fixtures.py --corpus DIR    # same expectations on real checkouts DIR/<id> (read only)

For each fixture: expected primary provenance, mode, verdict (exact or prefix), lines the Vision must
contain and must not contain, no app without a runtime type in the provision spec, and the SAME plan on
two runs (each run is a new process: python3 -I ignores PYTHONHASHSEED, so set and dict orders differ).
Prints "PASS …" / "FAIL …" lines. Exit 0 if all pass.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPTS = os.path.join(SKILL, "scripts")


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


def plan(repo, out):
    rc, txt = run(["bash", os.path.join(SCRIPTS, "scan.sh"), repo, "--out", os.path.join(out, "scan.json")])
    if rc != 0:
        return None, None, "scan failed: " + txt[-200:]
    rc, vision = run([sys.executable, "-I", os.path.join(SCRIPTS, "plan.py"), "--scan", os.path.join(out, "scan.json"), "--zone", "par",
                      "--health", "no", "--no-live", "--no-price", "--out", os.path.join(out, "plan.json")])
    if rc != 0:
        return None, None, "plan failed: " + vision[-300:]
    return json.load(open(os.path.join(out, "plan.json"))), vision, None


def stable(p):
    p = dict(p)
    p.pop("catalog_sources", None)
    p.pop("price", None)
    p.pop("written_at", None)
    return json.dumps(p, sort_keys=True)


def check(fid, fx, repo, tmp):
    e = fx["expect"]
    bad = []
    p, vision, err = plan(repo, tmp)
    if err:
        return [err]
    if p["primary_provenance"] != e["primary"]:
        bad.append("primary %s, expected %s" % (p["primary_provenance"], e["primary"]))
    if p["mode"] != e["mode"]:
        bad.append("mode %s, expected %s" % (p["mode"], e["mode"]))
    if e.get("verdict") and p["verdict"] != e["verdict"]:
        bad.append("verdict %r, expected %r" % (p["verdict"], e["verdict"]))
    if e.get("verdict_prefix") and not p["verdict"].startswith(e["verdict_prefix"]):
        bad.append("verdict %r, expected %r…" % (p["verdict"], e["verdict_prefix"]))
    for h in e.get("has", []):
        if h not in vision:
            bad.append("Vision lacks %r" % h)
    for n in e.get("no", []):
        if n in vision:
            bad.append("Vision has %r" % n)
    for a in p["provision"]["apps"]:
        if not a.get("type"):
            bad.append("app %s has no runtime type" % a["alias"])
    if len(vision.splitlines()) > 40:
        bad.append("Vision of %d lines (> 40)" % len(vision.splitlines()))
    p2, _, err2 = plan(repo, tmp + "-2")
    if err2 or stable(p) != stable(p2):
        bad.append("two runs gave different plans (nondeterministic)")
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
