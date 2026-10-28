"""deploy.py — `clever deploy` with a deterministic answer to the non-fast-forward case.

Called by scripts/deploy.sh. argv only (shell=False). A push refused as non-fast-forward is forced
ONLY when the target app is one of this POC's apps: its name starts with the plan's prefix AND it
is in the ledger (created or reused by this skill). Before forcing, the remote head is backed up
to a local branch `clever-backup/<alias>-<UTC time>` and printed. Otherwise: stop with a question,
which prints a token; `--force <token>` (only after the developer said yes) forces that app — the
token binds the app, its deployed head and the local commit, so a stale or wrong one is refused.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import APP_ID_RX, PUSH_URL_RX, TRACKED_STATE_ADVICE, mask, read_ledger, tracked_state  # noqa: E402

NONFF_RX = re.compile(r"non-fast-forward|\[rejected\]|failed to push some refs|Updates were rejected|fetch first", re.I)


def run(argv, cwd, timeout=3600, env=None, stream=False):
    if stream:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env, shell=False)
        out = []
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            out.append(line)
            if not re.search(r"git add \.|git commit -m", line):
                print("      " + mask(line, 200), flush=True)
        p.wait()
        return "\n".join(out), p.returncode
    try:
        p = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=timeout, env=env, shell=False)
        return p.stdout.decode("utf-8", "replace"), p.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc), 127


def clever_app(repo, alias):
    """The app linked under this alias in .clever.json, kept only if its app_id is a valid app_<uuid>."""
    try:
        d = json.load(open(os.path.join(repo, ".clever.json")))
    except (OSError, ValueError):
        return None
    apps = d.get("apps") if isinstance(d, dict) else None
    app = next((x for x in (apps if isinstance(apps, list) else []) if isinstance(x, dict) and x.get("alias") == alias), None)
    return app if app and APP_ID_RX.match(str(app.get("app_id", ""))) else None


def ledger_has(repo, app):
    keys = {app.get("app_id"), app.get("name")} - {None, ""}
    return any(r["kind"] == "app" and (r["id"] in keys or r["name"] in keys) for r in read_ledger(repo))


def push_url(app):
    """The app's git remote: the deploy_url of .clever.json only if it is Clever's push host for THIS app_id."""
    m = PUSH_URL_RX.match(str(app.get("deploy_url", "")))
    return m.group(0) if m and m.group(1) == app.get("app_id") else None


def plan_prefix(path):
    try:
        return json.load(open(path))["provision"]["name_prefix"]
    except (OSError, ValueError, KeyError):
        return None


def backup_remote(repo, app, alias, known_head):
    """Back the remote head (read once by main) up to a local branch. Returns (branch, commit, how) or (None, sha, reason)."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    branch = "clever-backup/%s-%s" % (alias, stamp)
    if known_head:                       # cheapest first: the deployed head may already be in this repository
        _, rc = run(["git", "cat-file", "-e", known_head + "^{commit}"], repo)
        if rc == 0:
            run(["git", "branch", branch, known_head], repo)
            return branch, known_head[:9], "deployed head, present locally"
    url = push_url(app)
    if url:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        _, rc = run(["git", "fetch", "--no-tags", "--", url, "+refs/heads/master:refs/heads/" + branch], repo, timeout=180, env=env)
        if rc == 0:
            sha, _ = run(["git", "rev-parse", "--short", branch], repo)
            return branch, sha.strip(), "fetched from the app's git remote"
        return None, known_head, "the remote head could not be fetched from %s and is not in this repository" % url
    return None, known_head, ("no valid Clever push URL for this app in .clever.json (expected https://push-…services.clever-cloud.com/<app_id>.git)"
                              " and the remote head is not in this repository")


def remote_head(repo, alias):
    """Last OK DEPLOY commit of the app (clever activity --format json), or None for a brand-new app."""
    out, rc = run(["clever", "activity", "--alias", alias, "--format", "json"], repo, timeout=60)
    try:
        acts = [x for x in json.loads(out) if isinstance(x, dict) and x.get("commit") and x.get("action") == "DEPLOY"
                and str(x.get("state", "")).upper() == "OK"]
    except (ValueError, TypeError):
        return None, False
    if not acts:
        return None, True
    acts.sort(key=lambda x: int(x.get("date") or 0))
    sha = acts[-1]["commit"]
    return (sha if re.fullmatch(r"^[0-9a-f]{7,40}$", sha) else None), True


