"""mdsafe.py — repository text made safe to show in Markdown, by construction: ONE constructor for the documents (deliverables.py), the screens
(screens.py) and REPORT.md (plan.py).

A pure library: Python 3.9 standard library plus the skill's common.py (CONTROL_RX, untrusted) and pricing.py (the public Premium page). No I/O.

    code(text)             -> str   an inline code span whose fence is one backtick longer than any run inside
    defang(text)           -> str   a repository's words made unlinkable as security reports write them (the rule of deliverables.py's docstring)
    span(text)             -> str   code(defang(text)): the fragment constructor — a repository's text (or a sentence of the plan that holds some) as an inert code span
    fragment(value, limit) -> str   span(untrusted(value, limit)): the same, between « » (exactly what harden() makes of a «…» fragment)
    name(value, limit)     -> str   an id or a name: plain (defanged) when it is a slug, else a fragment
    cmd(text)              -> str   a command or NAME=value to copy: a plain code span, exact, unless it holds a backtick, "](" or a script scheme (span())
    fence(lines)           -> str   the backtick fence of a code block that no line of `lines` can close
    scrub(text)            -> str   our own text: no link but the two public pages, no real id, no calendar or form host (a blacklist: the second layer)
    harden(text)           -> str   a whole text made safe by construction: every «…» fragment a span of its defanged text, the rest inert piece by piece

Every repository fragment is made a code span of its own defanged text: no renderer links, embeds or interprets anything inside it, whatever it holds.
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from common import CONTROL_RX, untrusted  # noqa: E402
import pricing  # noqa: E402

PREMIUM_URL = re.search(r"https://\S+", pricing.TEXT_PREMIUM).group(0)
SUPPORT_URL = "https://www.clever.cloud/developers/doc/find-help/"
ALLOWED_URLS = (PREMIUM_URL, SUPPORT_URL)
ID_RX = re.compile(r"(orga|app|addon|user)_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)      # the shape of a real id (a prefix and a UUID), wherever it sits: glued to a word too
ID_NAMES = {"orga": "org", "app": "app", "addon": "add-on", "user": "user"}


def _id_placeholder(m):
    """[org id], [app id], [add-on id], [user id]: square brackets like the skill's other labels, never < > (that would be an HTML tag to a renderer)."""
    return "[%s id]" % ID_NAMES[m.group(1).lower()]

_TAIL = r"[^\s<>\"'«»)\]]"
LINK_RX = re.compile(                                                               # every way of writing a link that is not a plain host name
    r"(?:[a-z][a-z0-9+.-]{0,40}://" + _TAIL + r"+)"                                 # any scheme://, glued to a word, a "_" or a "-" or not: http, https, ftp, ssh, a defanged hxxps…
    r"|(?:(?<![A-Za-z0-9/.:-])//[a-z0-9-]+(?:\.[a-z0-9-]+)+" + _TAIL + r"*)"         # protocol-relative: //host.tld/path (after a "_" too)
    r"|(?:<[a-z][a-z0-9+.-]{1,31}:[^\s<>]*>)"                                       # a CommonMark autolink: <https:evil.example>
    r"|(?:<[^\s<>@]+@[^\s<>]+>)"                                                    # an e-mail autolink: <x@evil.example>
    r"|(?:(?:mailto|javascript|vbscript|xmpp):" + _TAIL + r"*)"
    r"|(?:(?<![A-Za-z0-9])(?:tel|sms):[+0-9]" + _TAIL + r"*)"                       # tel:+33…, sms:12345 — not notifications:sms:send or queue:tel:outbound
    r"|(?:(?<![A-Za-z0-9])data:[a-z]+/" + _TAIL + r"*)"
    r"|(?:www\.[\w-]+(?:\.[\w-]+)*" + _TAIL + r"*)",                              # www. and ONE label, glued to anything: marked links www.py and xwww.host.tld
    re.I)
MD_LINK_RX = re.compile(r"\[((?:[^\[\]\n]|\[[^\[\]\n]*\]){0,200})\]\(([^)\n]{0,300})\)")      # [label](target), one level of brackets in the label: the label stays, the target goes
BARE_RX = re.compile(r"(?i)\b(?:calendly\.com|cal\.com|forms\.gle|typeform\.com|tally\.so|meetings\.hubspot\.com|docs\.google\.com/forms|calendar\.google\.com|"
                     r"outlook\.office365\.com/bookings)" + _TAIL + r"*")
