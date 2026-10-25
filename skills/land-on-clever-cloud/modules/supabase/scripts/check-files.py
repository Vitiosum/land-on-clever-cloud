#!/usr/bin/env python3
"""
Acceptance check of FILES on the target: bytes, rights, security headers.

    ./check-files.py check-files.json [--json check-files-target.json] [--sample N]

Binary counterpart of check-http.py (which only sends JSON). Same identity
file (token_cmd / token_env / headers, templates {token}, {token:name},
{id:name}); example: check-files.example.json.

Checks, all on the TARGET (on the source: HTTP reads only,
reference/storage.md §8):
  1. migrated files: each PASS object of the migrate-storage.py manifest
     is re-read THROUGH THE APPLICATION, under each read identity and under
     the owner's; SHA-256 = the source one (3rd step of the proof);
  2. serving headers: X-Content-Type-Options: nosniff, CSP
     default-src 'none' + sandbox; inline only for png/jpeg/webp/gif,
     otherwise attachment + application/octet-stream; escaped file name
     (no bare quote or line break; filename*=UTF-8''... preferred);
  3. upload of a generated PNG, re-read byte for byte;
  4. refusal on upload: HTML, SVG, XML, HTML disguised as .png -> 415; an HTML
     declared image/png is refused, or served under nosniff + sandbox; a text
     is refused, or served as an opaque attachment; a trap name (quote,
     CRLF) does not break Content-Disposition;
  5. rights: "refused" cases of the configuration (someone else's file, write
     on behalf of someone else); after a refused write on a migrated object, the
     owner re-reads it: SHA-256 unchanged; path traversal; missing
     object -> 404;
  5b. UPDATE policy ("replacement" section, if the app can replace a
     file): another user replaces the PNG of step 3 -> refused AND bytes
     intact; the owner replaces it -> accepted AND new bytes
     re-read. A 409 on an upload by another user proves the (bucket_id, name)
     uniqueness, not the UPDATE policy: without a replacement route, test it in SQL
     (reference/storage.md §8);
  6. private Cellar bucket: direct anonymous GET on CELLAR_ADDON_HOST -> 403;
  7. purge of the test objects through the "purge" route if it is declared
     (otherwise their list is printed at the end, to purge by hand).

Relative paths (manifest, token_cmd): resolved from the folder of the
configuration file, not from the current directory.

Uploads create test objects: their list is printed at the end, to purge
(without a DELETE policy, a purge under an identity deletes 0 rows WITHOUT error:
storage.md §9). No token is printed or written. Python 3 stdlib.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import secrets
import shlex
import struct
import subprocess
import sys
import urllib.parse
import uuid
import zlib

P = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
P.add_argument("config")
P.add_argument("--json", dest="json_out", help="write the checks to this file")
P.add_argument("--sample", type=int, default=0, help="re-read only N objects of the manifest (0 = all)")
P.add_argument("--no-upload", action="store_true", help="no upload: re-read, headers and read refusals only")
A = P.parse_args()

CONF = json.load(open(A.config, encoding="utf-8"))
ROOT = os.path.dirname(os.path.abspath(A.config))   # base of the relative paths
BASE = urllib.parse.urlsplit(CONF["url"].rstrip("/"))
IDS = CONF.get("identities", {})
INLINE = {"image/png", "image/jpeg", "image/webp", "image/gif"}

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

_tokens = {}
res = {"checks": [], "test_objects": []}
for _i in IDS.values():                 # every token_cmd checked BEFORE any request
    if isinstance(_i, dict) and _i.get("token_cmd"):
        token_argv(_i["token_cmd"])


def token(name):
    if name not in _tokens:
        i = IDS.get(name, {})
        if i.get("token_env"):
            _tokens[name] = os.environ[i["token_env"]]
        elif i.get("token_cmd"):
            _tokens[name] = subprocess.run(token_argv(i["token_cmd"]), check=True, cwd=ROOT,
                                           capture_output=True, text=True).stdout.strip()
        else:
            _tokens[name] = ""
    return _tokens[name]


def template(text, ident=None, values=None, url=False):
    """{token}, {token:name}, {id:name}, {random}; {bucket} and {name} encoded if url=True."""
    text = re.sub(r"\{token:([\w-]+)\}", lambda m: token(m.group(1)), text)
    text = re.sub(r"\{id:([\w-]+)\}", lambda m: IDS.get(m.group(1), {}).get("id", ""), text)
    for k, v in (values or {}).items():
        text = text.replace("{" + k + "}", urllib.parse.quote(v, safe="/") if url else v)
    return text.replace("{token}", token(ident) if ident and ident != "anon" else "")


def call(method, path, ident=None, body=None, headers=None):
    cls = http.client.HTTPSConnection if BASE.scheme == "https" else http.client.HTTPConnection
    c = cls(BASE.netloc, timeout=60)
    h = dict(IDS.get(ident, {}).get("headers", {})) if ident and ident != "anon" else {}
    h = {k: template(v, ident) for k, v in h.items()}
    h.update(headers or {})
    c.request(method, BASE.path + path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheaders(), data


def header(h, name):
    return next((v for k, v in h if k.lower() == name.lower()), None)


def ctrl(name, ok, got):
    res["checks"].append({"check": name, "got": got, "ok": bool(ok)})
    print(f"  {'OK ' if ok else 'KO '} {name:72} -> {got}")


def short(n, m=36):
    return n if len(n) <= m else "…" + n[-(m - 1):]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def png():
    """Valid 1x1 PNG, unique (random text in a tEXt chunk)."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"tEXt", b"check\x00" + secrets.token_hex(8).encode())
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")) + chunk(b"IEND", b""))