def is_ancestor(repo, older, newer):
    _, rc = run(["git", "cat-file", "-e", older + "^{commit}"], repo)
    if rc != 0:
        return False                      # not even present locally: cannot descend from it
    _, rc = run(["git", "merge-base", "--is-ancestor", older, newer], repo)
    return rc == 0


def is_root_commit(repo, sha):
    out, rc = run(["git", "rev-list", "--parents", "-n", "1", sha], repo)
    return rc == 0 and len(out.split()) == 1


def report_line(app, alias, head, local, branch):
    """A line the agent copies as is into the final report: the developer must know a push was forced."""
    print("REPORT: forced deploy of %s (%s): remote head %s replaced by %s; %s" % (
        app.get("name") or alias, alias, (head or "unknown")[:12], (local or "?")[:12],
        ("previous head kept in local branch %s" % branch) if branch else "previous head NOT backed up (forced without backup, as approved)"))


def force_token(app, alias, head, local):
    """RT D6: the token of the question "force-push, or deploy elsewhere?" — the app, its deployed head and the local commit it would replace. A consistency
    check that the developer's yes is about THIS push (never consent by itself): it changes as soon as one of them does."""
    return hashlib.sha256(("deploy-force\0%s\0%s\0%s" % (app.get("app_id") or alias, head or "unknown", local or "?")).encode()).hexdigest()[:12]


def rerun_line(a, *extra):
    """The exact deploy.sh line to run again after the developer's yes: this run's own options (--branch, --same-commit-policy) echoed, then `extra`
    (a value is one of the checked forms: the alias, the branch and the token passed their patterns, the policy is one of four words)."""
    parts = ["bash <skill>/scripts/deploy.sh --alias %s" % a.alias]
    if a.branch:
        parts.append("--branch %s" % a.branch)
    if a.same_commit_policy:
        parts.append("--same-commit-policy %s" % a.same_commit_policy)
    return " ".join(parts + list(extra))


def force_question(app, a, token, why):
    """The question of an app this skill cannot prove is its own, with the exact re-run line for a yes (RT D6: no scripted path existed after "force")."""
    print("QUESTION: %s is not provably one of this POC's apps (%s). Forcing would overwrite its history (the remote head is backed up first). Ask the developer: "
          "force-push, or deploy elsewhere? Only after the developer says yes: %s" % (app.get("name") or a.alias, why, rerun_line(a, "--force", token)))


def backup_question(a, how, head, approved):
    """No backup of the remote head is possible: the question, with the exact re-run line for a yes — the --force <token> of an approved run kept beside
    --force-without-backup (without it, the re-run would stop at the force question again)."""
    print("QUESTION: %s (deployed head: %s). Force anyway, losing it? Only after the developer says yes: %s" % (
        how, head or "unknown", rerun_line(a, *((["--force", a.force_token] if approved else []) + ["--force-without-backup"]))))