LINK_REMOVED = "[link removed]"
LINE_SEP_RX = re.compile("[\u0085\u2028\u2029]")                                  # Unicode line breaks: a text from the repository must stay on its line
INVISIBLE_RX = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0"
                          "\U000e0000-\U000e007f\U000e0100-\U000e01ef]")    # zero-width and bidirectional controls: text that reads one way and means another
# the labels line of every document says how the repository's words are shown (round 5): defanged, never to be pasted into a command as they are
DEFANGED_EN = "Repository text is shown defanged — (at) for the at sign, [.] and [:] in hosts and links, \uff40 for a backtick: never copy it into a command as it is."
DEFANGED_FR = ("Le texte du dépôt est affiché désamorcé — (at) pour l'arobase, [.] et [:] dans les hôtes et les liens, \uff40 pour un accent grave : "
               "ne le copiez jamais tel quel dans une commande.")


def scrub(text):
    """Our own text only (never a repository fragment: harden() keeps those out of its sight). No real orga_ / app_ / addon_ / user_ id (a placeholder), no link
    but the two public pages (any scheme://, glued to a word or a "_" or not, a protocol-relative //host.tld, mailto: javascript: tel: sms:, a <scheme:…> or <x@y>
    autolink, a www.host.tld glued to anything, as marked links it, a markdown link: the label stays, also the one a removal would leave behind), no calendar or
    form host, no Unicode line break or invisible control. A file name (docker/php-fpm/www.conf:3) or an export (user_20240101.sql) is not a link and not an id."""
    text = INVISIBLE_RX.sub("", LINE_SEP_RX.sub(" ", text))
    text = ID_RX.sub(_id_placeholder, text)
    for _ in range(8):          # "[a](b)(c)": removing the first link leaves "[link removed](c)", a link again
        done = MD_LINK_RX.sub(lambda m: (m.group(1) + " " if m.group(1).strip() else "") + LINK_REMOVED, text)
        if done == text:
            break
        text = done

    def link(m):
        u = m.group(0)
        core = u.rstrip(".,;:!?")
        return u if core in ALLOWED_URLS else LINK_REMOVED + u[len(core):]

    return BARE_RX.sub(LINK_REMOVED, LINK_RX.sub(link, text))


# ------------------------------------------------------------------------------------------------ safe by construction (rulings R31, R32)
# The scrub above is a blacklist, and a blacklist loses the race against markup. So the documents are safe by construction: every repository fragment is swapped for
# a token BEFORE anything else reads the document, and comes back at the very end as a code span built from its own defanged text; what is left between the tokens is
# our own text, made inert piece by piece. The exact rule is in the module docstring.
FRAGMENT_RX = re.compile("«\\S[^«»\n]*(?:»|(?=[«\n])|\\Z)")       # « then a non-blank, no « » inside (untrusted() guarantees it), to its » (a cut one: to the line's end)
TOKEN_CHARS_RX = re.compile("[\ue000-\ue002]")                    # the token's characters and defang()'s placeholder: replaced by U+FFFD (a non-blank for a non-blank, never
                                                                   # deleted), so no input forges a token and no fragment changes shape
TOKEN_RX = re.compile("\ue000([0-9]+)\ue001")
OWN_CODE_RX = re.compile(r"`([^`\n]+)`")                           # a code span our own sentences write (`.env`, `clever domain add …`): it stays one
ENTITY_RX = re.compile(r"&(?=#[0-9]{1,7};|#[xX][0-9a-fA-F]{1,6};|[A-Za-z][A-Za-z0-9]{0,31};)")       # an "&" that forms a character reference; any other "&" stays
_LEAD = "[ \t]*(?:(?:(?:[-*+]|[0-9]{1,9}[.)])[ \t]+|\\[[ xX]\\][ \t]+))*"          # what may open a line before its text: indentation, list markers, task boxes
QUOTE_RX = re.compile("(?m)^(%s)>" % _LEAD)                                                  # a quote marker there (in a list item too)
DEFINITION_RX = re.compile("(?m)^(%s)(\\[(?:\\\\.|[^\\\\\\]\n])*\\]):" % _LEAD)     # "[x]: url" there (a fragment, an escaped "\\]" in the label too): a link definition
WEB_TLDS = ("com", "net", "org", "io", "co", "fr", "de", "eu", "uk", "dev", "app", "ai", "me", "info", "biz", "xyz", "example", "test", "cloud", "tech", "site", "online")
FILE_EXTS = ("py", "js", "ts", "json", "yaml", "yml", "toml", "md", "txt", "conf", "ini", "sql", "log", "lock", "sh", "rb", "go", "php", "java", "cs", "xml", "html", "css", "env")
MORE_EXTS = ("service", "timer", "socket", "cfg", "properties", "gradle", "swift", "vue", "jsx", "tsx", "mjs", "cjs", "scss", "sass", "less", "svg", "png", "jpg",
             "jpeg", "gif", "ico", "webp", "pdf", "csv", "tsv", "tgz", "tar", "jar", "war", "class", "dll", "exe", "bin", "pem", "crt", "key", "csr", "pid", "sock",
             "sqlite", "sqlite3", "rst", "adoc", "tmpl", "tpl", "jinja", "twig", "erb", "ejs", "hbs", "mustache", "mod", "sum", "pyc", "pyi", "ipynb", "scala", "lua",
             "ps1", "bat", "cmd", "vbs", "aspx", "jsp", "cshtml", "sln", "csproj", "props", "plist", "hpp", "cpp", "cxx", "dart", "tfvars", "hcl", "proto", "graphql",
             "prisma", "wasm", "bak", "orig", "swp", "tmp", "sample", "dist", "template", "kts", "exs", "erl")     # 3+ letters: file names, never hosts (rule 6)
