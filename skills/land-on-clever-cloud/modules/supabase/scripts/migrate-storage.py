#!/usr/bin/env python3
"""
Migrate Supabase Storage files -> Cellar, with SHA-256 proof.

Separate from <skill>/scripts/migrate-files.py: Supabase Storage needs the object list from storage.objects
(database), the apikey/authenticated/public routes, the md5 eTag check, per-bucket mapping. Cellar client: shared <skill>/scripts/lib/cellar.py.

    ./migrate-storage.py objects.json --dry-run                   # plan only, nothing written
    ./migrate-storage.py objects.json --create-buckets --manifest storage-manifest.json
    ./migrate-storage.py --buckets avatars,documents --prefix acme- --create-buckets

objects.json: the list from the survey (`survey-supabase.sh --objects`, or
`--objects-query` passed to `execute_sql` of the MCP connector):
    [{"bucket": "avatars", "name": "<path>", "owner": "<uuid>",
      "mimetype": "image/png", "size": 179, "etag": "<md5>"}, ...]
The list comes from the DATABASE, never from the API: anonymous /object/list returns [].

For each object:
  1. source: download through the route that works, SHA-256 computed; size
             and md5 compared with storage.objects (eTag, except multipart uploads);
  2. Cellar: PUT signed with SigV4 (region us-east-1, payload signed by its
             SHA-256), then a RE-READ and the SHA-256 of what was re-read;
  3. the app: outside this script — scripts/check-files.py re-reads each
             file through the application and compares it with the manifest.
PASS = the source and Cellar digests are identical. The JSON manifest is
the proof to attach to the acceptance report.

Source, in order of preference:
  - Storage API (SUPABASE_URL) with the PUBLISHABLE key in `apikey`:
    /object/authenticated/<bucket>/<name> answers 200 if a SELECT policy
    allows it (without a session); /object/public/... for a public bucket. `--route
    auto` tries one then the other and records the one that worked.
  - otherwise the project's SECRET key (sb_secret_… or service_role): REQUIRED for the bytes of a
    PRIVATE bucket without a user session. The developer copies it from the dashboard
    (Settings > API keys) into a 600 file — `pbpaste > secret-key && chmod 600 secret-key`, then
    clear the clipboard — and passes --key-file FILE; it is only sent as `apikey` on GET. An agent
    never extracts it with a CLI. Or a variable named by --key-env, or a token (--token-env, sent as
    Authorization: Bearer);
  - or an export from the client: --source-dir DIR (files in DIR/<bucket>/<name>).
The source is only READ (GET). No write, no login. No redirect is followed, so the
key or token never goes to another host.

Target: CELLAR_ADDON_HOST (host without scheme; "http://..." accepted for a
local test), CELLAR_ADDON_KEY_ID, CELLAR_ADDON_KEY_SECRET, read from the
environment. Cellar bucket names are GLOBAL: --prefix (or
CELLAR_BUCKET_PREFIX) gives <prefix><bucket>; --bucket src=dest forces it.
Provisioning the add-on creates no bucket: --create-buckets creates the
missing ones (private). A bucket that answers 403 belongs to another account.

--dry-run: no download, no upload, and NO file written (--manifest is
useless, ignored); plan per bucket, valid Cellar names, variables present
(names only), existing buckets (HEAD, if the Cellar credentials are set), and
the READ ROUTE tested on the first object of each bucket (GET of byte 0 only):
the route that answers, or "private bucket: the secret key is needed".
URL and key names: SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL; SUPABASE_PUBLISHABLE_KEY, else
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY, NEXT_PUBLIC_SUPABASE_ANON_KEY or SUPABASE_ANON_KEY.
--resume <manifest> skips the objects already PASS.

--buckets b1,b2 without an object list: checks (and with --create-buckets, creates)
the buckets <prefix>b1… then stops. This is the "bucket" step of
templates/deploy-target.sh, doable as soon as the Cellar add-on exists.

No secret is printed or written: no key, no token, no signature.
Python 3 stdlib only. Exit code: 0 all PASS, 1 a FAIL, 2 usage.
"""
import argparse
import datetime
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse

SKILL_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(SKILL_ROOT, "scripts", "lib"))
from cellar import S3_BUCKET_RX, Cellar, Digest, connect, error_text, pump, s3_path  # noqa: E402


# -------------------------------------------------------------------- source
KEY_FALLBACKS = ("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY")


