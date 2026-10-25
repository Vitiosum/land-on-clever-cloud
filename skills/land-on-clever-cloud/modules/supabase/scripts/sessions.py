#!/usr/bin/env python3
"""
Opens real application sessions, and enrolls a TOTP factor when needed.

    ./sessions.py check.config.json

Without real sessions, nothing is tested: requests as `postgres` carry
BYPASSRLS, and requests with the anonymous key trigger no per-user policy.
RLS can only be verified under an identity that really holds a token.

NO PASSWORD is created or handled: it goes through the Admin API
(generate_link) then /verify, the path meant for a magic link. Tokens are
written to .sessions.local.json, to be ignored by git.

The TOTP is computed here (RFC 6238, no dependency): many policies require
aal2, and without a verified factor the whole flow concerned is
unreachable — which is wrongly read as "RLS blocks everything".

KEYS: sb_publishable_ / sb_secret_ keys go in the `apikey` header ONLY,
never in `Authorization: Bearer` (Supabase docs). Legacy keys (anon /
service_role JWT, deprecated by the end of 2026) keep both headers.

RUN IT ON THE SOURCE? No, unless explicitly agreed: each session opened
creates rows in auth.sessions and auth.refresh_tokens and updates
last_sign_in_at. On the source side, the acceptance check is read-only SQL
(modules/supabase/GUIDE.md, phase 6). This script is for a target that keeps
GoTrue.
"""
import base64
import hashlib
import hmac
import json
import os
import pathlib
import struct
import sys
import time
import urllib.error
import urllib.request

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


if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
    print(__doc__.strip())
    sys.exit(0)
CONF = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "check.config.json")
OUTPUT = CONF.parent / ".sessions.local.json"


def call(url, body=None, headers=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")[:300]}


def key_headers(key):
    """Headers for an API key: apikey alone for sb_, + Bearer for a legacy JWT."""
    if key.startswith("sb_"):
        return {"apikey": key}
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def totp_code(secret_b32, offset=0):
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + padding)
    counter = int(time.time()) // 30 + offset
    e = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    d = e[-1] & 0x0F
    return f"{(struct.unpack('>I', e[d:d + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def claim(token, name):
    try:
        c = token.split(".")[1]
        c += "=" * (-len(c) % 4)
        return json.loads(base64.urlsafe_b64decode(c)).get(name, "-")
    except Exception:                                          # noqa: BLE001
        return "-"


def main():
    if not CONF.exists():
        raise SystemExit(f"Missing: {CONF}. See check.config.example.json")
    conf = json.loads(CONF.read_text())
    base = conf["url"].rstrip("/")
    anon = conf["anon_key"]
    # Secret key read from a VARIABLE (name in service_role_key_env), not from
    # the file: a configuration JSON gets copied, shared, committed.
    var = conf.get("service_role_key_env", "SUPABASE_SECRET_KEY")
    sr = os.environ.get(var, "")
    if not sr and conf.get("service_role_key"):
        print(f"WARNING: secret key read from {CONF.name}; pass it through the variable {var}", file=sys.stderr)
        sr = conf["service_role_key"]
    if not sr:
        raise SystemExit(f"secret key missing: variable {var} (service_role_key_env)")
    admin = key_headers(sr)

    previous = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    sessions = {}

    for account in conf["accounts"]:
        email, role = account["email"], account.get("role", "?")
        st, d = call(f"{base}/auth/v1/admin/generate_link",
                     {"type": "magiclink", "email": email}, admin)
        if st != 200 or not d.get("hashed_token"):
            print(f"ERR  generate_link {email}: {st} {json.dumps(d)[:140]}")
            continue

        # magiclink is still accepted by /verify; the SDK documents 'email'.
        for link_type in ("magiclink", "email"):
            st, s = call(f"{base}/auth/v1/verify",
                         {"type": link_type, "token_hash": d["hashed_token"]}, {"apikey": anon})
            if st == 200 and "access_token" in s:
                break
        if st != 200 or "access_token" not in s:
            print(f"ERR  verify {email}: {st} {json.dumps(s)[:140]}")
            continue

        sessions[email] = {
            "role": role,
            "user_id": s["user"]["id"],
            "access_token": s["access_token"],
            "refresh_token": s.get("refresh_token"),
        }
        print(f"OK   {email:32} aal={claim(s['access_token'], 'aal')} role={role}")

        if not account.get("mfa"):
            continue

        # TOTP factor. The one already enrolled is reused: otherwise each rerun
        # stacks one more and exhausts the project quota.
        bearer = {"apikey": anon, "Authorization": f"Bearer {sessions[email]['access_token']}"}
        prev = previous.get(email) or {}
        fid, secret = prev.get("factor_id"), prev.get("totp_secret")

        if not (fid and secret):
            st, f = call(f"{base}/auth/v1/factors",
                         {"factor_type": "totp", "friendly_name": "acceptance"}, bearer)
            if st not in (200, 201):
                print(f"     ERR TOTP enrollment: {st} {json.dumps(f)[:140]}")
                continue
            fid, secret = f["id"], (f.get("totp") or {}).get("secret")

        st, c = call(f"{base}/auth/v1/factors/{fid}/challenge", {}, bearer)
        if st != 200:
            print(f"     ERR challenge: {st} {json.dumps(c)[:140]}")
            continue

        for offset in (0, -1, 1):          # tolerance for clock drift
            st, v = call(f"{base}/auth/v1/factors/{fid}/verify",
                         {"challenge_id": c["id"], "code": totp_code(secret, offset)}, bearer)
            if st == 200 and "access_token" in v:
                sessions[email].update(access_token=v["access_token"],
                                       refresh_token=v.get("refresh_token", sessions[email]["refresh_token"]),
                                       factor_id=fid, totp_secret=secret)
                print(f"     TOTP verified -> aal={claim(v['access_token'], 'aal')}")
                break
        else:
            print(f"     ERR TOTP verification: {json.dumps(v)[:140]}")

    write_600(OUTPUT, json.dumps(sessions, indent=1))
    print(f"\n{len(sessions)} session(s) -> {OUTPUT.name} (600, to be ignored by git)")


if __name__ == "__main__":
    main()
