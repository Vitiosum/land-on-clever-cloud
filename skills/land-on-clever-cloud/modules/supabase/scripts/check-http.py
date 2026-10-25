#!/usr/bin/env python3
"""
Generic HTTP acceptance check of the TARGET: resource x identity matrix, and
write isolation checks. Independent of PostgREST and GoTrue.

    ./check-http.py check-http.json [--json target.json] [--compare source.json]

It is the counterpart of check-rls.py when option 1 has removed PostgREST and
GoTrue: the target API is the application's own API, described in the
configuration file (see check-http.example.json).

Same three rules as check-rls.py:
  1. real sessions, obtained from the application itself (token forged with
     its secret by a command, or read from a variable) — never a password in
     the configuration;
  2. expected values as ISOLATION (0, >0, *) rather than absolute values;
  3. SERVER-SIDE count (total header) when the API provides one.

Writes (POST / PATCH / DELETE) run ONLY on the target: on the source, a
refused INSERT still consumes the sequence, and each login creates a session.
On the source side, the matrix comes from the read-only SQL simulation
(modules/supabase/GUIDE.md, phase 6), in the same format:
{"matrix": {resource: {identity: n}}}.
A source matrix with the reversed orientation ({identity: {resource: n}}) is
detected and transposed, with a warning, instead of producing a false
mismatch in every cell.

Relative paths (token_cmd): resolved from the folder of the configuration
file, not from the current directory.

No token is printed or written to the output.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

P = argparse.ArgumentParser(description="HTTP acceptance check of the target")
P.add_argument("config")
P.add_argument("--json", dest="json_out", help="write matrix + checks to this file")
P.add_argument("--compare", help="source matrix (same format) to compare with")
P.add_argument("--read-only", action="store_true", help="matrix only, no write checks")
A = P.parse_args()

CONF = json.load(open(A.config))
ROOT = os.path.dirname(os.path.abspath(A.config))   # base of relative paths

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """No redirect followed: urllib would copy apikey and Authorization into it,
    even to another host. A redirect comes back as an HTTP error."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None



def _tracked_by_git(path):
    d = os.path.dirname(os.path.abspath(path))
    return subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", "--", os.path.basename(path)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def token_argv(cmd):
    """token_cmd is TEXT FROM A FILE: it runs only as `node <…>/forge-token.mjs <id…>` (no shell, no
    other program), and never from a configuration tracked by git (the repo's word, not the developer's)."""
    if _tracked_by_git(A.config):
        sys.exit("token_cmd refused: %s is tracked by git — a command read from a committed file is never run. "
                 "Keep the configuration untracked (it holds no secret), or use token_env." % os.path.basename(A.config))
    argv = shlex.split(cmd)
    if (len(argv) < 2 or argv[0] != "node" or os.path.basename(argv[1]) != "forge-token.mjs"
            or not all(re.fullmatch(r"[A-Za-z0-9._:@/+=-]{1,200}", x) for x in argv[1:])):
        sys.exit("token_cmd refused: only `node <path>/forge-token.mjs <arguments>` is accepted (or token_env)")
    return argv

OPENER = urllib.request.build_opener(_NoRedirect)

BASE = CONF["url"].rstrip("/")
IDS = CONF.get("identities", {})
_tokens = {}
for _i in IDS.values():                 # every token_cmd checked BEFORE any request
    if isinstance(_i, dict) and _i.get("token_cmd"):
        token_argv(_i["token_cmd"])


def token(name):
    """Token of an identity: token_env, or output of token_cmd (forged with the app's secret)."""
    if name not in _tokens:
        i = IDS.get(name, {})
        if i.get("token_env"):
            _tokens[name] = os.environ[i["token_env"]]
        elif i.get("token_cmd"):
            # No shell: the command (and its arguments) is split by shlex.
            _tokens[name] = subprocess.run(token_argv(i["token_cmd"]), check=True, cwd=ROOT,
                                           capture_output=True, text=True).stdout.strip()
        else:
            _tokens[name] = ""
    return _tokens[name]


def template(text, ident):
    """{token} = token of the current identity; {token:name} = another one's; {id:name} = its id."""
    text = re.sub(r"\{token:([\w-]+)\}", lambda m: token(m.group(1)), text)
    text = re.sub(r"\{id:([\w-]+)\}", lambda m: IDS.get(m.group(1), {}).get("id", ""), text)
    return text.replace("{token}", token(ident) if ident else "")


def substitute(obj, ident):
    if isinstance(obj, str):
        return template(obj, ident)
    if isinstance(obj, dict):
        return {k: substitute(v, ident) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, ident) for v in obj]
    return obj