_EXT = "(?:%s)(?![\\w-])" % "|".join(FILE_EXTS)
SCHEME_RX = re.compile(r"(?i)(mailto|javascript|vbscript|xmpp|(?<![a-z0-9])data(?=:[a-z]+/)|(?<![a-z0-9])tel(?=:[+0-9])|(?<![a-z0-9])sms(?=:[+0-9])):")
WWW_RX = re.compile(r"(?i)www\.")                                                                       # www. glued to anything, followed by ONE label is enough
_NAME_END = "(?=[\\s,;)»]|:(?![0-9]+[/?#])|\\Z)"         # where a file name ends: a blank, ",", ";", ")", "»", the end, or ":" but not ":port/…" (Procfile.dev:8080/login is a URL)
WWW_FILE_RX = re.compile(r"(?i)(?:(?:%s)\.)*(?:%s)%s" % ("|".join(FILE_EXTS + MORE_EXTS), "|".join(FILE_EXTS + MORE_EXTS), _NAME_END))   # after "/": www.conf, www.py.service:4 —
                                                                                                                           # extensions only: /www.evil.shop.conf is a host
FILEISH_RX = re.compile(r"(?i)(?<![^\s/«])(\.(?:env|npmrc|nvmrc|yarnrc|babelrc|eslintrc|prettierrc|stylelintrc|editorconfig|gitignore|dockerignore|htaccess|flaskenv|"
                        r"python-version|node-version|ruby-version|tool-versions)|(?:docker|container|proc|make|gem|jenkins|vagrant|brew|rake|pod|just|earth|tilt|caddy)file|"
                        r"docker-compose|compose)((?:\.[\w-]+)+)" + _NAME_END)       # Dockerfile.dev, .env.example, Procfile.dev: file names, not hosts (_file_name decides)
# the variant words of FILE_SUFFIXES that are also live TLDs: before ":<digits>" the name is host:port, never a file name (re-review 4)
HOST_SUFFIX_WORDS = frozenset(("dev", "prod", "build", "jobs", "new", "ci", "windows", "ruby", "fly", "pg"))
# the suffixes a file-ish stem may carry and stay a file name (_file_name): file extensions, and the variant words of real repositories; never a web TLD but these
FILE_SUFFIXES = frozenset(FILE_EXTS + MORE_EXTS + ("dev", "development", "prod", "production", "local", "example", "sample", "test", "tests", "ci", "override", "staging", "stage",
                                                   "base", "debug", "default", "defaults", "dist", "docker", "web", "worker", "alpine", "slim", "build", "j2", "jinja2", "old",
                                                   "new", "backup", "release", "preview", "e2e", "integration", "unit", "k8s", "nginx", "celery", "cron", "arm64", "amd64",
                                                   "gpu", "cpu", "windows", "linux", "node", "python", "ruby", "rust", "deploy", "server", "client", "api", "frontend", "backend",
                                                   "admin", "app2", "multistage", "minimal", "full", "lite", "devcontainer", "codespaces", "gitpod", "heroku", "fly", "render",
                                                   "railway", "vercel", "netlify", "clever", "pg", "postgres", "mysql", "redis", "mongo", "db", "cache", "queue", "jobs"))