def check_headers(label, h, expected_type=None):
    """Serving headers of a file: nosniff, CSP, disposition, escaped name."""
    xcto = (header(h, "X-Content-Type-Options") or "").lower()
    csp = (header(h, "Content-Security-Policy") or "").lower()
    typ = (header(h, "Content-Type") or "").split(";")[0].strip().lower()
    disp = header(h, "Content-Disposition") or ""
    ok_csp = "default-src 'none'" in csp and "sandbox" in csp
    ctrl(f"{label}: nosniff + CSP default-src 'none'; sandbox", xcto == "nosniff" and ok_csp,
         f"nosniff={xcto == 'nosniff'} csp={'ok' if ok_csp else csp or 'missing'}")
    inline = disp.lower().startswith("inline") or not disp
    if typ in INLINE:
        ok = expected_type is None or typ == expected_type
        ctrl(f"{label}: raster image {typ}", ok, f"{typ}; {disp or 'no disposition'}")
    else:
        ctrl(f"{label}: non-image -> attachment + application/octet-stream",
             not inline and typ == "application/octet-stream", f"{typ}; {disp.split(';')[0] or 'no disposition'}")
    m = re.search(r'filename="((?:[^"\\]|\\.)*)"', disp)
    clean = ("\r" not in disp and "\n" not in disp
             and (m is not None or 'filename="' not in disp)
             and (m is None or disp.count('"') == 2 + m.group(1).count('\\"')))
    if "filename" in disp:
        ctrl(f"{label}: escaped file name (RFC 6266)", clean,
             "filename*" if "filename*=" in disp else "filename only")


# --------------------------------------------------------------------------- 1-2
read = CONF["read"]
man = (json.load(open(os.path.join(ROOT, template(CONF["manifest"])), encoding="utf-8"))
       if CONF.get("manifest") else {"objects": []})
objects = [o for o in man.get("objects", []) if o.get("status") == "PASS"]
if A.sample:
    objects = objects[:A.sample]
by_id = {v.get("id"): k for k, v in IDS.items() if v.get("id")}

print(f"1-2. Migrated files, re-read through the application ({len(objects)} objects)")
for o in objects:
    li = read.get("identities", ["anon"])
    readers = list(li.get(o["bucket"], li.get("*", [])) if isinstance(li, dict) else li)
    if by_id.get(o.get("owner")) and by_id[o["owner"]] not in readers:
        readers.append(by_id[o["owner"]])
    path = template(read["path"], values={"bucket": o["bucket"], "name": o["name"]}, url=True)
    h0 = None
    for ident in readers:
        st, h, body = call("GET", path, ident)
        ctrl(f"{o['bucket']}/{short(o['name'])} ({ident}) SHA-256 = source",
             st == 200 and sha(body) == o["sha256_source"], f"{st} {sha(body)[:16]}")
        h0 = h0 or (h if st == 200 else None)
    if h0:
        check_headers(short(o["name"]), h0)

# --------------------------------------------------------------------------- 3-4
upload = CONF.get("upload")


