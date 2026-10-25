"""cellar.py — the ONE Cellar (S3 API) client of the skill: AWS SigV4 signed by hand, path encoding,
streamed PUT/GET with size and digests computed on the fly, bucket HEAD/PUT, credentials file.

Used by scripts/migrate-files.py (URL lists, Vercel Blob, local folders) and by
modules/supabase/scripts/migrate-storage.py (Supabase Storage). Python 3 stdlib only; every
caller runs it with python3 -I. Secrets (keys, signatures, signed URLs) are never printed.
"""
import datetime
import hashlib
import hmac
import http.client
import json
import os
import re
import stat
import urllib.parse

BLOCK = 1024 * 1024
S3_BUCKET_RX = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# ------------------------------------------------------------------------- SigV4
def _h(key, text):
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def sign(method, host, path, headers, payload_sha, key_id, secret, region="us-east-1",
         service="s3", now=None, query=""):
    """Return the headers with x-amz-date, x-amz-content-sha256 and Authorization (AWS SigV4).
    `path` is already URI-encoded as sent; `query` is the canonical query string."""
    t = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    e = {k.lower(): str(v).strip() for k, v in headers.items()}
    e.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha})
    names = sorted(e)
    canonical = "\n".join([method, path, query, "".join("%s:%s\n" % (k, " ".join(e[k].split())) for k in names),
                           ";".join(names), payload_sha])
    scope = "%s/%s/%s/aws4_request" % (day, region, service)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _h(("AWS4" + secret).encode("utf-8"), day)
    for part in (region, service, "aws4_request"):
        k = _h(k, part)
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    e["authorization"] = "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s" % (
        key_id, scope, ";".join(names), signature)
    return e


def s3_path(*parts):
    """/bucket/a b.png -> /bucket/a%20b.png (RFC 3986, '/' kept), encoded once."""
    return "/" + "/".join(urllib.parse.quote(p, safe="/~") for p in parts)


# -------------------------------------------------------------------------- HTTP
def connect(base):
    u = urllib.parse.urlsplit(base)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    return cls(u.netloc, timeout=120), u.netloc


def error_text(body):
    """S3 <Code> or a short JSON message: never a header, never a signed URL."""
    t = body[:2000].decode("utf-8", "replace")
    m = re.search(r"<Code>([^<]{1,80})</Code>", t)
    if m:
        return m.group(1)
    try:
        j = json.loads(t)
        return str(j.get("message") or j.get("error") or "")[:120]
    except (ValueError, AttributeError):
        return ""


class Digest:
    """File-like sink computing size, SHA-256 and MD5 (S3 eTag check) while writing (optionally into a file)."""
    def __init__(self, f=None):
        self.f, self.n, self.sha, self.md5 = f, 0, hashlib.sha256(), hashlib.md5()

    def write(self, b):
        self.n += len(b)
        self.sha.update(b)
        self.md5.update(b)
        if self.f is not None:
            self.f.write(b)


def pump(src, dst):
    n = 0
    while True:
        b = src.read(BLOCK)
        if not b:
            return n
        dst.write(b)
        n += len(b)


class Cellar:
    def __init__(self, host, key_id, secret, region):
        self.base = host if re.match(r"^https?://", host) else "https://" + host
        self.key_id, self.secret, self.region = key_id, secret, region

    def call(self, method, path, body=None, headers=None, payload_sha=EMPTY_SHA256, size=0, sink=None):
        c, host = connect(self.base)
        e = sign(method, host, path, headers or {}, payload_sha, self.key_id, self.secret, self.region)
        if body is not None or method == "PUT":
            e["content-length"] = str(size)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in e.items():
            c.putheader(k, v)
        c.endheaders()
        if body is not None:
            class _W:
                write = c.send
            pump(body, _W())
        r = c.getresponse()
        if sink is not None and r.status == 200:
            n = pump(r, sink)
            r.close()
            c.close()
            return r.status, dict(r.getheaders()), n
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def bucket_state(self, bucket):
        st, _, _ = self.call("HEAD", s3_path(bucket))
        return {200: "present", 404: "absent",
                403: "403 (name owned by another account, or credentials refused)"}.get(st, "HTTP %d" % st)

    def create_bucket(self, bucket):
        st, _, body = self.call("PUT", s3_path(bucket), body=None, size=0)
        return st == 200, ("" if st == 200 else ("HTTP %d %s" % (st, error_text(body))).strip())



def read_env_file(path):
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit("%s is readable by group/others: chmod 600 it first" % path)
    vals = {}
    for ln in open(path, encoding="utf-8"):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln.rstrip("\n"))
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            vals[m.group(1)] = v
    return vals