ALPHA_HOST_RX = re.compile(r"(?<![\w.@/\[\]-])(\.?(?:[\w-]+\.)+([A-Za-z]{2,24}))(?=[/?]|:[0-9])")     # name.tld before /, ? or :port, any alphabetic TLD (rule 7)
AFTER_HOST_RX = re.compile(r"(?:(?<=//)|(?<=\[)|(?<= \(at\) ))((?:[\w-]+\.)+([A-Za-z]{2,24}))(?![\w-])")   # a name right after "//", "[" or " (at) ": a host whatever
                                                                                                          # follows and whatever its last label (.sh .py .md are TLDs too)
IPV4_RX = re.compile(r"(?<![\w.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?=[/?]|:[0-9])")                      # 1.2.3.4 before /, ? or :port
TLD_DOT_RX = re.compile(r"(?i)(?<=[\w-])\.(?=(?:%s)(?![\w-])(?!\.%s))" % ("|".join(WEB_TLDS), _EXT))     # name.tld, never name.tld.ext (app.test.js)
CC_DOT_RX = re.compile(r"(?i)(?<=[\w-])\.(?=(?!%s/)[a-z]{2}/)" % "(?:%s)" % "|".join(FILE_EXTS))         # name.cc/… (evil.ru/x), never lib.py/x
SLUG_RX = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")


def code(text):
    """Markdown inline code: the delimiter is one backtick longer than any run inside; a text that starts or ends with a backtick is padded with a space."""
    longest = max([len(r) for r in re.findall(r"`+", text)] or [0])
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return fence + pad + text + pad + fence


def defang(text):
    """A repository's words made unlinkable as security reports write them (the rule of deliverables.py's docstring, in its order): no renderer and no plain-text mail
    client links "a (at) www[.]evil-sign[.]com", "https[:]//evil[.]example/x" or "mailto[:]x"; a file name (app.py, settings.dev.json, deploy/www.sh, Dockerfile.dev)
    stays as it is."""
    t = TOKEN_CHARS_RX.sub("\ufffd", ID_RX.sub(_id_placeholder, CONTROL_RX.sub(" ", LINE_SEP_RX.sub(" ", INVISIBLE_RX.sub("", text)))))     # invisible: removed; any other control: a space
    t = t.replace("`", "\uff40").replace("](", "] (").replace("]:", "] :")
    t = t.replace("@", " (at) ").replace("://", "[:]//")
    t = SCHEME_RX.sub(lambda m: m.group(1) + "[:]", t)
    t = WWW_RX.sub(lambda m: m.group(0) if t[m.start() - 1:m.start()] == "/" and WWW_FILE_RX.match(t, m.end()) else m.group(0)[:3] + "[.]", t)
    t = FILEISH_RX.sub(_file_name, t)
    t = ALPHA_HOST_RX.sub(_host_name, AFTER_HOST_RX.sub(lambda m: m.group(0).replace(".", "[.]"), TLD_DOT_RX.sub("[.]", t)))     # after a marker: no extension spares it
    t = IPV4_RX.sub(lambda m: m.group(0) if m.group(0) == "0.0.0.0" or m.group(0).startswith("127.") else m.group(0).replace(".", "[.]"), t)
    return CC_DOT_RX.sub("[.]", t).replace("\ue002", ".")


def _file_name(m):
    """A file name (FILEISH_RX: a single-label stem — a dotfile, a *file, a compose file — then its suffixes, ending at a blank, ",", ";", ")", "»", ":" or the
    end, never before "/" or ":port/") keeps its dots (hidden behind U+E002 while the host rules run) only when EVERY suffix is a known file suffix
    (FILE_SUFFIXES: Dockerfile.dev, .env.example, .env.dev.local, compose.override.yml); anything else (compose.com, Makefile.com, .env.com, dockerfile.evil.com)
    is left to the host rules."""
    labels = m.group(2).split(".")[1:]
    if labels[-1].lower() in HOST_SUFFIX_WORDS and re.match(r":[0-9]", m.string[m.end():]):
        return m.group(0)                         # Procfile.dev:8080, Makefile.build:80: a live TLD word before a port is a host (Dockerfile.dev:3 too: the safe side)
    return m.group(0).replace(".", "\ue002") if all(x.lower() in FILE_SUFFIXES for x in labels) else m.group(0)


def _host_name(m):
    """A dotted name before /, ? or :port (ALPHA_HOST_RX): every dot bracketed (sub[.]evil[.]ru/x), unless its last label is a file extension (x.php?a=1, README.rst:2;
    two letters: only the ruling's list, so evil.rs/x stays a host)."""
    tld = m.group(2).lower()
    return m.group(0) if tld in FILE_EXTS or (len(tld) > 2 and tld in MORE_EXTS) else m.group(0).replace(".", "[.]")