def send(ident, bucket, name, content, mime, route=None):
    """Upload through the `upload` route (or `route`, e.g. the replacement section)."""
    route = route or upload
    values = {"bucket": bucket, "name": name}
    path = template(route["path"], ident, values, url=True)
    if route.get("format", "multipart") == "multipart":
        b = "----check" + uuid.uuid4().hex
        body = b""
        for k, v in route.get("fields", {}).items():
            body += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                     f"{template(v, ident, values)}\r\n").encode()
        body += (f'--{b}\r\nContent-Disposition: form-data; name="{route.get("file_field", "file")}"; '
                 f'filename="file"\r\nContent-Type: {mime}\r\n\r\n').encode() + content + f"\r\n--{b}--\r\n".encode()
        e = {"Content-Type": f"multipart/form-data; boundary={b}"}
    else:                                   # raw body, type in Content-Type (PUT /files/<name>)
        body, e = content, {"Content-Type": mime}
    st, _, _ = call(route.get("method", "POST"), path, ident, body, e)
    if st in route.get("ok_statuses", [200, 201]) and route is upload:
        res["test_objects"].append(f"{bucket}/{name}")
    return st


def reread(ident, bucket, name):
    return call("GET", template(read["path"], values={"bucket": bucket, "name": name}, url=True), ident)


png_sent = None
if upload and not A.no_upload:
    who, bucket = upload["identity"], upload["bucket"]
    ok_upload = upload.get("ok_statuses", [200, 201])
    refused = upload.get("refused_statuses", [415])

    def name(ext):
        return template(upload.get("name", "{id:" + who + "}-check-{random}.{ext}"), who,
                        {"random": secrets.token_hex(4), "ext": ext})

    print("3. Upload of a file, re-read byte for byte")
    content, n = png(), name("png")
    st = send(who, bucket, n, content, "image/png")
    ctrl(f"{who} uploads a PNG", st in ok_upload, st)
    png_sent = (n, content) if st in ok_upload else None
    st, h, body = reread(who, bucket, n)
    ctrl("  re-read through the application, identical SHA-256", st == 200 and sha(body) == sha(content), f"{st} {sha(body)[:16]}")
    if st == 200:
        check_headers("  uploaded PNG", h, "image/png")

    print("4. Active types refused on upload (files served from the app origin)")
    html = b"<!doctype html><script>alert(document.domain)</script>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    xml = b'<?xml version="1.0"?><x:script xmlns:x="http://www.w3.org/1999/xhtml">alert(1)</x:script>'
    for label, c, mime, ext in (("HTML", html, "text/html", "html"), ("SVG", svg, "image/svg+xml", "svg"),
                                ("XML", xml, "application/xml", "xml"),
                                ("HTML disguised as .png", html, "text/html", "png")):
        st = send(who, bucket, name(ext), c, mime)
        ctrl(f"{who} uploads {label} ({mime}) -> refused", st in refused, st)
    n = name("png")
    st = send(who, bucket, n, html, "image/png")
    if st in ok_upload:
        st2, h, _ = reread(who, bucket, n)
        xcto = (header(h, "X-Content-Type-Options") or "").lower()
        csp = (header(h, "Content-Security-Policy") or "").lower()
        ctrl("HTML declared image/png: accepted, so served under nosniff + sandbox",
             st2 == 200 and xcto == "nosniff" and "sandbox" in csp, f"{st2} nosniff={xcto == 'nosniff'}")
    else:
        ctrl("HTML declared image/png: refused", st in refused + [400], st)
    n = name("txt")
    st = send(who, bucket, n, b"acceptance check text", "text/plain")
    if st in ok_upload:
        st2, h, _ = reread(who, bucket, n)
        check_headers("text accepted", h)
    else:
        ctrl("text: refused on upload", st in refused + [400], st)
    n = template("{id:" + who + "}-check-" + secrets.token_hex(3) + '-a"b;x=1\r\nX-Injected: yes.txt', who)
    st = send(who, bucket, n, b"trap name", "text/plain")
    if st in ok_upload:
        st2, h, _ = reread(who, bucket, n)
        ctrl("trap name: no header injected", header(h, "X-Injected") is None, st2)
        check_headers("trap name", h)
    else:
        ctrl("trap name: refused on upload", st in [400, 403, 415, 422], st)

# --------------------------------------------------------------------------- 5
print("5. Rights")
for c in CONF.get("refused", []):
    ident = c.get("identity", "anon")
    if c.get("upload") and (not upload or A.no_upload):
        continue
    if c.get("upload"):                      # refused write (someone else's file, someone else's name)
        st = send(ident, c["bucket"], template(c["object"], ident), png(), "image/png")
    else:
        st, _, _ = reread(ident, c["bucket"], template(c["object"], ident))
    ctrl(c["name"], st in (c.get("status") or [403, 404]), st)
    if c.get("upload"):
        if st == 409:
            print("      409 = (bucket_id, name) uniqueness: does not prove the UPDATE policy (see 5b)")
        target = next((o for o in objects if o["bucket"] == c["bucket"] and o["name"] == template(c["object"], ident)), None)
        owner = by_id.get((target or {}).get("owner"))
        if target and owner:
            st2, _, body = reread(owner, target["bucket"], target["name"])
            ctrl(f"  {owner} re-reads {short(target['name'])}: original bytes intact",
                 st2 == 200 and sha(body) == target["sha256_source"], f"{st2} {sha(body)[:16]}")