def read_key_file(path):
    """The key from a file the developer wrote (mode 600, not tracked by git); never printed."""
    st = os.stat(path)
    if st.st_mode & 0o077:
        raise SystemExit("--key-file %s: readable by others: chmod 600 it" % path)
    d = os.path.dirname(os.path.abspath(path))
    import subprocess
    top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    if top and os.path.realpath(top) != os.path.realpath(os.path.expanduser("~")):
        raise SystemExit("--key-file %s is inside the repository %s (a `git add .` would commit it): keep it outside, e.g. ~/sb-secret-key" % (path, top))
    with open(path, encoding="utf-8") as f:
        lines = [x.strip() for x in f.read().splitlines() if x.strip()]
    if len(lines) != 1 or any(c.isspace() for c in lines[0]):
        raise SystemExit("--key-file %s: one line expected (the key alone)" % path)
    return lines[0]


def source_key(a):
    if a.key_file:
        return read_key_file(a.key_file), "--key-file"
    if a.key_env and os.environ.get(a.key_env):
        return os.environ[a.key_env], a.key_env
    if a.key_env == "SUPABASE_PUBLISHABLE_KEY":
        for n in KEY_FALLBACKS:
            if os.environ.get(n):
                return os.environ[n], n
    return "", None


class Source:
    def __init__(self, a):
        self.folder = a.source_dir
        self.url = (a.source_url or "").rstrip("/")
        self.key = source_key(a)[0]
        self.token = os.environ.get(a.token_env, "") if a.token_env else ""
        self.route = a.route

    def describe(self):
        if self.folder:
            return f"folder {self.folder}"
        return f"{urllib.parse.urlsplit(self.url).netloc} (route {self.route})"

    def probe(self, bucket, name):
        """Dry run: which read route answers for this object (GET of byte 0 only, nothing written)."""
        if self.folder or not self.url:
            return None, "no source URL"
        failures = []
        for route in (["authenticated", "public"] if self.route == "auto" else [self.route]):
            path = urllib.parse.urlsplit(self.url).path + s3_path("storage", "v1", "object", route, bucket, name)
            c, _ = connect(self.url)
            e = {"Range": "bytes=0-0"}
            if self.key:
                e["apikey"] = self.key
            if self.token:
                e["Authorization"] = "Bearer " + self.token
            try:
                c.request("GET", path, headers=e)
                r = c.getresponse()
                r.read(64)
                st = r.status
            except (OSError, http.client.HTTPException) as ex:
                c.close()
                return None, type(ex).__name__
            c.close()
            if st in (200, 206):
                return route, None
            failures.append(f"{route}: HTTP {st}")
        return None, "; ".join(failures)

    def read(self, bucket, name, sink):
        """Writes the object into `sink`; returns (route, error)."""
        if self.folder:
            root = os.path.realpath(self.folder)
            p = os.path.realpath(os.path.join(root, bucket, name))
            if not p.startswith(root + os.sep):
                return None, "path outside the source folder"
            if not os.path.isfile(p):
                return None, "missing from the source folder"
            with open(p, "rb") as f:
                pump(f, sink)
            return "folder", None
        routes = ["authenticated", "public"] if self.route == "auto" else [self.route]
        failures = []
        for route in routes:
            path = urllib.parse.urlsplit(self.url).path + s3_path(
                "storage", "v1", "object", route, bucket, name)
            c, _ = connect(self.url)
            e = {}
            if self.key:
                e["apikey"] = self.key
            if self.token:
                e["Authorization"] = "Bearer " + self.token
            c.request("GET", path, headers=e)
            r = c.getresponse()
            if r.status == 200:
                pump(r, sink)
                c.close()
                return route, None
            failures.append(f"{route}: HTTP {r.status} {error_text(r.read())}".strip())
            c.close()
        return None, "; ".join(failures)


def record_bucket(bucket, host):
    """A bucket created here goes in the skill's ledger (ledger.sh teardown-plan lists it)."""
    import subprocess
    led_sh = os.path.join(SKILL_ROOT, "scripts", "ledger.sh")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    if top and os.path.isfile(os.path.join(top, ".clever-landing", "ledger.tsv")):
        r = subprocess.run(["bash", led_sh, "add", "bucket", bucket, bucket, "cellar=%s; created by migrate-storage.py" % host],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"  bucket {bucket} recorded in the ledger" + ("" if r.returncode == 0 else " — FAILED"))
    else:
        print(f"  bucket {bucket} created: record it for the teardown: bash {led_sh} add bucket {bucket} {bucket}")


