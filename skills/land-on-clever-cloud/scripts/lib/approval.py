"""approval.py — the ONE approval token of the skill (provision.py and the Supabase module's
deploy-target.sh both use it).

The token is a CONSISTENCY check, not a proof that a human agreed: it binds what is about to run
to what was shown (the exact commands or plan, the price as read, the skill's own scripts, rules
and module files, the state of reused resources, the content of an input file). Consent is the
developer's explicit yes naming plan and price; an agent never re-runs with the token without it.

  python3 -I approval.py token [--text TEXT]... [--file PATH]...
      prints the 12-hex token; a missing --file counts as "absent"
"""
import hashlib
import os
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def skill_version():
    """Hash of the skill's own code, RECURSIVELY: scripts/ (with lib/), rules/, templates/ (cron…),
    and every module's scripts/ and templates/ (auth-nextjs/**, deploy-target.sh…). Every regular
    file counts (path + content), except caches (__pycache__, *.pyc, .DS_Store). Not hashed: the
    docs (*.md at the top, reference/) and tests/ — they are read, never run by an approved plan."""
    h = hashlib.sha256()
    subs = ["scripts", "rules", "templates"]
    mods = os.path.join(SKILL_DIR, "modules")
    for m in sorted(os.listdir(mods)) if os.path.isdir(mods) else []:
        for sub in ("scripts", "templates"):
            subs.append(os.path.join("modules", m, sub))
    for sub in subs:
        top = os.path.join(SKILL_DIR, sub)
        for d, _dirs, files in sorted(os.walk(top)) if os.path.isdir(top) else []:
            if "__pycache__" in d.split(os.sep):
                continue
            for f in sorted(files):
                full = os.path.join(d, f)
                if f.endswith(".pyc") or f == ".DS_Store" or os.path.islink(full) or not os.path.isfile(full):
                    continue
                with open(full, "rb") as fh:
                    h.update(os.path.relpath(full, SKILL_DIR).encode() + b"\0" + fh.read())
    return h.hexdigest()


def token(texts=(), files=()):
    h = hashlib.sha256(skill_version().encode())
    for t in texts:
        h.update(hashlib.sha256(str(t).encode("utf-8")).digest())
    for f in files:
        if os.path.isfile(f):
            with open(f, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
        else:
            h.update(b"absent")
    return h.hexdigest()[:12]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv[0] != "token":
        print("usage: python3 -I approval.py token [--text TEXT]... [--file PATH]...", file=sys.stderr)
        return 2
    texts, files, i = [], [], 1
    while i < len(argv):
        if argv[i] in ("--text", "--file") and i + 1 < len(argv):
            (texts if argv[i] == "--text" else files).append(argv[i + 1])
            i += 2
        else:
            print("error: unexpected argument %r" % argv[i], file=sys.stderr)
            return 2
    print(token(texts, files))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