replacement = CONF.get("replacement")
if replacement and upload and not A.no_upload:
    print("5b. UPDATE policy: replacement of an existing file")
    if not png_sent:
        ctrl("replacement: PNG of step 3 available", False, "upload of step 3 failed")
    else:
        n, original = png_sent
        who, others = upload["identity"], replacement["others"]
        st = send(others, upload["bucket"], n, png(), "image/png", replacement)
        ctrl(f"{others} replaces the file of {who} -> refused", st in replacement.get("refused_statuses", [403, 404]), st)
        st2, _, body = reread(who, upload["bucket"], n)
        ctrl(f"  {who} re-reads: bytes intact", st2 == 200 and sha(body) == sha(original), f"{st2} {sha(body)[:16]}")
        new = png()
        st = send(who, upload["bucket"], n, new, "image/png", replacement)
        ctrl(f"{who} replaces their file -> accepted", st in replacement.get("ok_statuses", [200, 201]), st)
        st2, _, body = reread(who, upload["bucket"], n)
        ctrl(f"  {who} re-reads: new bytes", st2 == 200 and sha(body) == sha(new), f"{st2} {sha(body)[:16]}")
b_test = (upload or {}).get("bucket") or (objects[0]["bucket"] if objects else None)
if b_test:
    print("5c. Paths")
    # Paths sent AS IS (no re-encoding): raw "../", encoded slash,
    # encoded backslash, encoded dots. Refusal expected AND no system file
    # content in the response.
    for form in ("../../../../etc/passwd", "..%2F..%2F..%2F..%2Fetc%2Fpasswd",
                 "..%5c..%5c..%5c..%5cetc%5cpasswd", "%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd"):
        st, _, body = call("GET", template(read["path"], values={"bucket": b_test, "name": form}))
        ctrl(f"traversal {form[:22]}… -> 400/403/404", st in (400, 403, 404) and b"root:" not in body, st)
    st, _, _ = reread("anon", b_test, "missing-" + secrets.token_hex(6) + ".png")
    ctrl("missing object -> 404", st in CONF.get("status_missing", [404]), st)

# --------------------------------------------------------------------------- 6
host = os.environ.get("CELLAR_ADDON_HOST", "")
if host and objects:
    print("6. Private Cellar bucket (direct access, no signature)")
    base = host if re.match(r"^https?://", host) else "https://" + host
    u = urllib.parse.urlsplit(base)
    for b in sorted({o["cellar_bucket"] for o in objects}):
        o = next(x for x in objects if x["cellar_bucket"] == b)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        cx = cls(u.netloc, timeout=30)
        cx.request("GET", "/" + urllib.parse.quote(b) + "/" + urllib.parse.quote(o["name"], safe="/~"))
        st = cx.getresponse().status
        cx.close()
        ctrl(f"anonymous GET {b}/{short(o['name'])} -> 403", st == 403, st)

# --------------------------------------------------------------------------- 7
# Purge of the test objects ("purge" route declared): a test HTML
# accepted by mistake does not stay online. Without a route, the list is printed at the end.
purge = CONF.get("purge")
if purge and upload and res["test_objects"]:
    print("7. Purge of the test objects")
    left = []
    for o in res["test_objects"]:
        b, n = o.split("/", 1)
        st, _, _ = call(purge.get("method", "DELETE"),
                        template(purge["path"], values={"bucket": b, "name": n}, url=True), upload["identity"])
        if st not in purge.get("ok_statuses", [200, 204]):
            left.append(o)
    ctrl("test objects purged", not left, f"{len(res['test_objects']) - len(left)}/{len(res['test_objects'])}")
    res["test_objects"] = left

ko = [c for c in res["checks"] if not c["ok"]]
print(f"\n{len(res['checks']) - len(ko)}/{len(res['checks'])} checks OK")
if res["test_objects"]:
    print("Test objects to purge: " + json.dumps(res["test_objects"], ensure_ascii=False))
if A.json_out:
    with open(A.json_out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
sys.exit(1 if ko else 0)