def call(method, path, ident=None, body=None, headers=None):
    req = urllib.request.Request(BASE + template(path, ident), method=method,
                                 data=json.dumps(substitute(body, ident)).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    h = dict(IDS.get(ident, {}).get("headers", {})) if ident else {}
    h.update(headers or {})
    for k, v in h.items():
        req.add_header(k, template(v, ident))
    try:
        with OPENER.open(req, timeout=30) as r:
            b = r.read()
            return r.status, r.headers, b
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers, e.read()
        except OSError:                 # error body cut off: the code is enough
            return e.code, e.headers, b""


def count(resource, ident, headers=None):
    """Number of visible rows, and others_rows if the resource declares an owner."""
    st, h, b = call("GET", resource["path"], ident, headers=headers)
    if st != 200:
        return (0 if st in resource.get("empty_statuses", [401, 403]) else f"HTTP {st}"), None
    try:
        body = json.loads(b) if b else None
    except json.JSONDecodeError:
        body = None
    mode = resource.get("count", "json")
    if mode.startswith("header:"):
        v = h.get(mode.split(":", 1)[1])
        n = int(v) if v and v.isdigit() else f"no {mode}"
    else:
        key = mode.split(":", 1)[1] if ":" in mode else None
        items = body.get(key) if (key and isinstance(body, dict)) else body
        n = len(items) if isinstance(items, list) else "body not a list"
    others = None
    field, uid = resource.get("owner"), IDS.get(ident or "", {}).get("id")
    if field and uid and isinstance(body, list):
        others = sum(1 for row in body if str(row.get(field)) != uid)
    return n, others


def matches(got, expected):
    if expected == ">0":
        return isinstance(got, int) and got > 0
    if expected == "*":
        return not isinstance(got, str)
    if isinstance(expected, list):
        return got in expected
    return got == expected


res = {"matrix": {}, "checks": []}


def check(name, got, expected):
    ok = matches(got, expected)
    res["checks"].append({"check": name, "expected": expected, "got": got, "ok": ok})
    print(f"  {'OK ' if ok else 'KO '} {name:60} -> {got}")


# 1. Read matrix
print("Matrix (server-side count)")
for r in CONF["matrix"]:
    for ident in ["anon"] + [i for i in IDS if i != "anon"]:
        n, others = count(r, None if ident == "anon" else ident)
        res["matrix"].setdefault(r["name"], {})[ident] = n
        if others is not None:
            res["matrix"].setdefault(r["name"] + "#others", {})[ident] = others
        print(f"  {r['name']:20} {ident:12} visible={n}" + (f" others_rows={others}" if others is not None else ""))

print("Expected")
for key, expected in sorted((CONF.get("expected") or {}).items()):
    resource, ident = key.split("@", 1)
    check(f"{resource}@{ident} = {expected}", res["matrix"].get(resource, {}).get(ident, "absent"), expected)

# 2. Checks (writes included): TARGET ONLY
if not A.read_only:
    print("Checks")
    index = {r["name"]: r for r in CONF["matrix"]}
    for c in CONF.get("checks", []):
        ident = c.get("identity")
        if "resource" in c:      # read under an identity, possibly tampered with
            n, _ = count(index[c["resource"]], ident, c.get("headers"))
            check(c["name"], n, c["count"])
        else:
            st, _, _ = call(c.get("method", "GET"), c["path"], ident, c.get("body"), c.get("headers"))
            check(c["name"], st, c["status"])

# 3. Comparison with the source
def orient(source, target, identities):
    """{resource: {identity: n}} expected; {identity: {resource: n}} detected and transposed."""
    if not isinstance(source, dict) or not all(isinstance(v, dict) for v in source.values()):
        sys.exit("--compare: unreadable matrix, expected format {\"matrix\": {resource: {identity: n}}}")
    ext = set(source)
    inn = {k for v in source.values() for k in v}
    resources = set(target)
    if ext & resources or not (ext <= identities and inn & resources):
        return source
    print("Warning: source matrix in {identity: {resource: n}} (reversed orientation): transposed.")
    t = {}
    for ident, rows in source.items():
        for r, n in rows.items():
            t.setdefault(r, {})[ident] = n
    return t


if A.compare:
    source = orient(json.load(open(A.compare)).get("matrix", {}), res["matrix"],
                    set(IDS) | {"anon"})
    unknown = sorted(set(source) - set(res["matrix"]))
    if unknown:
        print("Source resources missing from the target check (different name?): " + ", ".join(unknown))
    mismatches = [(r, i, source[r][i], res["matrix"].get(r, {}).get(i))
                  for r in source for i in source[r] if source[r][i] != res["matrix"].get(r, {}).get(i)]
    print("Source / target comparison: " + ("IDENTICAL MATRICES" if not mismatches else f"{len(mismatches)} mismatch(es)"))
    for r, i, s, c in mismatches:
        print(f"  {r}@{i}: source={s} target={c}")
    res["source_mismatches"] = [{"resource": r, "identity": i, "source": s, "target": c} for r, i, s, c in mismatches]

ko = [c for c in res["checks"] if not c["ok"]] + res.get("source_mismatches", [])
print(f"\n{len(res['checks']) - sum(1 for c in res['checks'] if not c['ok'])}/{len(res['checks'])} checks OK")
if A.json_out:
    with open(A.json_out, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
sys.exit(1 if ko else 0)
