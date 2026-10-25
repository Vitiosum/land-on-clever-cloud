#!/usr/bin/env python3
"""migrate-files.py — copy files from a source (URL list, Vercel Blob listing, S3-style listing,
or a local folder) to a Cellar bucket, keeping the keys, with SHA-256 proof.

Usage:
  python3 -I migrate-files.py --source LIST --bucket NAME [--create-bucket] [--manifest FILE]
                           [--dry-run] [--resume MANIFEST] [--source-token-env NAME]
                           [--cellar-env-file FILE] [--key-strip PREFIX] [--region us-east-1]
  python3 -I migrate-files.py --source-dir DIR --bucket NAME ...
  python3 -I migrate-files.py --self-test

LIST (JSON or text), one of:
  - text: one URL per line (key = URL path without the leading "/")
  - JSON list: ["https://…", …] or [{"url": "…", "key": "…", "content_type": "…", "size": 123}, …]
  - Vercel Blob listing (the shape of @vercel/blob list()): {"blobs": [{"url", "pathname",
    "contentType", "size"}, …]} — key = pathname
  - {"objects": [...]} with the same item shape as the JSON list
--source-dir DIR: every regular file under DIR, key = path relative to DIR; symbolic links and
anything outside DIR are skipped (never followed).
--source-token-host HOST: the only host(s) that receive the source token (default: the host of the
first URL of the listing); the token is never sent after a redirect.

For each object:
  1. source: GET (read-only), SHA-256 and size computed while reading; an optional bearer token
     is read from the environment variable named by --source-token-env (never printed);
  2. Cellar: PUT signed by hand (AWS SigV4, region us-east-1, payload signed by its SHA-256),
     then a GET re-read and its SHA-256;
  3. PASS only if both SHA-256 and sizes are equal. The manifest (JSON) is the proof.
Content types are kept from the source and REPORTED: text/html, image/svg+xml and other
non-image types must be served by the app with nosniff + CSP sandbox and as attachments.

Cellar credentials: CELLAR_ADDON_HOST (bare host; "http://127.0.0.1:port" allowed for tests),
CELLAR_ADDON_KEY_ID, CELLAR_ADDON_KEY_SECRET from the environment, or from --cellar-env-file
(KEY=VALUE lines, file mode 600 required — written by `env-safe.sh env-file --addon <addon_id>
--only CELLAR_ADDON_ --out FILE`, never copied by hand from `clever addon env`). Provisioning the add-on creates NO bucket: bucket
names are GLOBAL across Cellar — use a prefix you own (e.g. <app>-poc-files); --create-bucket
creates it (private). HEAD 403 = the name belongs to another account.

--dry-run: no download, no upload: the plan, the bucket name check, the bucket state (HEAD).
--resume MANIFEST: skip the objects already PASS. Secrets (keys, tokens, signatures, signed
URLs, query strings) are never printed nor written. Python 3 stdlib only.
Exit: 0 every object PASS, 1 at least one FAIL, 2 usage or configuration error.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from cellar import (EMPTY_SHA256, S3_BUCKET_RX, Cellar, Digest, connect, error_text, pump,  # noqa: E402
                    read_env_file, s3_path, sign)

SAFE_INLINE = ("image/png", "image/jpeg", "image/webp", "image/gif")


# ------------------------------------------------------------------------ source
def key_from_url(url, strip=""):
    p = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lstrip("/")
    if strip and p.startswith(strip):
        p = p[len(strip):].lstrip("/")
    return p


def load_list(path, strip=""):
    raw = open(path, encoding="utf-8").read()
    try:
        d = json.loads(raw)
    except ValueError:
        d = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if isinstance(d, dict):
        d = d.get("blobs") or d.get("objects") or d.get("items") or []
    out = []
    for it in d:
        if isinstance(it, str):
            it = {"url": it}
        url = it.get("url") or it.get("downloadUrl")
        key = it.get("key") or it.get("pathname") or (key_from_url(url, strip) if url else None)
        out.append({"url": url, "key": key, "content_type": it.get("content_type") or it.get("contentType"),
                    "size": it.get("size")})
    return out


def load_dir(root):
    """Every regular file under root. Symbolic links are NEVER followed (a committed link could point at
    ~/.ssh/id_ed25519): links, and anything whose real path leaves root, are skipped and reported."""
    root = os.path.realpath(root)
    out, skipped = [], []
    for dp, dirs, files in os.walk(root, followlinks=False):
        for d in list(dirs):
            if os.path.islink(os.path.join(dp, d)):
                skipped.append(os.path.relpath(os.path.join(dp, d), root))
                dirs.remove(d)
        for f in sorted(files):
            full = os.path.join(dp, f)
            real = os.path.realpath(full)
            if os.path.islink(full) or not real.startswith(root + os.sep) or not os.path.isfile(real):
                skipped.append(os.path.relpath(full, root))
                continue
            out.append({"path": full, "key": os.path.relpath(full, root).replace(os.sep, "/"),
                        "content_type": None, "size": os.path.getsize(full)})
    for x in skipped:
        print("skipped (symbolic link or outside the folder, never followed): %s" % re.sub(r"[\x00-\x1f\x7f]", "?", x))
    return sorted(out, key=lambda x: x["key"])


def fetch(obj, token, sink, token_hosts=()):
    """Write the source object into sink; return (content_type, error). The bearer token goes only to
    the hosts of token_hosts (never to another host of the listing, never after a redirect)."""
    if obj.get("path"):
        with open(obj["path"], "rb") as f:
            pump(f, sink)
        return None, None
    url = obj["url"]
    for _ in range(5):   # follow up to 5 redirects
        u = urllib.parse.urlsplit(url)
        c, _host = connect("%s://%s" % (u.scheme, u.netloc))
        target = (u.path or "/") + (("?" + u.query) if u.query else "")
        h = {"User-Agent": "land-on-clever-cloud/migrate-files"}
        if token and u.hostname in token_hosts:
            h["Authorization"] = "Bearer " + token
        c.request("GET", target, headers=h)
        r = c.getresponse()
        if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
            url = urllib.parse.urljoin(url, r.getheader("Location"))
            r.read()
            c.close()
            token = None          # never send the token to another host after a redirect
            continue
        if r.status != 200:
            err = "HTTP %d %s" % (r.status, error_text(r.read()))
            c.close()
            return None, err.strip()
        ct = r.getheader("Content-Type")
        pump(r, sink)
        c.close()
        return ct, None
    return None, "too many redirects"


# -------------------------------------------------------------------- self-test
def self_test():
    """AWS SigV4 published example (GET Object, examplebucket, 2013-05-24)."""
    e = sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"}, EMPTY_SHA256,
             "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
             now=datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc))
    ok = e["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    print("SigV4 AWS test vector: %s" % ("PASS" if ok else "FAIL"))
    ok2 = s3_path("b", "a b/é.png") == "/b/a%20b/%C3%A9.png"
    print("S3 path encoding: %s" % ("PASS" if ok2 else "FAIL"))
    return 0 if ok and ok2 else 1


# -------------------------------------------------------------------------- main

def record_bucket(bucket, host):
    """#27: a bucket this script created goes in the skill's ledger (teardown-plan lists it)."""
    led_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.sh")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    if top and os.path.isfile(os.path.join(top, ".clever-landing", "ledger.tsv")):
        r = subprocess.run(["bash", led_sh, "add", "bucket", bucket, bucket, "cellar=%s; created by %s" % (host, os.path.basename(sys.argv[0]))],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print("bucket %s recorded in the ledger%s" % (bucket, "" if r.returncode == 0 else " — FAILED: " + r.stderr.decode()[:120]))
    else:
        print("bucket %s created: record it for the teardown (from the app repository): bash %s add bucket %s %s" % (bucket, led_sh, bucket, bucket))

def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="URL list / Vercel Blob listing / objects JSON")
    ap.add_argument("--source-dir", help="local folder to upload (key = relative path)")
    ap.add_argument("--source-token-env", help="NAME of the variable holding a source bearer token")
    ap.add_argument("--source-token-host", action="append", default=[],
                    help="host that may receive the source token (repeatable; default: the host of the first URL only)")
    ap.add_argument("--key-strip", default="", help="prefix removed from keys derived from URL paths")
    ap.add_argument("--bucket", help="Cellar bucket (global name: prefix it with something you own)")
    ap.add_argument("--create-bucket", action="store_true", help="create the bucket if absent (private)")
    ap.add_argument("--cellar-env-file", help="KEY=VALUE file (mode 600) with the CELLAR_ADDON_* variables")
    ap.add_argument("--region", default="us-east-1", help="SigV4 region (Cellar: %(default)s)")
    ap.add_argument("--manifest", default=os.path.join(".clever-landing", "manifest-files.json"),
                    help="proof file (default: .clever-landing/manifest-files.json, git-excluded by ledger.sh init)")
    ap.add_argument("--resume", metavar="MANIFEST", help="skip the objects already PASS in this manifest")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-size", type=int, default=5 * 1024 ** 3, help="single PUT limit (no multipart)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if bool(a.source) == bool(a.source_dir) or not a.bucket:
        ap.print_usage()
        print("error: exactly one of --source / --source-dir, and --bucket, are required")
        return 2
    if not S3_BUCKET_RX.match(a.bucket):
        print("error: invalid bucket name %r (3-63 chars: lowercase letters, digits, '-', '.')" % a.bucket)
        return 2
    objs = load_dir(a.source_dir) if a.source_dir else load_list(a.source, a.key_strip)
    bad = [o for o in objs if not o.get("key") or (not o.get("path") and not o.get("url"))
           or re.search(r"[\x00-\x1f\x7f]", o.get("key") or "") or ".." in (o.get("key") or "").split("/")]
    if bad:
        print("error: %d object(s) without url/key or with an unsafe key (control characters, '..')" % len(bad))
        return 2
    done = set()
    previous = []
    if a.resume and os.path.exists(a.resume):
        previous = [o for o in json.load(open(a.resume, encoding="utf-8")).get("objects", []) if o.get("status") == "PASS"]
        done = {o["key"] for o in previous}
    todo = [o for o in objs if o["key"] not in done]

    env = dict(os.environ)
    if a.cellar_env_file:
        env.update(read_env_file(a.cellar_env_file))
    missing = [v for v in ("CELLAR_ADDON_HOST", "CELLAR_ADDON_KEY_ID", "CELLAR_ADDON_KEY_SECRET") if not env.get(v)]
    token = ""
    if a.source_token_env:
        token = os.environ.get(a.source_token_env, "")
        if not token:
            missing.append(a.source_token_env)
    token_hosts = set(a.source_token_host)
    if token and not token_hosts:
        first = next((urllib.parse.urlsplit(o["url"]).hostname for o in objs if o.get("url")), None)
        token_hosts = {first} if first else set()
    if token:
        print("source token sent only to: %s" % (", ".join(sorted(h for h in token_hosts if h)) or "no host"))
    vol = sum(int(o.get("size") or 0) for o in todo)
    print("objects: %d listed, %d already PASS, %d to copy (~%d bytes declared) -> bucket %s" % (
        len(objs), len(done), len(todo), vol, a.bucket))
    if missing:
        print("missing variables (names only): " + ", ".join(missing))
    cellar = None
    if not any(m.startswith("CELLAR_") for m in missing):
        cellar = Cellar(env["CELLAR_ADDON_HOST"], env["CELLAR_ADDON_KEY_ID"], env["CELLAR_ADDON_KEY_SECRET"], a.region)
    if a.dry_run:
        if cellar:
            try:
                print("bucket %s: %s" % (a.bucket, cellar.bucket_state(a.bucket)))
            except OSError as exc:
                print("bucket %s: unreachable (%s)" % (a.bucket, type(exc).__name__))
        for o in todo[:10]:
            print("  would copy: %s" % o["key"])
        if len(todo) > 10:
            print("  … and %d more" % (len(todo) - 10))
        print("DRY RUN: nothing downloaded, nothing uploaded.")
        return 2 if missing else 0
    if missing:
        return 2
    state = cellar.bucket_state(a.bucket)
    if state == "absent" and a.create_bucket:
        ok, err = cellar.create_bucket(a.bucket)
        state = "created" if ok else "creation refused: " + err
        if ok:
            record_bucket(a.bucket, urllib.parse.urlsplit(cellar.base).netloc)
    print("bucket %s: %s" % (a.bucket, state))
    if state not in ("present", "created"):
        print("stop: bucket unavailable" + ("" if a.create_bucket else " (--create-bucket?)"))
        return 2

    results = list(previous)
    started = time.time()

    def write_manifest():
        ok = sum(1 for r in results if r["status"] == "PASS")
        doc = {"generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "tool": "migrate-files.py", "target": "%s/%s" % (urllib.parse.urlsplit(cellar.base).netloc, a.bucket),
               "summary": {"objects": len(results), "pass": ok, "fail": len(results) - ok,
                           "bytes": sum(r.get("size") or 0 for r in results),
                           "not_safe_inline": sorted({r["key"] for r in results if r.get("content_type") and r["content_type"].split(";")[0] not in SAFE_INLINE}),
                           "overwritten": sorted(r["key"] for r in results if r.get("overwrote")),
                           "seconds": round(time.time() - started, 1)},
               "objects": results}
        if os.path.dirname(a.manifest):
            os.makedirs(os.path.dirname(a.manifest), exist_ok=True)
        tmp = a.manifest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
        os.replace(tmp, a.manifest)
        return doc

    with tempfile.TemporaryDirectory(prefix="migrate-files-") as tmpd:
        for o in todo:
            r = {"key": o["key"], "source": (urllib.parse.urlsplit(o["url"]).netloc if o.get("url") else "local folder"),
                 "content_type": o.get("content_type"), "size": None, "sha256_source": None,
                 "sha256_cellar": None, "status": "FAIL", "error": None}
            results.append(r)
            try:
                fp = os.path.join(tmpd, "obj")
                with open(fp, "wb") as f:
                    d = Digest(f)
                    ct, err = fetch(o, token, d, token_hosts)
                if err:
                    r["error"] = "source: " + err
                    continue
                r.update(size=d.n, sha256_source=d.sha.hexdigest(), content_type=r["content_type"] or ct or "application/octet-stream")
                if o.get("size") is not None and int(o["size"]) != d.n:
                    r["error"] = "source: %d bytes read, %s declared" % (d.n, o["size"])
                    continue
                if d.n > a.max_size:
                    r["error"] = "%d bytes > --max-size (multipart not supported)" % d.n
                    continue
                path = s3_path(a.bucket, o["key"])
                hs, hh, _ = cellar.call("HEAD", path)
                if hs == 200:                # #19: never overwrite in silence
                    r["overwrote"] = True
                    same = {k.lower(): v for k, v in hh.items()}.get("x-amz-meta-sha256") == r["sha256_source"]
                    print("overwrites an existing object: %s%s" % (o["key"], " (same SHA-256)" if same else ""))
                with open(fp, "rb") as f:
                    st, _, body = cellar.call("PUT", path, body=f, size=d.n, payload_sha=r["sha256_source"],
                                              headers={"content-type": r["content_type"], "x-amz-meta-sha256": r["sha256_source"]})
                if st != 200:
                    r["error"] = ("Cellar PUT: HTTP %d %s" % (st, error_text(body))).strip()
                    continue
                back = Digest()
                st, _, n = cellar.call("GET", path, sink=back)
                if st != 200:
                    r["error"] = "Cellar GET (re-read): HTTP %d" % st
                    continue
                r["sha256_cellar"] = back.sha.hexdigest()
                if back.n != d.n or r["sha256_cellar"] != r["sha256_source"]:
                    r["error"] = "SHA-256 or size differs after re-read"
                    continue
                r["status"] = "PASS"
            except OSError as exc:
                r["error"] = "network: %s" % type(exc).__name__
            finally:
                print("%-4s %s%s" % (r["status"], r["key"], ("  (" + r["error"] + ")") if r["error"] else ""))
                write_manifest()
    doc = write_manifest()
    s = doc["summary"]
    print("SUMMARY: %d objects, %d PASS, %d FAIL, %d bytes — manifest %s" % (s["objects"], s["pass"], s["fail"], s["bytes"], a.manifest))
    if s["not_safe_inline"]:
        print("NOTE: %d object(s) are not safe-inline images (e.g. %s): the delivery route must send them with "
              "X-Content-Type-Options: nosniff, Content-Security-Policy: default-src 'none'; sandbox, as attachments" % (
                  len(s["not_safe_inline"]), s["not_safe_inline"][0]))
    print("VERDICT: %s" % ("PASS" if s["fail"] == 0 else "FAIL"))
    return 0 if s["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
