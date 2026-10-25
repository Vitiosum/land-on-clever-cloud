#!/usr/bin/env python3
"""
Acceptance check of the access rules: resource x identity matrix.

    ./sessions.py check.config.json      # prerequisite
    ./check-rls.py check.config.json [--json output.json]

THIS IS THE DELIVERABLE THAT PROVES THE MIGRATION. Run it on the source, run
it again on the target: identical matrix = faithful migration. A cell that
changes points to the exact problem, without rereading a policy.

Three choices make it replayable months later, and portable from one
platform to another:

  1. It RENEWS expired tokens before querying. Tokens live for one hour:
     without this, the whole matrix fails with authentication errors and
     looks like a massive regression.
  2. Expected values are written as ISOLATION (0, >0), not absolute values.
     The corpus varies with whether volume was loaded or not; the access
     rules do not change. That is what lets you compare source and target
     without rewriting the test.
  3. It COUNTS SERVER-SIDE (HEAD + Prefer: count=exact). Counting the rows
     returned would show the pagination cap instead of the truth.

PREREQUISITE: PostgREST and GoTrue on BOTH sides. If the target removes them
(option 1, the recommended option), this script only runs on the source side;
on the target side, use check-http.py, and on the source side prefer the
read-only SQL simulation (modules/supabase/GUIDE.md, phase 6): opening
sessions modifies the source.

KEYS: an sb_publishable_ key goes in `apikey` only; the anon identity
therefore sends NO Bearer. User JWTs stay in Bearer.
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

_P = argparse.ArgumentParser(description="Resource x identity matrix (PostgREST)")
_P.add_argument("config", nargs="?", default="check.config.json")
_P.add_argument("--json", dest="json_out", help="write the matrix to this file")
_A = _P.parse_args()
CONF = pathlib.Path(_A.config)
SESSIONS = CONF.parent / ".sessions.local.json"
JSON_OUT = pathlib.Path(_A.json_out) if _A.json_out else None

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No redirect followed: urllib would copy apikey and Authorization into it,
    even to another host. A redirect comes back as an HTTP error."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def write_600(path, text):
    """File created directly as 600 (never 644 then chmod), atomic replace."""
    path = str(path)
    tmp = path + ".tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)



def claim(token, name):
    import base64
    try:
        c = token.split(".")[1]
        c += "=" * (-len(c) % 4)
        return json.loads(base64.urlsafe_b64decode(c)).get(name, "-")
    except Exception:                                          # noqa: BLE001
        return "-"


def refresh(base, anon, sessions):
    changed = False
    for email, s in sessions.items():
        exp = claim(s["access_token"], "exp")
        if isinstance(exp, int) and exp > time.time() + 60:
            continue
        if not s.get("refresh_token"):
            print(f"  {email}: token expired, no refresh_token — run sessions.py again")
            continue
        req = urllib.request.Request(f"{base}/auth/v1/token?grant_type=refresh_token",
                                     data=json.dumps({"refresh_token": s["refresh_token"]}).encode(),
                                     method="POST")
        req.add_header("apikey", anon)
        req.add_header("Content-Type", "application/json")
        try:
            with OPENER.open(req, timeout=30) as r:
                d = json.loads(r.read())
            s["access_token"] = d["access_token"]
            s["refresh_token"] = d.get("refresh_token", s["refresh_token"])
            changed = True
            print(f"  {email:32} token renewed (aal={claim(s['access_token'], 'aal')})")
        except urllib.error.HTTPError as e:
            print(f"  {email}: refresh refused ({e.code}) — run sessions.py again")
    if changed:
        write_600(SESSIONS, json.dumps(sessions, indent=1))
    return sessions


def count(base, anon, token, schema, resource):
    """EXACT number of visible rows, or 'ERR <code>'."""
    # HEAD: no body, no Range — so no 416 on an empty table.
    req = urllib.request.Request(f"{base}/rest/v1/{resource}?select=*", method="HEAD")
    req.add_header("apikey", anon)
    if token and not (token == anon and anon.startswith("sb_")):
        req.add_header("Authorization", f"Bearer {token}")
    if schema:
        req.add_header("Accept-Profile", schema)
    req.add_header("Prefer", "count=exact")
    try:
        with OPENER.open(req, timeout=60) as r:
            total = (r.headers.get("Content-Range") or "").split("/")[-1]
            return int(total) if total.isdigit() else 0
    except urllib.error.HTTPError as e:
        try:
            return f"ERR {json.loads(e.read() or b'{}').get('code', e.code)}"
        except json.JSONDecodeError:
            return f"HTTP {e.code}"
    except Exception as exc:                                   # noqa: BLE001
        return f"KO {type(exc).__name__}"


def matches(got, expected):
    if expected == ">0":
        return isinstance(got, int) and got > 0
    if expected == "*":
        return not isinstance(got, str)
    return got == expected


def main():
    if not CONF.exists():
        raise SystemExit(f"Missing: {CONF}. See check.config.example.json")
    conf = json.loads(CONF.read_text())
    base = conf["url"].rstrip("/")
    anon = conf["anon_key"]

    if not SESSIONS.exists():
        raise SystemExit(f"Missing: {SESSIONS.name} — run ./sessions.py {CONF.name} first")
    sessions = refresh(base, anon, json.loads(SESSIONS.read_text()))

    identities = [("anon", anon)] + [(s["role"], s["access_token"]) for s in sessions.values()]

    print("\nIdentities")
    for name, token in identities:
        if name == "anon":
            print(f"  {name:14} publishable key, no session")
        else:
            print(f"  {name:14} aal={claim(token, 'aal')}")

    resources = conf["resources"]
    width = max(len(r["name"]) for r in resources) + 2
    header = "".ljust(width) + "".join(n.ljust(14) for n, _ in identities)
    print("\n" + header + "\n" + "-" * len(header))

    results, matrix = {}, {}
    for r in resources:
        line = r["name"].ljust(width)
        matrix[r["name"]] = {}
        for name, token in identities:
            v = count(base, anon, token, r.get("schema"), r["name"])
            results[(r["name"], name)] = v
            matrix[r["name"]][name] = v
            line += str(v).ljust(14)
        print(line)

    print("\nMismatches against the stated rules")
    mismatches = []
    for key, expected in sorted((conf.get("expected") or {}).items()):
        resource, ident = key.split("@", 1)
        got = results.get((resource, ident), "absent")
        if not matches(got, expected):
            mismatches.append({"resource": resource, "identity": ident,
                               "expected": expected, "got": got})
            print(f"  {resource}@{ident:14} expected={expected}  got={got}")
    if not mismatches:
        print("  none")

    if JSON_OUT:
        JSON_OUT.write_text(json.dumps({"matrix": matrix, "mismatches": mismatches}, indent=1))
        print(f"\nMatrix written to {JSON_OUT}")
        print("Compare source and target:  diff <(jq -S .matrix source.json) <(jq -S .matrix target.json)")

    print("\nIdentical matrix after migration = faithful migration.")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
