"""source_vercel.py — read-only access to a Vercel source, without ever displaying a value.

Called by scripts/source-vercel.sh. Every Vercel read of the skill goes through here; the model
never runs `vercel env …` itself (`vercel env ls` prints a truncated prefix of Config values).

  names   : `vercel env pull` into a private temporary file (mode 600, in a 700 directory),
            print the variable NAMES only, delete the file.
  secret  : same pull, keep ONLY the requested variable, write it to --out (mode 600, NAME=value),
            delete the full pulled file. Prints the name and the output path, never the value.
  blobs   : `vercel blob list --json` with the token read from a 600 file and passed in the
            child's environment only (BLOB_READ_WRITE_TOKEN), all pages, written as
            {"blobs": [...]} for migrate-files.py; prints the count and total size.
argv only, shell=False. Python 3 stdlib.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

NAME_RX = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def pull(environment, cwd, project):
    """Return (dict name->value, error). The pulled file only ever lives in a 700 temp dir."""
    d = tempfile.mkdtemp(prefix="loc-vercel-")
    os.chmod(d, 0o700)
    f = os.path.join(d, "pulled.env")
    try:
        argv = ["vercel", "env", "pull", f, "--environment", environment, "--yes"]
        if project:
            argv += ["--project", project]
        if cwd:
            argv += ["--cwd", cwd]
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                           timeout=180, shell=False)
        if p.returncode != 0 or not os.path.exists(f):
            last = (p.stderr or p.stdout).decode("utf-8", "replace").strip().splitlines()
            err = re.sub(r"[A-Za-z0-9_\-]{24,}", "<redacted>", last[-1] if last else "exit %d" % p.returncode)
            return None, "vercel env pull failed: %s" % err[:160]
        os.chmod(f, 0o600)
        vals = {}
        for ln in open(f, encoding="utf-8", errors="replace"):
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln.rstrip("\n"))
            if m:
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                vals[m.group(1)] = v
        return vals, None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "vercel env pull failed: %s" % type(exc).__name__
    finally:
        shutil.rmtree(d, ignore_errors=True)     # the full pulled file never survives


def read_token(path, name):
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit("%s is readable by group/others: chmod 600 it first" % path)
    for ln in open(path, encoding="utf-8"):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", ln.rstrip("\n"))
        if m and (m.group(1) == name or name is None):
            return m.group(2).strip().strip("\"'")
    raise SystemExit("no %s= line in %s" % (name, path))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["names", "secret", "blobs"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--environment", default="production")
    ap.add_argument("--cwd")
    ap.add_argument("--project")
    ap.add_argument("--out")
    ap.add_argument("--token-file")
    ap.add_argument("--token-name", default="BLOB_READ_WRITE_TOKEN")
    ap.add_argument("--prefix")
    a = ap.parse_args(argv)
    if a.environment not in ("production", "preview", "development"):
        print("error: --environment production|preview|development")
        return 2
    if a.cmd == "names":
        vals, err = pull(a.environment, a.cwd, a.project)
        if err:
            print(err)
            return 4
        names = sorted(k for k in vals if not k.startswith("VERCEL_"))
        system = sorted(k for k in vals if k.startswith("VERCEL_"))
        print("variables (%s, names only): %s" % (a.environment, " ".join(names) or "none"))
        if system:
            print("set by Vercel itself (not to copy): %s" % " ".join(system))
        return 0
    if a.cmd == "secret":
        if not a.name or not NAME_RX.match(a.name) or not a.out:
            print("usage: secret NAME --out FILE")
            return 2
        vals, err = pull(a.environment, a.cwd, a.project)
        if err:
            print(err)
            return 4
        if a.name not in vals or not vals[a.name]:
            print("%s is not set in %s (names: source-vercel.sh names)" % (a.name, a.environment))
            return 3
        if os.path.lexists(a.out):
            os.unlink(a.out)      # O_CREAT does not change the mode of an existing file: start from a new one
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("%s=%s\n" % (a.name, vals[a.name]))
        vals.clear()
        print("written %s to %s (mode 600, value never shown); the full pulled file was deleted" % (a.name, a.out))
        return 0
    # blobs
    if not a.token_file or not a.out:
        print("usage: blobs --token-file FILE --out LISTING.json [--prefix P]")
        return 2
    token = read_token(a.token_file, a.token_name)
    env = dict(os.environ, BLOB_READ_WRITE_TOKEN=token)
    blobs, cursor, pages = [], None, 0
    while True:
        argv = ["vercel", "blob", "list", "--json", "--limit", "1000"]
        if cursor:
            argv += ["--next", cursor]
        if a.prefix:
            argv += ["--prefix", a.prefix]
        if a.cwd:
            argv += ["--cwd", a.cwd]
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                           env=env, timeout=180, shell=False)
        if p.returncode != 0:
            last = p.stderr.decode("utf-8", "replace").strip().splitlines()
            print("vercel blob list failed: %s" % re.sub(r"[A-Za-z0-9_\-]{24,}", "<redacted>", last[-1] if last else "")[:160])
            return 4
        try:
            d = json.loads(p.stdout.decode("utf-8", "replace"))
        except ValueError:
            print("vercel blob list --json did not return JSON (CLI version?)")
            return 4
        page = d.get("blobs", []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        blobs += [{"url": b.get("url"), "downloadUrl": b.get("downloadUrl"), "pathname": b.get("pathname"),
                   "contentType": b.get("contentType"), "size": b.get("size")} for b in page if isinstance(b, dict)]
        pages += 1
        cursor = (d.get("cursor") or d.get("next")) if isinstance(d, dict) and (d.get("hasMore") or d.get("cursor") or d.get("next")) else None
        if not cursor or pages > 200:
            break
    token = None
    env = None
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"blobs": blobs}, fh, indent=1)
    print("listed %d blob(s), %d bytes, %d page(s) -> %s (for migrate-files.py --source)" % (
        len(blobs), sum(int(b.get("size") or 0) for b in blobs), pages, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