# ---------------------------------------------------------------------- main
def load_objects(path):
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, dict):            # {"objects": [...]} or MCP result {"objects": ...}
        d = d.get("objects", d)
    if not isinstance(d, list):
        raise SystemExit(f"{path}: object list expected")
    return d


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  SUPABASE_URL=https://<ref>.supabase.co SUPABASE_PUBLISHABLE_KEY=sb_publishable_... \\\n"
               "  ./migrate-storage.py objects.json --prefix acme- --create-buckets --manifest storage-manifest.json")
    p.add_argument("objects", nargs="?", help="object list (JSON from the survey); optional with --buckets")
    p.add_argument("--manifest", default=None,
                   help="JSON manifest produced by a real migration (default: storage-manifest.json; ignored with --dry-run)")
    p.add_argument("--dry-run", action="store_true", help="no transfer, no file written: plan and checks only")
    p.add_argument("--buckets", default="", metavar="B1,B2",
                   help="source buckets to check or create (with --create-buckets), even without an object list")
    p.add_argument("--source-url", default=os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL", ""),
                   help="default: $SUPABASE_URL, else $NEXT_PUBLIC_SUPABASE_URL")
    p.add_argument("--key-file", help="file (mode 600, untracked) holding the project's secret key, written by the developer")
    p.add_argument("--source-host", help="the exact source host the developer names, when it is not <ref>.supabase.co (self-hosted)")
    p.add_argument("--key-env", default="SUPABASE_PUBLISHABLE_KEY",
                   help="NAME of the variable holding the key sent as apikey (default: %(default)s)")
    p.add_argument("--token-env", default="", help="NAME of the variable holding a token sent as Authorization: Bearer")
    p.add_argument("--route", choices=["auto", "authenticated", "public"], default="auto")
    p.add_argument("--source-dir", help="export from the client: files in DIR/<bucket>/<name>")
    p.add_argument("--prefix", default=os.environ.get("CELLAR_BUCKET_PREFIX", ""),
                   help="Cellar bucket = <prefix><bucket> (default: $CELLAR_BUCKET_PREFIX)")
    p.add_argument("--bucket", action="append", default=[], metavar="SRC=DEST", help="forced mapping")
    p.add_argument("--only", action="append", default=[], metavar="BUCKET", help="migrate only this bucket")
    p.add_argument("--create-buckets", action="store_true", help="create the missing Cellar buckets (private)")
    p.add_argument("--region", default="us-east-1", help="SigV4 signing region (default: %(default)s)")
    p.add_argument("--resume", metavar="MANIFEST", help="skip the PASS objects of this manifest")
    p.add_argument("--max-size", type=int, default=5 * 1024 ** 3,
                   help="above it: FAIL (simple PUT, multipart not handled; default 5 GiB)")
    a = p.parse_args()
    if not a.objects and not a.buckets:
        p.error("object list or --buckets expected")
    if a.dry_run and a.manifest:
        print("--dry-run: --manifest ignored, no file is written")
    a.manifest = a.manifest or "storage-manifest.json"

    objects = load_objects(a.objects) if a.objects else []
    if a.only:
        objects = [o for o in objects if o.get("bucket") in a.only]
    mapping = dict(x.split("=", 1) for x in a.bucket)
    dest = lambda b: mapping.get(b, a.prefix + b)   # noqa: E731

    done = set()
    if a.resume and os.path.exists(a.resume):
        for o in json.load(open(a.resume, encoding="utf-8")).get("objects", []):
            if o.get("status") == "PASS":
                done.add((o["bucket"], o["name"]))
    todo = [o for o in objects if (o.get("bucket"), o.get("name")) not in done]

    # Configuration: variable names only, never their value.
    missing = [v for v in ("CELLAR_ADDON_HOST", "CELLAR_ADDON_KEY_ID", "CELLAR_ADDON_KEY_SECRET") if not os.environ.get(v)]
    if todo and not a.source_dir:
        if not a.source_url:
            missing.append("SUPABASE_URL (or --source-url / --source-dir)")
        if not source_key(a)[1]:
            missing.append("a key: --key-file FILE (secret key, needed for a private bucket) or " + a.key_env
                           + (" / " + " / ".join(KEY_FALLBACKS) if a.key_env == "SUPABASE_PUBLISHABLE_KEY" else ""))
    if a.token_env and not os.environ.get(a.token_env):
        missing.append(a.token_env)

    # M1: a key or a token is only ever sent over https to <ref>.supabase.co, or to the exact host the
    # developer passed with --source-host (http only for a loopback test host). Checked before any request.
    if a.source_url and not a.source_dir and (source_key(a)[1] or a.token_env):
        u = urllib.parse.urlsplit(a.source_url)
        host = (u.hostname or "").lower()
        loop = host in ("127.0.0.1", "localhost", "::1")
        ok_host = bool(re.fullmatch(r"[a-z0-9]{20}\.supabase\.co", host)) or (a.source_host and host == a.source_host.lower())
        if not ok_host or (u.scheme != "https" and not (loop and a.source_host)):
            sys.exit("REFUSED: the key would go to %s://%s — only https://<ref>.supabase.co, or the exact host given with --source-host "
                     "(https; http only for a loopback test host). Nothing was sent." % (u.scheme or "?", host or "?"))
        print("source host: %s — the key is sent there only, as apikey on GET" % host)
    buckets = sorted({o.get("bucket") for o in todo} | {b.strip() for b in a.buckets.split(",") if b.strip()})
    invalid = [dest(b) for b in buckets if not S3_BUCKET_RX.match(dest(b) or "")]

    print(f"Objects: {len(objects)} listed, {len(done)} already PASS, {len(todo)} to migrate")
    for b in buckets:
        batch = [o for o in todo if o.get("bucket") == b]
        vol = sum(int(o.get("size") or 0) for o in batch)
        print(f"  {b:24} -> {dest(b):32} {len(batch):6} objects {vol:14} bytes")
    if not a.prefix and len(mapping) < len(buckets):
        print("Warning: without --prefix, the Cellar names are the Supabase ones; they are GLOBAL "
              "(\"avatars\" is probably already taken: HEAD 403)")
    if invalid:
        print("Invalid Cellar bucket names (S3: 3-63 chars, lowercase, digits, '-', '.'): "
              + ", ".join(invalid) + " -> --prefix or --bucket SRC=DEST")
    if missing:
        print("Missing variables: " + ", ".join(missing))

    cellar = None
    if not any(v.startswith("CELLAR_") for v in missing):
        cellar = Cellar(os.environ["CELLAR_ADDON_HOST"], os.environ["CELLAR_ADDON_KEY_ID"],
                        os.environ["CELLAR_ADDON_KEY_SECRET"], a.region)

    if a.dry_run:
        if cellar and not invalid:
            for b in buckets:
                try:
                    print(f"  bucket {dest(b)}: {cellar.bucket_state(dest(b))}")
                except OSError as e:
                    print(f"  bucket {dest(b)}: unreachable ({type(e).__name__})")
        if todo and not a.source_dir and a.source_url and source_key(a)[1]:
            probe = Source(a)
            for b in buckets:
                first = next((o for o in todo if o.get("bucket") == b and isinstance(o.get("name"), str) and o["name"]
                              and not o["name"].startswith("/") and ".." not in o["name"].split("/")), None)
                if not first:
                    continue
                route, err = probe.probe(b, first["name"])
                if route:
                    print(f"  read route for {b}: {route} answers (first object, byte 0 only) with {source_key(a)[1]}")
                else:
                    print(f"  read route for {b}: NONE answers ({err}) — a private bucket needs the project's SECRET key "
                          f"(--key-file, written by the developer): BLOCKING for the files")
        print("DRY RUN: no file downloaded, uploaded or written.")
        sys.exit(2 if (missing or invalid) else 0)

    if missing or invalid:
        sys.exit(2)

    for b in buckets:
        state = cellar.bucket_state(dest(b))
        if state == "absent" and a.create_buckets:
            ok, err = cellar.create_bucket(dest(b))
            state = "created" if ok else f"creation refused: {err}"
            if ok:
                record_bucket(dest(b), urllib.parse.urlsplit(cellar.base).netloc)
        print(f"  bucket {dest(b)}: {state}")
        if state not in ("present", "created"):
            print("Stop: Cellar bucket unavailable" + ("" if a.create_buckets else " (--create-buckets?)"))
            sys.exit(2)
    if not objects:
        print("No object list: buckets ready.")
        sys.exit(0)

    source = Source(a)
    results = []
    if a.resume and os.path.exists(a.resume):
        results = [o for o in json.load(open(a.resume, encoding="utf-8")).get("objects", [])
                   if o.get("status") == "PASS"]
    started = time.time()

    def write_manifest():
        ok = sum(1 for r in results if r["status"] == "PASS")
        doc = {
            "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tool": "migrate-storage.py",
            "source": source.describe(),
            "target": urllib.parse.urlsplit(cellar.base).netloc,
            "summary": {"objects": len(results), "pass": ok, "fail": len(results) - ok,
                        "bytes": sum(r.get("size") or 0 for r in results),
                        "seconds": round(time.time() - started, 1)},
            "objects": results,
        }
        tmp = a.manifest + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        # 600 from creation: object names can be personal data.
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1, ensure_ascii=False)
        os.replace(tmp, a.manifest)
        return doc

    with tempfile.TemporaryDirectory(prefix="migrate-storage-") as tmp:
        for i, o in enumerate(todo, 1):
            b, name = o.get("bucket"), o.get("name")
            etag = (o.get("etag") or "").strip('"').lower()
            r = {"bucket": b, "cellar_bucket": dest(b), "name": name, "owner": o.get("owner"),
                 "mimetype": o.get("mimetype"), "expected_size": o.get("size"), "size": None,
                 "route": None, "md5_etag_source": etag or None, "md5": None, "sha256_source": None,
                 "cellar_etag": None, "sha256_cellar": None, "status": "FAIL", "error": None}
            results.append(r)
            # Names read from the client's database: untrusted input.
            if (not isinstance(name, str) or not name or name.startswith("/") or len(name) > 1024
                    or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
                    or any(seg in ("..", ".") for seg in name.split("/"))):
                r["error"] = "object name refused (empty, absolute, '..', control character)"
                print(f"  FAIL {b}/<refused name>")
                continue
            try:
                file_path = os.path.join(tmp, "object")
                with open(file_path, "wb") as f:
                    e = Digest(f)
                    r["route"], err = source.read(b, name, e)
                if err:
                    r["error"] = "source: " + err
                    continue
                r.update(size=e.n, md5=e.md5.hexdigest(), sha256_source=e.sha.hexdigest())
                if o.get("size") is not None and int(o["size"]) != e.n:
                    r["error"] = f"source: {e.n} bytes read, {o['size']} expected (storage.objects)"
                    continue
                if re.fullmatch(r"[0-9a-f]{32}", etag) and etag != r["md5"]:
                    r["error"] = "source: md5 read differs from the storage.objects eTag"
                    continue
                if e.n > a.max_size:
                    r["error"] = f"{e.n} bytes > --max-size (multipart upload not handled)"
                    continue
                headers = {"content-type": o.get("mimetype") or "application/octet-stream",
                           "x-amz-meta-sha256": r["sha256_source"]}
                if o.get("cache_control"):
                    headers["cache-control"] = o["cache_control"]
                path = s3_path(dest(b), name)
                with open(file_path, "rb") as f:
                    st, h, body = cellar.call("PUT", path, body=f, headers=headers,
                                              payload_sha=r["sha256_source"], size=e.n)
                if st != 200:
                    r["error"] = f"Cellar PUT: HTTP {st} {error_text(body)}".strip()
                    continue
                r["cellar_etag"] = {k.lower(): v for k, v in h.items()}.get("etag", "").strip('"') or None
                back = Digest(None)
                st, _, n = cellar.call("GET", path, sink=back)
                if st != 200:
                    r["error"] = f"Cellar re-read: HTTP {st}"
                    continue
                r["sha256_cellar"] = back.sha.hexdigest()
                if back.n != e.n or r["sha256_cellar"] != r["sha256_source"]:
                    r["error"] = "SHA-256 re-read from Cellar differs from the source"
                    continue
                r["status"] = "PASS"
            except (OSError, http.client.HTTPException) as ex:   # network (incl. IncompleteRead), disk: FAIL, go on
                r["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
            finally:
                print(f"  {r['status']:4} {b}/{name}" + (f"  ({r['error']})" if r["error"] else ""))
                if i % 10 == 0:            # resume: at most 9 objects to redo after a cut
                    write_manifest()

    doc = write_manifest()
    s = doc["summary"]
    print(f"\n{'PASS' if s['fail'] == 0 else 'FAIL'}: {s['pass']}/{s['objects']} objects identical "
          f"(SHA-256 source = Cellar re-read), {s['bytes']} bytes, {s['seconds']} s -> {a.manifest}")
    sys.exit(0 if s["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