def force_allowed(a, app):
    prefix = plan_prefix(a.plan)
    has_prefix = bool(prefix and str(app.get("name", "")).startswith(prefix + "-"))
    listed = ledger_has(a.repo, app)
    return has_prefix and listed, prefix, has_prefix, listed


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--alias", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--branch")
    ap.add_argument("--same-commit-policy", choices=["error", "ignore", "restart", "rebuild"])
    ap.add_argument("--force-without-backup", action="store_true")
    ap.add_argument("--force", dest="force_token", metavar="TOKEN", help="the token of the force question, after the developer's yes")
    a = ap.parse_args(argv)
    if a.force_token is not None and not re.fullmatch(r"[0-9a-f]{12}", a.force_token):
        print("REFUSED: --force takes the 12-character token the question printed")
        return 10
    if not re.fullmatch(r"^[a-z0-9][a-z0-9-]{0,62}$", a.alias):
        print("REFUSED: invalid alias")
        return 10
    if a.branch and not re.fullmatch(r"^[A-Za-z0-9._/-]{1,100}$", a.branch):
        print("REFUSED: invalid branch name")
        return 10
    base = ["clever", "deploy", "--alias", a.alias]
    if a.branch:
        base += ["--branch", a.branch]
    if a.same_commit_policy:
        base += ["--same-commit-policy", a.same_commit_policy]
    tracked = tracked_state(a.repo)
    if tracked:
        print("REFUSED: %s tracked by git: the skill's local state never comes from the repository — %s" % (", ".join(tracked), TRACKED_STATE_ADVICE))
        return 10
    app = clever_app(a.repo, a.alias) or {}
    out, rc = run(["git", "rev-parse", (a.branch or "HEAD") + "^{commit}"], a.repo)
    local = out.strip() if rc == 0 else None
    head, known = remote_head(a.repo, a.alias)
    # decide BEFORE pushing: clever-tools hides a non-fast-forward refusal behind "repository is shallow"
    # when .git/shallow exists (src/commands/deploy/deploy.command.js:147-155, clever-tools 5.2.0)
    diverged = bool(local and head and head != local and not is_ancestor(a.repo, head, local))
    print("GIT   local %s · deployed head %s%s" % ((local or "?")[:12], (head or ("none (brand-new app)" if known else "unknown"))[:40],
                                                   " · NOT an ancestor of the local commit" if diverged else ""))
    forced = False
    branch = None
    token = force_token(app, a.alias, head, local)
    approved = a.force_token is not None and a.force_token == token
    if a.force_token is not None and not approved:
        print("REFUSED: the --force token does not match this app, its deployed head and the local commit (one of them changed since the question, or the "
              "token is not this question's): nothing pushed — run deploy.sh without --force and ask again")
        return 10
    if diverged:
        ok_, prefix, has_prefix, listed = force_allowed(a, app)
        print("NON-FAST-FORWARD (decided before the push): the app's remote holds %s, which the local commit does not descend from%s." % (
            head[:12], " (the local commit is a root commit: the shallow-clone snapshot)" if is_root_commit(a.repo, local) else ""))
        if not ok_ and not approved:
            force_question(app, a, token, "name prefix %r: %s; in the ledger: %s" % (prefix, "yes" if has_prefix else "no", "yes" if listed else "no"))
            return 13
        if not ok_:
            print("FORCE approved by the developer (--force token %s): %s is not one of this POC's apps" % (token, app.get("name") or a.alias))
        branch, sha, how = backup_remote(a.repo, app, a.alias, head)
        if branch:
            print("BACKUP remote head %s saved as local branch %s (%s)" % (sha, branch, how))
        elif not a.force_without_backup:
            backup_question(a, how, head, approved)
            return 13
        else:
            print("WARNING forcing WITHOUT backup, as approved: remote head %s is lost" % head)
        forced = True
    argv_ = base + (["--force"] if forced else [])
    print("RUN   " + " ".join(argv_) + (("   # forced: approved by the developer (--force)" if approved else "   # forced: app %s has the plan prefix and is in the ledger" % app.get("name")) if forced else ""))
    out, rc = run(argv_, a.repo, stream=True)
    if rc == 0:
        print("DEPLOYED %s%s%s" % (a.alias, " (forced)" if forced else "", (" — previous remote head kept in branch " + branch) if branch else ""))
        if forced:
            report_line(app, a.alias, head, local, branch)
        return 0
    shallow_mask = re.search(r"repository is shallow", out, re.I)
    if forced:
        print("FAIL  forced deploy exited %d%s" % (rc, " (clever-tools reports 'shallow' for ANY push error in a shallow clone: the real cause is hidden)" if shallow_mask else ""))
        return 11
    if NONFF_RX.search(out) or (shallow_mask and head):
        # the remote moved under us, or the cause is hidden by the shallow message: same rule, decided now
        print("PUSH REFUSED%s: applying the non-fast-forward rule." % (" (cause hidden by clever-tools' 'repository is shallow' message)" if shallow_mask else ""))
        ok_, prefix, has_prefix, listed = force_allowed(a, app)
        if not ok_ and not approved:
            force_question(app, a, token, "prefix %s, ledger %s" % ("yes" if has_prefix else "no", "yes" if listed else "no"))
            return 13
        branch, sha, how = backup_remote(a.repo, app, a.alias, head)
        if not branch and not a.force_without_backup:
            backup_question(a, how, head, approved)
            return 13
        if branch:
            print("BACKUP remote head %s saved as local branch %s (%s)" % (sha, branch, how))
        out, rc = run(base + ["--force"], a.repo, stream=True)
        if rc == 0:
            print("DEPLOYED %s (forced)%s" % (a.alias, (" — previous remote head kept in branch " + branch) if branch else ""))
            report_line(app, a.alias, head, local, branch)
            return 0
        print("FAIL  forced deploy exited %d" % rc)
        return 11
    if shallow_mask:
        print("FAIL  'repository is shallow' on an app with no deployed commit: apply the shallow workaround (preflight GATE line)")
        return 11
    print("FAIL  deploy exited %d: read the log above from the top (reference/troubleshooting.md)" % rc)
    return 11


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