def _plain(text):
    """A piece of our own text, outside any code span: no link (the scrub first: it removes a mailto:x@host or an <x@host> whole, and its www rule needs nothing
    before "www.", so "a@www.host" loses its host), no e-mail address (every "@" spelled out), no "](" that could close a link label, no backtick (only our own
    spans are code), no "~" (marked's strikethrough runs on into a code span, and "~`" is plain text to it), no "<" (no tag, no autolink), no character
    reference."""
    text = scrub(text).replace("@", " (at) ").replace("](", "] (")
    return ENTITY_RX.sub("\uff06", text.replace("`", "\uff40").replace("~", "\uff5e").replace("<", "\uff1c"))


def harden(text):
    """The text of a document made safe by construction (rulings R31, R32): 0. the token's characters become U+FFFD (no fragment changes shape); 1. every
    repository fragment of that raw text becomes a token, its code span built at once from its own defanged text; 2. what is between the tokens is our own text:
    control characters and a line-leading ">" made inert, then each piece on its own (no pass sees a fragment or crosses its boundary): our own `…` spans kept as
    spans, the rest made inert by _plain(); 3. each token is swapped for its finished span; 4. on the final text, a line-leading "[label]:" is made inert (the
    scrub can leave one). Two spans never touch and no backslash precedes a fence, so every backtick of the result belongs to a span this function built."""
    spans = []

    def keep(span):
        spans.append(span)
        return "\ue000%d\ue001" % (len(spans) - 1)

    text = FRAGMENT_RX.sub(lambda m: keep(code(defang(m.group(0)))), TOKEN_CHARS_RX.sub("\ufffd", text))         # the fragments of the RAW text, shape kept
    text = CONTROL_RX.sub(lambda m: m.group(0) if m.group(0) == "\n" else " ", LINE_SEP_RX.sub(" ", INVISIBLE_RX.sub("", text)))     # our text, character by character
    text = QUOTE_RX.sub("\\1\uff1e", text)
    pieces = []
    for i, part in enumerate(TOKEN_RX.split(text)):
        if i % 2:
            pieces.append((True, spans[int(part)]))
            continue
        pos = 0
        for m in OWN_CODE_RX.finditer(part):
            pieces.append((False, _plain(part[pos:m.start()])))
            pieces.append((True, code(scrub(m.group(1)).replace("@", " (at) ").replace("](", "] ("))))      # no "](" anywhere: marked's masking looks for one
            pos = m.end()
        pieces.append((False, _plain(part[pos:])))
    out = []
    for is_span, piece in pieces:
        if is_span and out and out[-1][-1:] in ("`", "\\"):
            out.append(" ")
        if piece:
            out.append(piece)
    return DEFINITION_RX.sub("\\1\\2 :", "".join(out))        # on the final text: the scrub could make one ("[link removed]: …"); a span at a line's start opens with "`"


def span(text):
    """The fragment constructor: a repository's text — or one of the plan's sentences that holds some — as an inline code span of its defanged text. Inside
    it nothing is a link, an image, a tag or an entity to any renderer, and no backtick is left that could end it."""
    return code(defang(str(text)))


def fragment(value, limit=80):
    """Repository text between « », as a code span: span(untrusted(value, limit)), what harden() makes of a «…» fragment."""
    return span(untrusted(value, limit))


def name(value, limit=40):
    """A component id, an alias or a name read from the repository: plain (defanged: socket[.]io) when it is a slug — letters, digits, dot, dash,
    underscore —, else a fragment."""
    t = " ".join(CONTROL_RX.sub(" ", str(value)).split())
    t = t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
    return defang(t) if SLUG_RX.match(t) else fragment(value, limit)


def cmd(text):
    """A command line or an environment variable the skill prints to be copied as it is: a single-backtick code span when the text holds no backtick
    (a renderer then reads it as code only, and it stays exact), no "](" (marked's link rules look for one through a span) and no script scheme
    (mailto:, javascript:, vbscript:, xmpp:, data:, tel:, sms: — a plain-text reader could still follow one); otherwise it is shown as span()
    (defanged, not to be copied as it is): a longer fence is not masked from a renderer's link rules."""
    t = CONTROL_RX.sub(" ", str(text))
    return code(t) if "`" not in t and "](" not in t and not SCHEME_RX.search(t) else span(t)


def fence(lines):
    """The fence of a fenced code block holding `lines`: three backticks, or one more than the longest run of backticks in them, so no line closes it."""
    longest = max([len(r) for ln in lines for r in re.findall(r"`+", str(ln))] or [0])
    return "`" * max(3, longest + 1)
