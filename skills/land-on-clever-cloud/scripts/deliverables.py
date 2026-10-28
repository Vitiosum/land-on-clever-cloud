#!/usr/bin/env python3
"""deliverables.py — the three documents to take away: a decision recap, a contact request for Clever Cloud, a pre-cutover checklist.

A decision recap to share with the team or the decision-maker, a contact request ready to send
to Clever Cloud when a human must take over, and a pre-cutover checklist (with an HDS version). Nothing here creates a resource, reads a secret
value, touches data or uses the network: it only writes text from what plan.json, answers.json and prove.sh's output already say.

Usage:
  python3 -I deliverables.py --plan .clever-landing/plan.json --kind recap|contact|checklist
         [--answers .clever-landing/answers.json] [--proof PROVE_OUTPUT]... [--lang en|fr] [--out FILE]

  --plan     plan.py's output (.clever-landing/plan.json). It must be a plan plan.py wrote on this machine (its sha256 is in the .git/ registry of the
             repository the FILE lives in, found from its path, or of the one it names), untracked: a plan that came with the repository, was edited or
             forged is the repository's word, not ours, and is refused (exit 3) like a tracked .clever-landing/. Not a JSON object: exit 2
  --kind     recap      the verdict (the production TEXT as plan.py wrote it, also when its list of blockers is empty), the POC and the three
                        production prices, the exact support / HDS / Premium lines, what you lose, the migration sketch, the next steps
             contact    the message for Clever Cloud: context in 2 lines, what is asked (one item per hand-off trigger of the plan, with what to
                        bring), closed questions, the deadline (answers.json after_poc.deadline, else [to fill]), what the skill established
                        with its proofs; with health data yes or not sure, the section "To prepare for the HDS meeting" (GDPR role, HDS activities,
                        contract): questions the developer is never asked during the journey
             checklist  before the cutover: production sizing applied, timed rehearsal, tickets with their delay, the dated cutover (TTL lowered,
                        domain added at D-3 (J-3) at the earliest, zone CNAME, write freeze, point of no return, rollback), rotation of the secrets
                        handed over (a POC secret is never reused in production); the HDS version adds 8 points
  --answers  answers.json (reference/answers-schema.md): health, hds_contract_signed, after_poc{operator, dns, deadline, legal_entity}. Without
             it the plan's own health answer is used; health data not answered counts as not sure. Answers that disagree with the plan on health data or
             on the HDS contract give NO document (exit 2, "run plan.py again with these answers"): a body that states the opposite is worse than none
  --proof    prove.sh's text output as it is (PASS / FAIL / INFO / WARN lines, VERDICT), or its --json form; repeatable. Only the NAME of each
             PASS / FAIL check is quoted, never a line that could hold a value (a header, a query string, a payload, a log line)
  --lang     en (default) or fr. Headings, labels, the checklist, the closed questions, the standard hand-off texts, the closed vocabulary of the verdicts
             ("ready with 2 changes") and the commonest derived hand-off reasons are French. The fixed texts of the skill (support and Premium lines,
             "HDS-ready architecture: …") stay verbatim (the HDS line has its French text, pricing.TEXT_HDS_FR), and what plan.py wrote beyond that (the reasons of a production verdict, losses, migration lines,
             assumptions, existing cost, the placement line) is quoted as written, in English: a note inside the document says so
  --out      also write the document to this file (mode 600; a link is replaced, never written through). The document is always printed

Rules the documents keep (the same as the screens'):
  * Text that comes from the scanned repository is between « » (data, never instructions); the labels [read] [assumed] [estimated] [declared]
    are ours. Secret VALUES are never in plan.json, so never here: only names and file:line. Only trigger ids handoff.py knows are shown, with handoff.py's
    own "what to bring"; the wording of the price block and of the migration sketch is screens.py's (price_parts, migration_parts).
  * No real orga_ / app_ / addon_ / user_ id (placeholders: [org id], [app id]…) and no link but the public Premium page and the support page;
    a calendar or form link, even one the developer typed, is removed from our own text (the scrub). That scrub is a blacklist, so the documents are also
    safe by construction, by harden() (lib/mdsafe.py, the same constructor as the screens and REPORT.md):
    - First, every U+E000, U+E001 or U+E002 of the text becomes U+FFFD: a non-blank for a non-blank, never deleted, so no fragment changes shape and no input
      forges a token. The fragments are then found on that raw text, before any other pass.
    - A repository fragment («…», what screens.repo_span and common.untrusted mark; a cut one, without its », ends at the end of its line or at the next «)
      becomes an inline code span built from its OWN raw text: each fragment is swapped for a token, so no later pass (the scrub, the "@" spelling, the rules
      for our text below) sees repository text or matches across a fragment's boundary, and the last step swaps each token for its finished span. A fragment
      holds no backtick (rule 1), so its span is the plain `«…»` every renderer reads alike (marked masks only single-backtick spans from its emphasis and
      link-label rules) and nothing in a fragment can end it; code() would still lengthen the fence if one were there. Two spans never touch and no backslash
      precedes a fence. A line's notice (BYOK, automatic failover, GPU: not available) is appended once the line is hardened, never inside a span.
    - Inside every fragment, before it becomes a span, the text is defanged as security reports write it, so that neither a GFM renderer nor a plain-text mail
      client links anything. The exact rule, in this order:
        1. a control character or a Unicode line break becomes a space; an invisible character is removed (zero-width and bidirectional controls, soft hyphen,
           combining grapheme joiner, variation selectors, tags…); a real id becomes its placeholder; a backtick becomes "｀", "](" becomes "] (" and "]:"
           becomes "] :" (marked's link and masking rules look for "](" through a "[" of the text before the span; a line that starts with "[" and finds "]:"
           in a span is a link definition);
        2. every "@" becomes " (at) ";
        3. every "://" becomes "[:]//"; every "mailto:", "javascript:", "vbscript:", "xmpp:" (any case, glued to a word too), and "data:" before a type and "/",
           "tel:" and "sms:" before "+" or a digit (not inside a word: notifications:sms:send stays) become "mailto[:]" and so on;
        4. every "www." (any case, glued to a word too, followed by ONE label is enough: www.py) becomes "www[.]", unless it opens a path segment after "/" and
           every label after it is a file extension, ending at a blank, ":" (not ":port/"), ",", ";", ")", "»" or the end (deploy/www.sh, sites/www.conf,
           deploy/www.py.service:4; /www.evil.shop.conf is a host);
        5. a file name keeps its dots: a single-label stem — a dotfile (.env, .npmrc…), a *file (Dockerfile, Procfile, Makefile…) or a compose file — whose
           suffixes are ALL known file suffixes (FILE_SUFFIXES: the extensions and the variant words dev, prod, local, example, override…), ending at a
           blank, ":" (not ":port/"), ",", ";", ")", "»" or the end, never before "/", and never before ":<digits>" when the last suffix is a live TLD
           word (HOST_SUFFIX_WORDS: dev, prod, build…) (Dockerfile.dev, .env.example:3, .env.dev.local; compose.com, Makefile.com, .env.com,
           dockerfile.evil.com, Procfile.dev:8080/login, Procfile.dev:8080, Dockerfile.dev:3 and Dockerfile.dev/x are hosts);
        6. a dot gets bracketed ("evil-sign[.]com") when a letter, digit, "_" or "-" precedes it and a web TLD follows it and ends there (no letter, digit, "_"
           or "-" after it) without "." and a file extension after it (app.test.js and settings.dev.json are file names): com net org io co fr de eu uk dev app
           ai me info biz xyz example test cloud tech site online, any case;
        7. a dotted name that is not inside a path (no letter, digit, ".", "@", "/", "-" or bracket before it; a leading "." is part of it) and is followed by
           "/", "?" or ":" and a digit has every dot bracketed (sub[.]evil[.]ru/x, evil[.]shop/login, evil[.]ru:8080/x, [.]env[.]evil[.]ru:443/x), whatever its
           alphabetic TLD (2 to 24 letters), unless that last label is a file extension (x.php?a=1, README.rst:2, www.py.service:4; two letters: only the list
           below); any dotted name right after "//", "[" or " (at) " has every dot bracketed whatever follows and whatever its last label (https[:]//evil[.]shop,
           https[:]//clever-login[.]sh/reset, [evil[.]sh/x, a (at) evil[.]sh);
           so has an IPv4 address there (1[.]2[.]3[.]4/x), except 0.0.0.0 and 127.x.x.x;
        8. so does a dot followed by any two letters and "/" anywhere ("deploy/evil[.]ru/x"), unless those two letters are a file extension (lib.py/x);
        file extensions never touched: .py .js .ts .json .yaml .yml .toml .md .txt .conf .ini .sql .log .lock .sh .rb .go .php .java .cs .xml .html .css .env;
        for rule 7 also the longer ones of MORE_EXTS (service, cfg, rst, png, sqlite, template…).
    - A Subject's repository name (a plain slug, pasted into a mail) gets no file-name exemption: every dot is bracketed (compose[.]com, app[.]py).
    - Our own text (our sentences, plan.py's and the screens', the developer's answers, a Subject's repository name already defanged as above) loses its
      control characters (but the line breaks), Unicode line breaks and invisible characters. A ">" that starts a line becomes "＞", after the indentation,
      list markers and task boxes that may come first. Then it is made inert piece by piece between the fragments: the URL scrub runs first (the second layer;
      its www rule needs nothing before "www." and ONE label after it, as marked: "a@www.host" and "www.acme" go), then every "@" is spelled " (at) ", "](" is
      split, a stray backtick becomes "｀" (the code spans our own sentences write, `…`, stay spans, scrubbed, with no "@" and no "](" either), a "~" becomes
      "～" (marked's strikethrough would run on into a span), every "<" becomes "＜", and an "&" becomes "＆" only when it forms a character reference (&#58;
      &#x3a; &amp;: "R&D", "Martin & Associés" and "&&" stay). Last, on the final text, a "[label]:" that starts a line (after the same indentation, list
      markers and task boxes) becomes "[label] :": a link definition would turn every [label] of the document into a link, and the scrub itself can leave one
      ("[link removed]: …"); a span at a line's start opens with "`", so no fragment is touched.
    - The labels line of every document says it: repository text is shown defanged ((at), [.], [:], ｀ for a backtick) and is never to be copied into a command
      as it is.
  * HDS figures (communicated by Clever Cloud on 2026-10-27): the HDS line states the rule — €200/month per HDS organisation + 1.4 × the resources placed in it, the second
    organisation at grid prices — with its source (communicated by Clever Cloud, not on the public pricing page: confirm on the quote/contract); each price
    level carries its HDS figure beside the grid total. Premium is its public formula only, never in a total, never "includes HDS". Only parhds is ever named as an HDS zone. No claim of conformity is made: the HDS line says what is in place and what is missing.
  * What does not exist is never promised: BYOK, automatic failover, GPU, Kubernetes in HDS, encryption at rest of Cellar, a contractual RTO in the
    standard offer appear only on a line that says it is not available.
  * The decision Reply of step 5 is screens.py's and is not repeated here: the recap lists the next steps instead.

Exit 0 on success, 2 on a usage error, invalid input, or answers that disagree with the plan, 3 when the plan is not ours (.clever-landing/ committed, or a
plan.json plan.py did not write on this machine: the repository's word, not ours).
"""
import argparse
import json
import os
import re
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from common import CONTROL_RX, TRACKED_STATE_ADVICE, known_plan_bytes, tracked_state  # noqa: E402
# the constructor that makes repository text safe by construction (harden, defang, code, scrub…) is shared with the screens and REPORT.md: lib/mdsafe.py
from mdsafe import (ALLOWED_URLS, BARE_RX, DEFANGED_EN, DEFANGED_FR, FRAGMENT_RX, ID_NAMES, ID_RX, INVISIBLE_RX, LINE_SEP_RX, LINK_REMOVED, LINK_RX,  # noqa: E402,F401
                    MD_LINK_RX, PREMIUM_URL, SLUG_RX, SUPPORT_URL, TOKEN_CHARS_RX, _id_placeholder, _plain, code, defang, harden, scrub)
import handoff  # noqa: E402
import pricing  # noqa: E402
import questions  # noqa: E402
import screens  # noqa: E402

KINDS = ("recap", "contact", "checklist")
LANGS = ("en", "fr")
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_PROOF_BYTES = 256 * 1024
MAX_PROOF_LINES = 14                                     # check names printed per proof
ROW_RX = re.compile(r"\A(PASS|FAIL|WARN|INFO)\s+(\S.*)\Z")
VERDICT_RX = re.compile(r"\AVERDICT: (PROVEN with warnings|PROVEN|NOT PROVEN) \(([0-9]{1,6}) s\)\Z")
HEADER_RX = re.compile(r"\A[A-Za-z][A-Za-z0-9-]{0,39}\Z")
ROUTE_RX = re.compile(r"\A/[A-Za-z0-9/_.-]{0,60}\Z")
CMD_PREFIX = "python3 -I <skill>/scripts/deliverables.py --plan .clever-landing/plan.json"       # as SKILL.md writes it: scripts/ alone does not exist in the repository

# one entry per hand-off trigger (handoff.TRIGGER_IDS): the topic of a ticket, the closed question, and the French standard texts. English: the plan's own
# sentences (handoff.py), quoted as written. French: the standard reason and the list to bring; a reason that carries details of the plan stays quoted.
TRIGGERS = {
    "hds_contract": (
        ("HDS contract and opening of parhds", "Contrat HDS et ouverture de parhds"),
        ("Can the parhds zone be opened for our organisation once the HDS contract is signed? (yes / no)",
         "La zone parhds peut-elle être ouverte pour notre organisation une fois le contrat HDS signé ? (oui / non)"),
        "Un contrat HDS avec Clever Cloud doit être signé avant toute donnée de santé réelle.",
        "le choix du modèle de contrat, le formulaire de contrat, l'onboarding, l'ouverture de la zone parhds ; aucune donnée de santé réelle avant la signature"),
    "quote_or_discount": (
        ("Quote or commercial conditions", "Devis ou conditions commerciales"),
        ("Can you send a quote for the production setup below? (yes / no)", "Pouvez-vous envoyer un devis pour la production décrite ci-dessous ? (oui / non)"),
        "Un devis, une remise ou une condition commerciale est demandé.",
        "les volumes et la durée attendus, l'entité juridique qui signerait, la date à laquelle la décision est due"),
    "premium": (
        ("Premium support (24/7)", "Support Premium (24/7)"),
        ("Does Premium cover the production setup described below? (yes / no)", "Premium couvre-t-il la production décrite ci-dessous ? (oui / non)"),
        "Un support 24/7 est demandé : Premium est l'option (formule publique sur l'écran des prix, jamais dans les totaux).",
        "les environnements de production concernés, qui doit être joint la nuit et pour quoi, la décision sur l'engagement de 12 mois"),
    "pitr": (
        ("Point-in-time recovery (PITR)", "Restauration à un instant donné (PITR)"),
        ("Can PITR be set up on our production database? (yes / no)", "La restauration à un instant donné peut-elle être mise en place sur notre base de production ? (oui / non)"),
        "La restauration à un instant donné est demandée : hors libre-service, sans prix public.",
        "le RPO visé, la taille de la base, le temps de restauration acceptable"),
    "replica": (
        ("Database replica", "Réplica de base de données"),
        ("Can a replica be set up on our production database? (yes / no)", "Un réplica peut-il être mis en place sur notre base de production ? (oui / non)"),
        "Un réplica est demandé : hors libre-service, sans prix public.",
        "la taille de la base, la coupure et le temps de bascule acceptables, la possibilité de lire sur le réplica"),
    "encrypt_existing_addon": (
        ("Encrypting an existing add-on", "Chiffrement d'un add-on existant"),
        ("Can support encrypt the existing add-on, followed by a data migration? (yes / no)", "Le support peut-il chiffrer l'add-on existant, puis migrer les données ? (oui / non)"),
        "Un add-on existant n'est pas chiffré ; le chiffrement se choisit à la création : il faut un nouvel add-on et un déplacement des données.",
        "l'identifiant et le plan de l'add-on, la taille des données, la coupure acceptable pour le déplacement"),
    "retention": (
        ("Longer retention", "Rétention plus longue"),
        ("Can the retention we need be configured? (yes / no)", "La rétention dont nous avons besoin peut-elle être configurée ? (oui / non)"),
        "Une rétention plus longue que la rétention standard est demandée (sauvegardes, journaux ou fichiers).",
        "ce qui doit être conservé (sauvegardes, journaux, fichiers), combien de temps, et la raison de cette durée"),
    "fixed_ip": (
        ("Fixed IP address", "Adresse IP fixe"),
        ("Can a fixed IP address be provided for our application? (yes / no)", "Une adresse IP fixe peut-elle être fournie pour notre application ? (oui / non)"),
        "Une adresse IP fixe est nécessaire.",
        "qui en a besoin et dans quel sens (entrant ou sortant), combien d'adresses, à partir de quelle date"),
    "vpn": (
        ("VPN link", "Lien VPN"),
        ("Can a VPN link to our network be set up? (yes / no)", "Un lien VPN vers notre réseau peut-il être mis en place ? (oui / non)"),
        "Un lien VPN vers un autre réseau est nécessaire.",
        "le réseau distant et ses sous-réseaux, le protocole, qui gère l'autre extrémité"),
    "pg_roles_or_extensions": (
        ("PostgreSQL roles or extensions", "Rôles ou extensions PostgreSQL"),
        ("Are the PostgreSQL roles and extensions we need available on the add-on? (yes / no)", "Les rôles et extensions PostgreSQL dont nous avons besoin sont-ils disponibles sur l'add-on ? (oui / non)"),
        "Des rôles ou des extensions PostgreSQL au-delà des réglages par défaut de l'add-on sont nécessaires.",
        "les rôles avec leurs droits, les extensions, la version de PostgreSQL utilisée"),
    "contractual_resilience": (
        ("Contractual resilience", "Résilience contractuelle"),
        ("A contractual RTO is not available in the standard offer: does another offer carry one? (yes / no)",
         "Un RTO contractuel n'est pas disponible dans l'offre standard : une autre offre en prévoit-elle un ? (oui / non)"),
        "Un RTO contractuel n'est pas disponible dans l'offre standard.",
        "l'engagement de disponibilité et de reprise voulu (chiffres et pénalités), les services concernés, l'entité juridique"),
    "load_test": (
        ("Load test", "Test de charge"),
        ("Can your support be warned of a load test ahead of time? (yes / no)", "Votre support peut-il être prévenu à l'avance d'un test de charge ? (oui / non)"),
        "Un test de charge est lancé, avec le support de Clever Cloud prévenu à l'avance.",
        "la fenêtre de dates, le pic visé en requêtes par seconde, les routes testées, qui lance le test"),
    "hds_ambiguous_service": (
        ("Service absent from parhds or not recommended there", "Service absent de parhds ou non recommandé"),
        ("Is each service named above available in parhds, or must it run in the second, non-HDS organisation? (available / second organisation, per service)",
         "Chaque service nommé ci-dessus est-il disponible dans parhds, ou doit-il tourner dans la seconde organisation, hors HDS ? (disponible / seconde organisation, par service)"),
        "Un service est absent de parhds ou non recommandé dans cette zone.",
        "pour chaque service : les données qui y transitent (même en cache, ou des données d'identité), l'organisation où il tournerait, l'avis du DPO s'il y en a un"),
    "decisive_no": (
        ("A blocking point", "Un point bloquant"),
        ("For each blocking point listed, is there a workaround? (yes / no, per point)", "Pour chaque point bloquant listé, existe-t-il un contournement ? (oui / non, par point)"),
        "Une exigence décisive reçoit un non.",
        "les points bloquants de l'écran de compatibilité et l'exigence que chacun met en cause : un vrai non, ou un contournement à regarder ensemble"),
    "public_buyer_or_dpo": (
        ("Public buyer, RSSI or DPO", "Acheteur public, RSSI ou DPO"),
        ("Can the security questionnaire be shared with our RSSI or DPO? (yes / no)", "Le questionnaire de sécurité peut-il être partagé avec notre RSSI ou notre DPO ? (oui / non)"),
        "Un acheteur public, un responsable de la sécurité des systèmes d'information (RSSI) ou un DPO participe à la décision.",
        "leurs noms et rôles, le cadre d'achat le cas échéant, le questionnaire de sécurité à remplir"),
    "beyond_catalogue": (
        ("Database beyond the catalogue", "Base de données au-delà du catalogue"),
        ("Is there a database plan above the largest one in the catalogue, or a way to split the data? (plan / split / neither)",
         "Existe-t-il un plan de base de données au-dessus du plus grand du catalogue, ou un moyen de découper les données ? (plan / découpage / aucun)"),
        "Une base de données dépasse le plus grand plan dédié chiffré du catalogue.",
        "la taille réelle des données et sa croissance, le pic de connexions, le profil lecture/écriture, ce qui peut être archivé ou découpé"),
}


class Usage(Exception):
    """A problem with the inputs: the message is printed, the exit code is 2."""


# ------------------------------------------------------------------------------------------------ small readers
def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, (list, tuple)) else []


def _strs(v):
    return [x for x in _list(v) if isinstance(x, str) and x.strip()]


def _free(v, limit=200):
    """The developer's own words (answers.json), as one line: control characters and runs of blanks removed, cut."""
    t = " ".join(CONTROL_RX.sub(" ", INVISIBLE_RX.sub("", str(v))).split())          # an invisible character is removed, any other control is a blank
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _no_constant(name):
    raise ValueError("constant %s is not JSON" % name)


def _one_of(v, options):
    """v when it is a text among `options` (a list or a dict from a plan is never a key), else None."""
    return v if isinstance(v, str) and v in options else None


def _clean(v):
    """The plan or the answers with every real-looking id replaced by its placeholder, before any text is cut or shown: an id never enters the documents,
    and never half of one (a cut in the middle)."""
    if isinstance(v, str):
        return ID_RX.sub(_id_placeholder, v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return dict((k, _clean(x)) for k, x in v.items())
    return v


def _read_plan(path, limit):
    """The plan's bytes, read ONCE, from a regular file (a FIFO, a device or a directory is not a plan, and is never waited on). The caller parses THESE bytes and
    checks THEIR sha256: what was checked is what is shown."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise Usage("cannot read the plan %s: %s" % (path, exc.strerror or "error"))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Usage("%s is not a regular file: run plan.py first" % path)
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as exc:
        raise Usage("cannot read the plan %s: %s" % (path, exc.strerror or "error"))
    finally:
        os.close(fd)
    if size > limit:
        raise Usage("%s is larger than %d bytes" % (path, limit))
    return b"".join(chunks)


def _parse_plan(raw, path):
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise Usage("%s is not valid JSON (the plan): run plan.py first" % path)


# ------------------------------------------------------------------------------------------------ the proof (prove.sh's output)
def planned_routes(plan):
    """The paths the plan itself asks prove.sh to check (components[].prove.checks and health_path): the only routes a check name may quote. A path the
    plan never listed could hold anything (an identifier, a token in a cron route), so it is not named."""
    out = set()
    for comp in _list(_dict(plan).get("components")):
        pr = _dict(_dict(comp).get("prove"))
        for ck in _list(pr.get("checks")):
            if isinstance(_dict(ck).get("path"), str):
                out.add(ck["path"])
        hp = _dict(pr.get("health_path"))
        if isinstance(hp.get("path"), str):
            out.add(hp["path"])
    return out


def _route_ok(path, known):
    """A route that is safe to name: listed by the plan, plain segments only, no query, nothing that looks like a token or an identifier."""
    if path not in known or not ROUTE_RX.match(path):
        return False
    segs = [s for s in path.split("/") if s]
    return len(segs) <= 5 and all(len(s) <= 24 and len(re.findall(r"[0-9]", s)) < 8 for s in segs)


def check_name(detail, lang, known=frozenset()):
    """The name of one PASS / FAIL check of prove.sh, without any value: what was checked, never what was found. A line that matches nothing known
    is named generically. (A URL, a header value, a payload, a marker, a query string or a log line is never repeated.)"""
    en = lang != "fr"
    d = detail.strip()
    m = re.match(r"\AGET https?://\S+ -> ([0-9]{3})\b", d)
    if m:
        return "the app answers on its URL (HTTP %s)" % m.group(1) if en else "l'application répond sur son URL (HTTP %s)" % m.group(1)
    m = re.match(r"\A(?:header |response header )([A-Za-z0-9-]{1,40})\b", d)
    if m and HEADER_RX.match(m.group(1)):
        return "response header %s" % m.group(1) if en else "en-tête de réponse %s" % m.group(1)
    if re.match(r"\Apage (?:does not )?contains?\b", d):
        return "the page holds the expected marker" if en else "la page contient le marqueur attendu"
    if re.match(r"\A(?:bundle |the page references no JS bundle|\"[^\"]*\" in none of )", d):
        return "build-time variables inlined in the JS bundle" if en else "variables de build injectées dans le bundle JS"
    if d.startswith("tables in PostgreSQL") or d.startswith("tables not in PostgreSQL"):
        return "the app's tables are in PostgreSQL, not in a SQLite fallback" if en else "les tables de l'application sont dans PostgreSQL, pas dans un repli SQLite"
    if re.match(r"\A(?:form upload|upload round trip|upload POST|uploaded, but)", d):
        return "file upload and read back (SHA-256)" if en else "envoi de fichier puis relecture (SHA-256)"
    if d.startswith("socket.io"):
        return "socket.io event received" if en else "événement socket.io reçu"
    if re.match(r"\A(?:logs contain|logs \()", d):
        return "expected line in the logs" if en else "ligne attendue dans les journaux"
    if "cron job output" in d or d.startswith("no cron output"):
        return "cron job ran" if en else "la tâche cron s'est exécutée"
    if "build/run logs contain" in d:
        return "build evidence in the logs" if en else "preuve de build dans les journaux"
    m = re.match(r"\A([A-Z]{3,7}) (/\S*?)(?: \(with Authorization\))? -> ([0-9]{3})\b", d)
    if m:
        auth = " (with Authorization)" if "(with Authorization)" in d.split("->")[0] else ""
        if _route_ok(m.group(2), known):
            return ("%s %s%s -> %s" % (m.group(1), m.group(2), auth, m.group(3))) if en else ("%s %s%s -> %s" % (m.group(1), m.group(2), " (avec Authorization)" if auth else "", m.group(3)))
        return ("an HTTP check %s -> %s on a path the plan does not list (path not quoted)" % (m.group(1), m.group(3))) if en else (
            "une vérification HTTP %s -> %s sur un chemin que le plan ne liste pas (chemin non cité)" % (m.group(1), m.group(3)))
    if d.startswith("only '/ answers'"):
        return "only the home page was checked" if en else "seule la page d'accueil a été vérifiée"
    if d.startswith("closed route"):
        return "a route that must stay closed on another domain of the project (404)" if en else "une route qui doit rester fermée sur un autre domaine du projet (404)"
    return "another check (not quoted)" if en else "une autre vérification (non citée)"


def parse_proof(text, lang, known=frozenset()):
    """prove.sh's text output, or its --json form: {"verdict", "seconds", "passed": [name], "failed": [name], "warnings": n}. Only PASS / FAIL rows outside the
    log block are read; the lines between "---- last logs" and the closing "----" are log lines, whatever they start with."""
    rows, warnings, verdict, seconds = [], 0, None, None
    t = text.lstrip()
    if t.startswith("{"):
        try:
            data = json.loads(t, parse_constant=_no_constant)
        except (ValueError, RecursionError):
            raise Usage("the proof file looks like JSON but is not valid JSON")
        data = _dict(data)
        if data.get("verdict") in ("PROVEN", "PROVEN with warnings", "NOT PROVEN"):
            verdict = data["verdict"]
        if isinstance(data.get("seconds"), int) and not isinstance(data["seconds"], bool) and 0 <= data["seconds"] < 10 ** 6:
            seconds = data["seconds"]
        for c in _list(data.get("checks")):
            c = _dict(c)
            if c.get("status") in ("PASS", "FAIL", "WARN", "INFO") and isinstance(c.get("detail"), str):
                rows.append((c["status"], c["detail"]))
    else:
        in_logs = False
        for raw in text.split("\n"):          # "\n" only: splitlines() also breaks on U+2028, U+2029 and NEL, which a log line can carry to forge a row
            line = CONTROL_RX.sub(" ", raw).rstrip()
            if line.startswith("---- last logs"):
                in_logs = True
                continue
            if in_logs:
                in_logs = line.strip() != "----"
                continue
            m = VERDICT_RX.match(line)
            if m:
                verdict, seconds = m.group(1), int(m.group(2))
                continue
            m = ROW_RX.match(line)
            if m:
                rows.append((m.group(1), m.group(2)))
    passed, failed = [], []
    for status, detail in rows:
        if status == "WARN":
            warnings += 1
        elif status in ("PASS", "FAIL"):
            name = check_name(detail, lang, known)
            bucket = passed if status == "PASS" else failed
            if name not in bucket:
                bucket.append(name)
    return {"verdict": verdict, "seconds": seconds, "passed": passed, "failed": failed, "warnings": warnings}


# ------------------------------------------------------------------------------------------------ what the three documents share
class Ctx(object):
    """The plan, the answers and the proofs read once: the language, the health data answer (and whether it is assumed), the HDS contract, the
    developer's after-POC words, the price state. Every document asks this object, so they cannot disagree."""

    def __init__(self, plan, answers, answers_given, proofs, lang):
        self.plan = plan if isinstance(plan, dict) else {}
        self.answers = answers if isinstance(answers, dict) else {}
        self.answers_given = answers_given
        self.lang = lang
        self.proofs = proofs
        self.eff = questions.effective_answers({}, self.answers)
        self.today = time.strftime("%Y-%m-%d")
        self.name = screens.project_name(self.plan)
        top = _dict(self.plan.get("repo")).get("toplevel")
        base = os.path.basename(top.rstrip("/")) if isinstance(top, str) and top.strip("/") else ""
        # a Subject is pasted into a mail: a plain slug with EVERY dot bracketed — no file-name exemption there (compose[.]com, Makefile[.]com, www[.]py)
        self.subject_name = defang(base).replace("[.]", ".").replace(".", "[.]") if SLUG_RX.match(base) else self.t("this repository", "ce dépôt")
        self.report_only = screens.is_report_only(self.plan)
        prov = self.plan.get("primary_provenance")
        self.provenance = screens.provenance_name(self.plan) if isinstance(prov, str) and prov else self.t("an unknown source", "une source inconnue")
        v = self.plan.get("verdict")
        self.verdict = v if isinstance(v, dict) else {"poc": v}
        self.plan_assumed = set(_strs(self.plan.get("answers_assumed")))
        self.conflicts = []
        self._pp = None
        self._health()
        self._contract()
        # the plan as the screens' helpers must read it: the health answer of THIS document (the plan may carry none: then it is the answers' or the default)
        self.view = dict(self.plan, health_answer=self.health,
                         answers_assumed=[x for x in _strs(self.plan.get("answers_assumed")) if x != "health"] + (["health"] if self.health_assumed else []))
        self.after = {k: _free(v) for k, v in _dict(self.eff.get("after_poc")).items() if isinstance(v, str) and v.strip()}

    # -- language
    def t(self, en, fr):
        return fr if self.lang == "fr" else en

    @property
    def semi(self):
        """The separator of a list of phrases: French puts a space before the semicolon."""
        return " ; " if self.lang == "fr" else "; "

    @property
    def fill(self):
        return self.t("[to fill]", "[à compléter]")

    # -- health data: the answers file, else the plan, else not sure [assumed]. Where the two disagree there is NO document: main() refuses (exit 2), a
    #    body that states the opposite of the answers is worse than none
    def _health(self):
        plan_h = _one_of(self.plan.get("health_answer"), questions.HEALTH)
        asked = _one_of(self.answers.get("health"), questions.HEALTH)
        if asked and plan_h and asked != plan_h:
            self.conflicts.append(("health data", asked, plan_h))
            self.health, self.health_assumed = asked, False
        elif asked:
            self.health, self.health_assumed = asked, False
        elif plan_h:
            self.health, self.health_assumed = plan_h, "health" in self.plan_assumed
        else:
            self.health, self.health_assumed = "unsure", True
        self.hds = self.health in ("yes", "unsure")

    def _contract(self):
        asked = _one_of(self.answers.get("hds_contract_signed"), ("yes", "no"))
        planned = _one_of(self.plan.get("hds_contract_signed"), ("yes", "no"))
        if asked and planned and asked != planned:
            self.conflicts.append(("HDS contract", asked, planned))
            self.contract, self.contract_assumed = asked, False
        elif asked or planned:
            self.contract = asked or planned
            self.contract_assumed = False if asked else "hds_contract_signed" in self.plan_assumed
        else:
            self.contract, self.contract_assumed = "no", True

    @property
    def signed(self):
        return self.contract == "yes"

    def assumed(self, key):
        """Is this answer a default? From the answers file when there is one, else from the plan's own list."""
        if key in ("load", "service"):
            return key in (self.eff["assumed"] if self.answers_given else self.plan_assumed)
        return key in self.plan_assumed

    # -- the pieces of text every document uses
    def health_text(self):
        word = {"yes": self.t("yes", "oui"), "no": self.t("no", "non"), "unsure": self.t("not sure", "pas sûr")}[self.health]
        return word + (" [assumed]" if self.health_assumed else self.t(" (answered)", " (répondu)"))

    def contract_text(self):
        if self.signed:
            return self.t("declared signed [declared]", "déclaré signé [declared]")
        return self.t("not signed", "non signé") + (" [assumed]" if self.contract_assumed else self.t(" (answered)", " (répondu)"))

    def price_parts(self):
        """The wording of the price block (screens.price_parts, ONE copy for the screen and the documents), in this document's language and health answer."""
        if self._pp is None:
            self._pp = screens.price_parts(self.plan, self.lang, True, health=self.health)
        return self._pp

    @property
    def price_state(self):
        """"report-only", "unavailable" (the grid could not be read), "absent" (price not read) or "ok"."""
        return self.price_parts()["state"]

    def proof_verdict(self, pf):
        """prove.sh's verdict line, in the document's language (PROVEN / PROVEN with warnings / NOT PROVEN)."""
        v = pf["verdict"]
        if v is None:
            return self.t("no VERDICT line (incomplete output?)", "pas de ligne VERDICT (sortie incomplète ?)")
        return _FR_VERDICT[v] if self.lang == "fr" else v

    def poc_verdict(self):
        t = screens.one_line(self.verdict.get("poc") or "unknown", 160)
        return _fr_poc(t) if self.lang == "fr" else t

    def production_verdict(self):
        """The production verdict as plan.py wrote it (the whole text, also when its list of blockers is empty), the repository's detail between « »; in
        French only the leading word is translated."""
        t = screens.production_text(self.verdict.get("production") or "unknown", _strs(self.verdict.get("production_blockers")))
        return _fr_production(t) if self.lang == "fr" else t

    # -- what the plan holds (the checklist asks for a step only when the plan has the thing it is about)
    def components(self):
        return [x for x in _list(self.plan.get("components")) if isinstance(x, dict) and x.get("target") != "drop"]

    def has_database(self):
        return any(x.get("service") in DB_SERVICES and x.get("target") != "keep-saas" for x in self.components())

    def has_scheduler(self):
        """A beat / cron / scheduler process (its own single-instance app: `singleton`) or a cron job."""
        return any(x.get("singleton") is True or (x.get("kind") == "job" and re.match(r"cron(-|\Z)", str(x.get("id")))) for x in self.components())

    def has_redis(self):
        return any(x.get("service") == "redis" and x.get("target") == "addon" and not x.get("optional") for x in self.components())

    def triggers(self):
        """The hand-off triggers of the plan: only ids handoff.py knows (an unknown id is dropped, never shown, never a topic), each with its own reason."""
        return [h for h in _list(self.plan.get("handoff")) if isinstance(h, dict) and h.get("id") in handoff.TRIGGER_IDS
                and isinstance(h.get("why"), str) and h["why"].strip()]

    def trigger_info(self, h):
        """(topic, why, bring, question) of one trigger, in the document's language. `bring` is always handoff.py's standard list, never the plan's text;
        `why` is the standard sentence or the reason the plan derived from the answers (French: the standard reason, the derived one quoted in English)."""
        tid = h["id"]
        topic_t, ask_t, why_fr, bring_fr = TRIGGERS[tid]
        std_why, std_bring = handoff.standard_text(tid)
        why = screens.one_line(h.get("why"), 400)
        bring = screens.one_line(std_bring, 400)
        if self.lang == "fr":
            derived = _fr_why(why)
            why = why_fr if why == screens.one_line(std_why, 400) else derived or why_fr + " (détail du plan, en anglais : %s)" % why
            bring = bring_fr
        i = 1 if self.lang == "fr" else 0
        return topic_t[i], why, bring, ask_t[i]


DB_SERVICES = ("postgresql", "mysql", "mongodb")
_FR_VERDICT = {"PROVEN": "PROUVÉ", "PROVEN with warnings": "PROUVÉ avec avertissements", "NOT PROVEN": "NON PROUVÉ"}
_FR_POC = {"ready": "prêt", "partial landing": "atterrissage partiel", "not a fit today": "pas adapté aujourd'hui",
           "not yet covered — report only": "pas encore couvert — rapport seul", "unknown": "inconnu"}


_FR_PEOPLE = {"yes": "oui", "not sure": "pas sûr"}
_FR_SHAPES = {"predictable peaks": "pics prévisibles", "events": "événements"}


def _fr_why(text):
    """The reasons handoff.py derives from the answers, in French, when they have the exact shape it writes (the commonest triggers); None for anything else
    (a reason that carries a detail of the repository stays quoted in English)."""
    t = text.strip()
    m = re.match(r"\Ahealth data: (yes|not sure)( \[assumed\])?; HDS contract not declared signed( \[assumed\])?\.\Z", t)
    if m:
        return "données de santé : %s%s ; contrat HDS non déclaré signé%s." % (_FR_PEOPLE[m.group(1)], m.group(2) or "", m.group(3) or "")
    unless = r"(?:; %s ([A-Za-z0-9_., +-]{1,200}))?" % re.escape(handoff.UNLESS_WITHOUT)    # an optional database add-on (R28)
    fr_unless = lambda g: " ; sauf si vous partez sans %s" % g if g else ""
    m = re.match(r"\ARPO of ([0-9.]+) h asked, under the ([0-9]+) h of the daily backups: PITR is needed \(no public price, outside self-service\)" + unless + r"\.\Z", t)
    if m:
        return "RPO de %s h demandé, sous les %s h des sauvegardes quotidiennes : la restauration à un instant donné est nécessaire (sans prix public, hors libre-service)%s." % (
            m.group(1), m.group(2), fr_unless(m.group(3)))
    m = re.match(r"\ADowntime tolerated is minutes: a replica is needed \(no public price, outside self-service\)" + unless + r"\.\Z", t)
    if m:
        return "La coupure tolérée se compte en minutes : un réplica est nécessaire (sans prix public, hors libre-service)%s." % fr_unless(m.group(1))
    m = re.match(r"\ALoad is (predictable peaks|events): a load test is run with Clever Cloud support warned beforehand\.\Z", t)
    if m:
        return "La charge est de type « %s » : un test de charge est lancé, avec le support de Clever Cloud prévenu à l'avance." % _FR_SHAPES[m.group(1)]
    m = re.match(r"\Ahealth data \((yes|not sure)\) and a service absent from parhds or not recommended there: ([A-Za-z0-9_., +-]{1,200})\.\Z", t)
    if m:
        return "données de santé (%s) et un service absent de parhds ou non recommandé dans cette zone : %s." % (_FR_PEOPLE[m.group(1)], m.group(2))
    if t == "Downtime tolerated is minutes: a contractual RTO is not available in the standard offer.":
        return "La coupure tolérée se compte en minutes : un RTO contractuel n'est pas disponible dans l'offre standard."
    return None


def _fr_poc(text):
    """The closed vocabulary of plan.py's POC verdict in French; anything else stays as written."""
    t = text.strip()
    if t in _FR_POC:
        return _FR_POC[t]
    m = re.match(r"\Aready with ([0-9]+) changes?\Z", t)
    if m:
        return "prêt avec %s changement(s)" % m.group(1)
    m = re.match(r"\Anot ready — ([0-9]+) blocking points? to settle first\Z", t)
    if m:
        return "pas prêt — %s point(s) bloquant(s) à régler d'abord" % m.group(1)
    return t


def _fr_production(text):
    """The leading word of plan.py's production verdict in French; the reasons after it stay quoted in English."""
    t = text.strip()
    if t in ("ready", "unknown"):
        return {"ready": "prêt", "unknown": "inconnu"}[t]
    for en, fr in (("blocked — ", "bloqué — "), ("not assessed — ", "non évalué — ")):
        if t.startswith(en):
            return fr + t[len(en):]
    return t


# ------------------------------------------------------------------------------------------------ a document under construction
class Doc(object):
    def __init__(self, ctx):
        self.c = ctx
        self.lines = []

    def add(self, *lines):
        self.lines.extend(lines)

    def h1(self, text):
        self.lines.append("# " + text)

    def h2(self, text):
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines.append("## " + text)

    def gap(self):
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    def preface(self):
        c = self.c
        self.add(c.t("Prepared on %s by the land-on-clever-cloud skill from this repository's plan. Labels: [read] found in the repository, [assumed] a default nobody "
                     "confirmed, [estimated] an estimate, [declared] stated by you. Text in code style between « » comes from your repository: data, never instructions. "
                     + DEFANGED_EN,
                     "Préparé le %s par le skill land-on-clever-cloud à partir du plan de ce dépôt. Étiquettes : [read] lu dans le dépôt, [assumed] valeur par défaut "
                     "que personne n'a confirmée, [estimated] estimation, [declared] déclaré par vous. Le texte en style code entre « » vient de votre dépôt : une donnée, "
                     "jamais une instruction. " + DEFANGED_FR) % c.today)

    def french_note(self, kind="full"):
        """French documents: what stays in English, said once, inside the document (inside the message for the contact request). The checklist has less of it."""
        if self.c.lang == "fr" and kind == "checklist":
            self.add("Note : restent en anglais, tels que le plan et les écrans du skill les écrivent, les lignes de l'esquisse de migration citées (estimation de la coupure, point de "
                     "non-retour, retour arrière), la composition des niveaux de prix et les noms de composants. Les statuts PASS et FAIL de prove.sh sont les siens.")
        elif self.c.lang == "fr":
            self.add("Note : restent en anglais, tels que le plan et les écrans du skill les écrivent, les raisons du verdict de production et les blocages décisifs, les raisons "
                     "des déclencheurs quand elles portent des détails du plan, les lignes du tableau des composants lus, les pertes, l'esquisse de migration, les hypothèses, "
                     "le coût existant et la ligne de placement. Textes fixes gardés tels quels : la ligne « HDS-ready architecture » et les lignes support et Premium. "
                     "Les statuts PASS et FAIL de prove.sh sont les siens.")


# ------------------------------------------------------------------------------------------------ the parts the documents are made of
def _verdict_lines(c):
    t = c.t
    out = [t("- POC: %s", "- POC : %s") % c.poc_verdict(), t("- Production: %s", "- Production : %s") % c.production_verdict()]
    blockers = _strs(c.verdict.get("production_blockers"))
    if blockers:
        out.append(t("- Decisive noes: %s", "- Blocages décisifs : %s") % c.semi.join(screens.production_text(b, [b]) for b in blockers[:5]))
    out.append("- " + screens.hds_line(c.view, True, c.lang))
    out.append("- " + screens.placement_text(c.view, True))
    return out


def _existing_line(c, line):
    """An existing-cost line of the price block; the third-party price page is not repeated here (REPORT.md holds it with its date)."""
    where = c.t("public price page, link and date in REPORT.md", "page de prix publique, lien et date dans REPORT.md")
    return re.sub(r"\A(?:Existing cost|Coût existant), ", "", re.sub(r"https?://\S+?(?=, read on)", where, line))


def _price_section(d):
    """The price block: screens.price_parts() is the one copy of its wording; this only lays it out as a document."""
    c = d.c
    t = c.t
    pp = c.price_parts()
    d.h2(t("Price", "Prix"))
    d.add(t("Public grid prices, 720-hour month (30 days), EUR excluding taxes, billed per second.",
            "Prix publics de la grille, mois de 720 h (30 jours), en euros hors taxes, facturés à la seconde."))
    d.add(*pp["status"])
    if pp["state"] == "ok":
        d.add(pp["head"])
        d.add(*["- " + l for l in pp["levels"]])
        d.add(pp["legend"])
        d.add(*pp["flags"])
    if pp["services"]:
        d.add(t("Services:", "Services :"))
        d.add(*["- " + x for x in pp["services"]])
    d.add(t("Existing cost:", "Coût existant :"))
    d.add(*["- " + _existing_line(c, x) for x in pp["existing"]])
    d.add(*pp["outbound"])
    if pp["assumptions"]:
        d.add(t("Assumptions:", "Hypothèses :"))
        d.add(*["- " + a for a in pp["assumptions"]])


def _losses_section(d):
    c = d.c
    losses = [screens.one_line(l.get("text"), 220) for l in _list(c.plan.get("losses")) if isinstance(l, dict) and l.get("text")]
    d.h2(c.t("What you would lose" if c.report_only else "What you lose or must do differently",
             "Ce que vous perdriez" if c.report_only else "Ce que vous perdez ou devez faire autrement"))
    d.add(*["- " + x for x in losses] or [c.t("- Nothing the plan names.", "- Rien que le plan ne nomme.")])


def _migration_section(d):
    c = d.c
    sketch, lines = screens.migration_parts(c.plan)
    d.h2(c.t("Migration sketch [estimated]", "Esquisse de migration [estimated]") if sketch else c.t("Migration", "Migration"))
    d.add(*["- " + l for l in lines] or [c.t("- Not assessed.", "- Non évaluée.")])


def _proof_lines(c):
    """The proof, as (level, text): level 0 is a sentence (the verdict of a proof, or that there is none), level 1 one check name. The verdict and the
    NAMES of the PASS / FAIL checks only: never a line that could hold a value."""
    if not c.proofs:
        return [(0, c.t("No POC proof was given to this document: run prove.sh on the POC, then pass its output with --proof.",
                        "Aucune preuve du POC n'a été fournie à ce document : lancez prove.sh sur le POC, puis passez sa sortie avec --proof."))]
    out = []
    for i, pf in enumerate(c.proofs, 1):
        verdict = c.proof_verdict(pf)
        secs = " (%d s)" % pf["seconds"] if pf["seconds"] is not None else ""
        label = c.t("POC proof", "Preuve du POC") + ((" %d/%d" % (i, len(c.proofs))) if len(c.proofs) > 1 else "")
        out.append((0, c.t("%s (prove.sh): %s%s — %d passed, %d failed, %d warning(s).", "%s (prove.sh) : %s%s — %d réussie(s), %d en échec, %d avertissement(s).")
                     % (label, verdict, secs, len(pf["passed"]), len(pf["failed"]), pf["warnings"])))
        names = [("PASS ", n) for n in pf["passed"]] + [("FAIL ", n) for n in pf["failed"]]
        out += [(1, s_ + n) for s_, n in names[:MAX_PROOF_LINES]]
        if len(names) > MAX_PROOF_LINES:
            out.append((1, c.t("… %d more checks in the proof output", "… %d autres vérifications dans la sortie de la preuve") % (len(names) - MAX_PROOF_LINES)))
        if pf["warnings"]:
            out.append((1, c.t("Read the WARN lines of the proof output: they are not quoted here.", "Lisez les lignes WARN de la sortie de la preuve : elles ne sont pas citées ici.")))
    return out


def _after_poc_line(c):
    a = c.after
    f = lambda k: a.get(k) or c.fill
    return c.t("Operator: %s · DNS: %s · Deadline: %s · Signing entity: %s", "Qui opère : %s · DNS : %s · Échéance : %s · Entité signataire : %s") % (
        f("operator"), f("dns"), f("deadline"), f("legal_entity"))


# ------------------------------------------------------------------------------------------------ the recap
def _next_steps(c):
    p = c.plan
    t = c.t
    steps = []
    if c.report_only:
        steps.append(t("Report only: nothing was created and no price was quoted; this recap is what was read.",
                       "Rapport seul : rien n'a été créé et aucun prix n'a été annoncé ; ce récapitulatif est ce qui a été lu."))
    blocking = _strs(p.get("blocking"))
    if blocking:
        steps.append(t("Settle the blocking point(s) of the POC first: %s.", "Réglez d'abord le ou les point(s) bloquant(s) du POC : %s.") % c.semi.join(screens.repo_span(b, 120) for b in blocking[:3]))
    fix = screens.blocker_lines(p, True)[0]
    if fix:
        steps.append(t("Before any real data, fix the security points found in the repository (%d, listed in REPORT.md): they do not block the POC.",
                       "Avant toute donnée réelle, corrigez les points de sécurité trouvés dans le dépôt (%d, listés dans REPORT.md) : ils ne bloquent pas le POC.") % len(fix))
    if c.hds and not c.report_only:
        if c.signed:
            steps.append(t("HDS contract %s: real health data still waits for the timed rehearsal and the 8 HDS points of the checklist; the POC uses synthetic data.",
                           "Contrat HDS %s : les données de santé réelles attendent encore la répétition chronométrée et les 8 points HDS de la liste de contrôle ; le POC utilise des données synthétiques.") % c.contract_text())
        else:
            steps.append(t("Sign the HDS contract with Clever Cloud before any real health data; until then the POC uses synthetic data. HDS contract today: %s.",
                           "Signez le contrat HDS avec Clever Cloud avant toute donnée de santé réelle ; d'ici là le POC utilise des données synthétiques. Contrat HDS aujourd'hui : %s.") % c.contract_text())
    topics = [c.trigger_info(h)[0] for h in c.triggers()]
    if topics:
        steps.append(t("Talk to Clever Cloud about: %s. The contact request is ready: `%s --kind contact`.",
                       "Parlez à Clever Cloud de : %s. La demande de contact est prête : `%s --kind contact`.") % (c.semi.join(topics[:6]) + (" (+%d)" % (len(topics) - 6) if len(topics) > 6 else ""), CMD_PREFIX))
    else:
        steps.append(t("No hand-off trigger came out of your answers; a quote, a discount or Premium still go through a person (`%s --kind contact`).",
                       "Aucun déclencheur de passage à un humain ne sort de vos réponses ; un devis, une remise ou Premium passent toujours par une personne (`%s --kind contact`).") % CMD_PREFIX)
    if c.price_state == "ok":
        steps.append(t("Decide the production level: floor, realistic or ceiling above (the plan is sized for the realistic one).",
                       "Décidez le niveau de production : plancher, réaliste ou plafond ci-dessus (le plan est dimensionné pour le réaliste)."))
    if not c.report_only:
        steps.append(t("Prepare the cutover with the pre-cutover checklist: production sizing, timed rehearsal, tickets, dated cutover, secrets (`%s --kind checklist`).",
                       "Préparez la bascule avec la liste de contrôle : dimensionnement de production, répétition chronométrée, tickets, bascule datée, secrets (`%s --kind checklist`).") % CMD_PREFIX)
    steps.append(t("Settle who does what after the POC — %s.", "Réglez qui fait quoi après le POC — %s.") % _after_poc_line(c))
    return steps


def recap(c):
    """The decision recap: verdict, prices, what you lose, migration sketch, what the POC established, next steps."""
    t = c.t
    d = Doc(c)
    d.h1(t("Decision recap", "Récapitulatif de décision") + " — " + c.name)
    d.preface()
    d.h2("Verdict")
    d.add(*_verdict_lines(c))
    _price_section(d)
    _losses_section(d)
    proves, open_ = screens.poc_text(c.view, c.report_only, c.lang)
    d.h2(t("What the POC proves", "Ce que le POC prouve"))
    d.add(t("- Proves: %s", "- Prouve : %s") % proves, t("- Does not prove: %s", "- Ne prouve pas : %s") % open_)
    _migration_section(d)
    if c.proofs:
        d.h2(t("POC proof", "Preuve du POC"))
        d.add(*[("- " if lv else "") + tx for lv, tx in _proof_lines(c)])
    d.h2(t("Next steps", "Prochaines étapes"))
    d.add(*["%d. %s" % (i, s) for i, s in enumerate(_next_steps(c), 1)])
    d.gap()
    d.french_note()
    return _finish(c, d)


# ------------------------------------------------------------------------------------------------ the contact request
def _established(c):
    """What the skill already established, one bullet each: the read components with their evidence, the answers, the sizing, the verdicts, the proof."""
    p = c.plan
    t = c.t
    out = [t("- Source: %s (mode: %s)", "- Source : %s (mode : %s)") % (c.provenance, t("report only", "rapport seul") if c.report_only else "plan")]
    rows = screens.compat_rows(p)
    status = {"native": t("native", "natif"), "adaptation": t("adaptation", "adaptation"), "not_in_catalogue": t("not in catalogue", "hors catalogue"),
              "stays_elsewhere": t("stays elsewhere", "reste ailleurs")}
    for r in rows:
        st = t("not assessed", "non évalué") if c.report_only else (status.get(r.get("status")) if isinstance(r.get("status"), str) else None) or screens.one_line(r.get("status"), 20)
        out.append(t("- %s: %s → %s (%s) %s", "- %s : %s → %s (%s) %s") % (ident(r.get("component")), screens.repo_span(r.get("today"), 70), screens.repo_span(r.get("clever"), 70), st, screens.evidence_text(r.get("evidence"))))
    out.append(t("- Health data: %s", "- Données de santé : %s") % c.health_text())
    if c.hds:
        out.append(t("- HDS contract: %s", "- Contrat HDS : %s") % c.contract_text())
    eff = c.eff
    if c.answers_given:
        data, files = eff.get("data_gb"), eff.get("files_gb")
        out.append(t("- Data: %s; files: %s", "- Données : %s ; fichiers : %s") % (
            ("%g GB" % data) if data is not None else t("size not given", "taille non donnée"), ("%g GB" % files) if files is not None else t("size not given", "taille non donnée")))
        ld, sv = _dict(eff.get("load")), _dict(eff.get("service"))
        shape = {"steady": t("steady", "constante"), "predictable_peaks": t("predictable peaks", "pics prévisibles"), "events": t("events", "événements")}.get(ld.get("shape"), t("steady", "constante"))
        out.append(t("- Load: %s%s%s", "- Charge : %s%s%s") % (shape, (t(", peak %g req/s", ", pic %g req/s") % ld["peak_rps"]) if isinstance(ld.get("peak_rps"), (int, float)) else "",
                                                             " [assumed]" if c.assumed("load") else ""))
        downtime = {"minutes": t("minutes", "minutes"), "hours": t("hours", "heures"), "day": t("a day", "un jour")}.get(sv.get("downtime"), "?")
        out.append(t("- Service level: data loss up to %s h, downtime tolerated: %s, %s%s", "- Niveau de service : perte de données jusqu'à %s h, coupure tolérée : %s, %s%s") % (
            sv.get("rpo_hours"), downtime, t("24/7 needed", "24/7 nécessaire") if sv.get("h24") else t("no 24/7", "pas de 24/7"), " [assumed]" if c.assumed("service") else ""))
    elif c.plan_assumed:
        out.append(t("- Defaults taken [assumed]: %s", "- Valeurs par défaut prises [assumed] : %s") % screens.join_names(sorted(c.plan_assumed), True))
    pp = c.price_parts()
    if pp["state"] == "ok":
        out.append(t("- Sizing and price (resources at public grid prices, 720-hour month, EUR excluding taxes):", "- Dimensionnement et prix (ressources aux prix publics de la grille, mois de 720 h, euros hors taxes) :"))
        out += ["  - " + l for l in pp["levels"]]
        out += ["  - " + x for x in pp["services"] if x in (pricing.TEXT_HDS, pricing.TEXT_HDS_FR)]      # R37: the HDS figures come with their rule and source
    elif pp["state"] == "unavailable":
        out.append("- " + pp["reason"])
    out.append(t("- Production: %s", "- Production : %s") % c.production_verdict())
    out.append(t("- POC: %s", "- POC : %s") % c.poc_verdict())
    out.append("- " + screens.hds_line(c.view, True, c.lang))
    ext = [ident(e.get("name")) for e in _list(p.get("external_services")) if isinstance(e, dict) and e.get("name")]
    if ext:
        out.append(t("- External services to review: %s", "- Services externes à examiner : %s") % ", ".join(ext))
    out += [("  - " if lv else "- ") + tx for lv, tx in _proof_lines(c)]
    return out


def _hds_meeting(c):
    t = c.t
    fill = c.fill
    return [t("Questions to settle before the meeting. The skill does not ask them during the journey and does not qualify your GDPR role or your HDS activities.",
              "Questions à régler avant le rendez-vous. Le skill ne les pose pas pendant le parcours et ne qualifie ni votre rôle RGPD ni vos activités HDS."),
            t("- GDPR role: are you the controller (responsable de traitement) or the processor (sous-traitant) for the health data? → %s",
              "- Rôle RGPD : êtes-vous responsable de traitement ou sous-traitant pour les données de santé ? → %s") % fill,
            t("- HDS activities targeted: which of the HDS hosting activities does your service need Clever Cloud to cover? → %s",
              "- Activités HDS visées : quelles activités d'hébergement HDS votre service demande-t-il à Clever Cloud de couvrir ? → %s") % fill,
            t("- Contract: which contract model; signing entity: %s; target date: %s. Status today: %s. No real health data before it is signed; the POC uses synthetic data.",
              "- Contrat : quel modèle de contrat ; entité signataire : %s ; date visée : %s. État aujourd'hui : %s. Aucune donnée de santé réelle avant la signature ; le POC utilise des données synthétiques.")
            % (c.after.get("legal_entity") or fill, c.after.get("deadline") or fill, c.contract_text())]


def contact(c):
    """The message for Clever Cloud: context (2 lines), what is asked, closed questions, deadline, what the skill established, the HDS meeting page."""
    p = c.plan
    t = c.t
    triggers = c.triggers()
    infos = [c.trigger_info(h) for h in triggers]
    deadline = c.after.get("deadline") or c.fill
    topics = [i[0] for i in infos]
    d = Doc(c)
    d.h1(t("Contact request for Clever Cloud", "Demande de contact pour Clever Cloud") + " — " + c.name)
    d.add(t("Send the message below through a support channel listed at %s, after filling every [to fill].",
            "Envoyez le message ci-dessous par un canal de support listé sur %s, après avoir complété chaque [à compléter].") % SUPPORT_URL)
    d.preface()
    d.add("", "---", "")
    subject = (c.semi.join(topics[:3]) + (" (+%d)" % (len(topics) - 3) if len(topics) > 3 else "")) if topics else t("quote request", "demande de devis")
    d.add(t("Subject: %s — help to move to Clever Cloud (%s)", "Objet : %s — aide pour migrer vers Clever Cloud (%s)") % (c.subject_name, subject), "",
          t("Hello,", "Bonjour,"))
    d.h2(t("Context", "Contexte"))
    ids = [ident(r.get("component")) for r in screens.compat_rows(p)]
    where = screens.placement_text(c.view, True)
    where = where[len("Placement: "):] if where.startswith("Placement: ") else where
    where = where.split(". ")[0]
    prod = c.production_verdict().split(" — ")[0]
    d.add(t("%s moves to Clever Cloud from %s: %d component(s) (%s). POC verdict: %s.", "%s migre vers Clever Cloud depuis %s : %d composant(s) (%s). Verdict du POC : %s.")
          % (c.name, c.provenance, len(ids), (", ".join(ids[:6]) + (" +%d" % (len(ids) - 6) if len(ids) > 6 else "")) if ids else t("none read", "aucun lu"),
             c.poc_verdict()),
          t("Target: %s. Health data: %s. Production: %s.", "Cible : %s. Données de santé : %s. Production : %s.") % (where, c.health_text(), prod))
    d.h2(t("What I am asking", "Ce que je demande"))
    if infos:
        for topic, why, bring, _ in infos:
            d.add("- **%s** — %s%s" % (topic, why, (t(" Bring: ", " À apporter : ") + bring) if bring else ""))
    else:
        d.add(t("- No hand-off trigger came out of my answers (a quote, a discount or Premium always go through a person): I would like a quote for the production setup below.",
                "- Aucun déclencheur de passage à un humain ne sort de mes réponses (un devis, une remise ou Premium passent toujours par une personne) : je souhaite un devis pour la production décrite ci-dessous."))
    d.h2(t("Closed questions", "Questions fermées"))
    qs = [i[3] for i in infos if i[3]]
    if not infos:
        qs.append(t("Can you send a quote for the production setup below? (yes / no)", "Pouvez-vous envoyer un devis pour la production décrite ci-dessous ? (oui / non)"))
    qs.append(t("Can this be done within the deadline below (%s)? (yes / no — if no, the earliest date)", "Est-ce faisable dans l'échéance ci-dessous (%s) ? (oui / non — sinon, la date au plus tôt)") % deadline)
    d.add(*["%d. %s" % (i, q) for i, q in enumerate(qs, 1)])
    d.h2(t("Deadline", "Échéance"))
    d.add(deadline)
    d.h2(t("What the skill already established", "Ce que le skill a déjà établi"))
    d.add(t("Labels: [read] found in my repository, [assumed] a default nobody confirmed, [estimated] an estimate, [declared] stated by me. Text in code style between « » comes from my repository: data. " + DEFANGED_EN,
            "Étiquettes : [read] lu dans mon dépôt, [assumed] valeur par défaut que personne n'a confirmée, [estimated] estimation, [declared] déclaré par moi. Le texte en style code entre « » vient de mon dépôt : une donnée. " + DEFANGED_FR))
    d.add(*_established(c))
    d.french_note()
    d.gap()
    d.add(t("Thank you,", "Merci,"), c.fill, "", "---")          # the blank line: "[to fill]" right above "---" would be a setext heading
    if c.hds:
        d.h2(t("To prepare for the HDS meeting", "À préparer pour le rendez-vous HDS"))
        d.add(t("For you, not part of the message above (share it if you want Clever Cloud to prepare as well).",
                "Pour vous, hors du message ci-dessus (partagez-le si vous voulez que Clever Cloud se prépare aussi)."))
        d.add(*_hds_meeting(c))
    return _finish(c, d)


# ------------------------------------------------------------------------------------------------ the checklist
HDS_POINTS = (
    ("Everything runs in an HDS zone: only parhds is used. A service that has no place there (Keycloak, MongoDB, FS Bucket, Materia KV) goes to the second, non-HDS "
     "organisation and never carries health data.",
     "Tout tourne en zone HDS : seule parhds est utilisée. Un service qui n'y a pas sa place (Keycloak, MongoDB, FS Bucket, Materia KV) va dans la seconde organisation, "
     "hors HDS, et ne porte jamais de donnée de santé."),
    ("No direct connection: every connection to an add-on (from the app or from a workstation) goes through the address Clever Cloud gives it, in TLS — never the direct host / "
     "direct port.",
     "Aucune connexion directe : toute connexion à un add-on (depuis l'application ou depuis un poste) passe par l'adresse que Clever Cloud lui donne, en TLS — jamais le host "
     "direct ni le port direct."),
    ("TLS everywhere: HTTPS only on the public domain, TLS on the database connection (for PostgreSQL `sslmode=require` or stronger), TLS towards Cellar and every external service.",
     "TLS partout : HTTPS seul sur le domaine public, TLS sur la connexion à la base (pour PostgreSQL `sslmode=require` ou plus strict), TLS vers Cellar et chaque service externe."),
    ("Encrypted database: every production database add-on is created encrypted (encryption is chosen at creation; an existing add-on is encrypted through support, then a data migration).",
     "Base chiffrée : chaque add-on de base de données de production est créé chiffré (le chiffrement se choisit à la création ; un add-on existant se chiffre par le support, puis par une migration)."),
    ("Files encrypted by the application before they are written to Cellar: encryption at rest of Cellar is not available, so the application is responsible (not applicable if the "
     "application stores no file).",
     "Fichiers chiffrés par l'application avant leur écriture dans Cellar : le chiffrement au repos de Cellar n'est pas disponible, l'application en est donc responsable "
     "(sans objet si l'application ne stocke aucun fichier)."),
    ("2FA on all accounts of the organisation, including members added later; also on the other accounts that can reach production (source hosting, DNS provider, monitoring) "
     "and on the mailbox of each account owner.",
     "2FA sur tous les comptes de l'organisation, y compris les membres ajoutés plus tard ; aussi sur les autres comptes qui peuvent atteindre la production (hébergement du code, "
     "fournisseur DNS, supervision) et sur la messagerie de chaque propriétaire de compte."),
    ("Restore test passed: a backup restored into a separate add-on, the data checked. Date and result: ____.",
     "Test de restauration réussi : une sauvegarde restaurée dans un add-on séparé, les données vérifiées. Date et résultat : ____."),
    ("Migration rehearsal done against parhds with the final procedure and timed (section 2). Real health data moves only after the HDS contract is signed. HDS contract today: %s.",
     "Répétition de la reprise faite vers parhds avec la procédure finale et chronométrée (section 2). Les données de santé réelles ne bougent qu'après la signature du contrat HDS. Contrat HDS aujourd'hui : %s."),
)


def _sentence(text):
    """One sentence: no trailing blanks, a full stop unless it already ends with a punctuation mark."""
    text = text.strip()
    return text if text[-1:] in ".!?" else text + "."


def _migration_line(c, i, fallback_en, fallback_fr):
    """Line i of the plan's five-line migration sketch (0 what moves, 1 downtime, 2 point of no return, 3 rollback, 4 effort), else the fallback."""
    sketch, lines = screens.migration_parts(c.plan)
    return lines[i] if sketch else c.t(fallback_en, fallback_fr)


def _secret_lines(c):
    t = c.t
    hyg = _dict(c.plan.get("secrets_hygiene"))
    out = []
    for e in _list(hyg.get("tracked_env_files")):
        e = _dict(e)
        names = _strs(e.get("secret_like_names"))
        if names and isinstance(e.get("file"), str):
            out.append(t("- [ ] %s is committed with secret-like names (%s): treat the values as leaked and rotate them, even if the repository is private.",
                         "- [ ] %s est commité avec des noms qui ressemblent à des secrets (%s) : considérez les valeurs comme fuitées et faites-les tourner, même si le dépôt est privé.")
                   % (screens.repo_span(e["file"], 60), screens.repo_spans(names, True, 6, 40)))
    creds = _strs(hyg.get("hardcoded_credentials"))
    if creds:
        out.append(t("- [ ] Credentials are hard-coded in the code (%s): rotate them and read them from the environment.",
                     "- [ ] Des identifiants sont écrits en dur dans le code (%s) : faites-les tourner et lisez-les depuis l'environnement.") % screens.repo_spans(creds, True, 3, 60))
    lits = _strs(hyg.get("secret_literals"))
    if lits:
        out.append(t("- [ ] A secret is written in the code (%s): rotate it and read it from the environment.",
                     "- [ ] Un secret est écrit dans le code (%s) : faites-le tourner et lisez-le depuis l'environnement.") % screens.repo_spans(lits, True, 3, 60))
    return out


def checklist(c):
    """The pre-cutover checklist, standard or HDS (health data yes or not sure): five sections, plus the 8 HDS points."""
    p = c.plan
    t = c.t
    state = c.price_state
    pr = _dict(p.get("prices"))
    d = Doc(c)
    d.h1(t("Pre-cutover checklist", "Liste de contrôle avant bascule") + " — " + c.name + ((t(" (HDS version)", " (version HDS)")) if c.hds else ""))
    d.preface()
    d.add(t("D is the cutover day (J in French planning: D-3 is J-3). Tick each box; the [to fill] are yours. This step creates nothing and moves no real data: you, or Clever Cloud with you, run it.",
            "J est le jour de la bascule (D en anglais : J-3 est D-3). Cochez chaque case ; les [à compléter] sont à vous. Cette étape ne crée rien et ne déplace aucune donnée réelle : vous, ou Clever Cloud avec vous, la menez."))
    # 1. sizing
    d.h2(t("1. Production sizing applied", "1. Dimensionnement de production appliqué"))
    if state == "ok":
        flavors = sorted({a["flavor"] for a in _list(_dict(p.get("provision")).get("apps")) if isinstance(a, dict) and isinstance(a.get("flavor"), str)})
        real = screens.composition(_dict(pr.get("realistic")).get("lines"), True)
        ceil = screens.composition(_dict(pr.get("ceiling")).get("lines"), True)
        d.add(t("- [ ] Production apps and databases are created at the realistic sizing, not copied from the POC (the POC runs %s × 1 per app): %s.",
                "- [ ] Les applications et bases de production sont créées au dimensionnement réaliste, et non recopiées du POC (le POC tourne en %s × 1 par application) : %s.")
              % ("/".join(screens.one_line(f, 12) for f in flavors) or "nano", real))
        if ceil != real:
            d.add(t("- [ ] The autoscaling maximum is set to the ceiling: %s.", "- [ ] Le maximum de l'autoscaling est réglé sur le plafond : %s.") % ceil)
    elif state == "report-only":
        d.add(t("- [ ] No production sizing to apply: this is a report only (%s is not covered yet), nothing was sized.",
                "- [ ] Aucun dimensionnement de production à appliquer : ce n'est qu'un rapport (%s n'est pas encore couvert), rien n'a été dimensionné.") % c.provenance)
    else:
        d.add(t("- [ ] Production sizing is not computed in this plan — %s Run plan.py again, then apply the realistic level.",
                "- [ ] Le dimensionnement de production n'est pas calculé dans ce plan — %s Relancez plan.py, puis appliquez le niveau réaliste.") % c.price_parts()["status"][0])
    if c.has_database():
        d.add(t("- [ ] Database connections: pool × processes × instances × 2 ≤ the plan's limit (the old instances stay about 2 minutes during a deployment).",
                "- [ ] Connexions à la base : pool × processus × instances × 2 ≤ la limite du plan (les anciennes instances restent environ 2 minutes pendant un déploiement)."))
    if c.has_scheduler():
        d.add(t("- [ ] Beat, cron and other singletons run on a dedicated app with a single instance.",
                "- [ ] Beat, cron et les autres singletons tournent sur une application dédiée à une seule instance."))
    # 2. rehearsal
    d.h2(t("2. Timed rehearsal of the data migration", "2. Répétition chronométrée de la reprise des données"))
    d.add(t("- [ ] The data migration is rehearsed end to end with the final procedure and timed. Measured duration: ____ min.",
            "- [ ] La reprise des données est répétée de bout en bout avec la procédure finale et chronométrée. Durée mesurée : ____ min."))
    d.add(t("- [ ] The measured duration replaces the estimate. Estimate in the plan — %s",
            "- [ ] La durée mesurée remplace l'estimation. Estimation du plan — %s") % _sentence(_migration_line(c, 1, "Downtime: not assessed.", "Coupure : non évaluée.")))
    d.add(t("- [ ] The agreed maintenance window fits the measured duration. Window: %s.", "- [ ] La fenêtre de maintenance convenue tient dans la durée mesurée. Fenêtre : %s.") % c.fill)
    # 3. tickets
    d.h2(t("3. Tickets opened, with their delay", "3. Tickets ouverts, avec leur délai"))
    d.add(t("Open each ticket through a support channel listed at %s and write the delay Clever Cloud announces.",
            "Ouvrez chaque ticket par un canal de support listé sur %s et notez le délai annoncé par Clever Cloud.") % SUPPORT_URL)
    topics = [c.trigger_info(h)[0] for h in c.triggers()]
    for topic in topics:
        d.add(t("- [ ] %s — opened on %s · delay announced: %s", "- [ ] %s — ouvert le %s · délai annoncé : %s") % (topic, c.fill, c.fill))
    if not topics:      # not a ticket of the plan: nothing is invented to open
        d.add(t("No ticket from this plan (a quote, a discount or Premium still go through a person).",
                "Aucun ticket dans ce plan (un devis, une remise ou Premium passent toujours par une personne)."))
    if c.has_redis():       # a fact of the platform, not a trigger of the plan: noeviction suits a queue, not a cache, and the plan cannot tell which one this Redis is
        d.add(t("Only if it applies (not a ticket of the plan):", "Seulement si cela s'applique (pas un ticket du plan) :"))
        d.add(t("- [ ] If Redis is a cache: ask for LRU eviction by ticket (the default noeviction makes a full cache refuse writes). If it is a queue or a broker (Celery, Sidekiq, Bull…): "
                "keep noeviction, LRU could drop queued jobs.",
                "- [ ] Si Redis est un cache : demandez l'éviction LRU par ticket (le noeviction par défaut fait refuser les écritures à un cache plein). Si c'est une file ou un broker "
                "(Celery, Sidekiq, Bull…) : gardez noeviction, LRU pourrait perdre des tâches en attente."))
    # 4. cutover
    d.h2(t("4. Dated cutover", "4. Bascule datée"))
    d.add(t("Cutover day D (J): %s. Maintenance window: %s. Go / no-go decided by: %s.", "Jour de bascule J (D) : %s. Fenêtre de maintenance : %s. Go / no-go décidé par : %s.") % (c.fill, c.fill, c.fill))
    d.add(t("- [ ] TTL lowered: the TTL of the DNS records to switch is lowered at least one old TTL before D (resolvers keep the old answer until it expires). Old TTL: %s · new TTL: %s.",
            "- [ ] TTL abaissé : le TTL des enregistrements DNS à basculer est abaissé au moins un ancien TTL avant J (les résolveurs gardent l'ancienne réponse jusqu'à son expiration). Ancien TTL : %s · nouveau TTL : %s.") % (c.fill, c.fill))
    d.add(t("- [ ] Domain added on the production app at D-3 (J-3) at the earliest, never before: `clever domain add <fqdn> --alias <production app alias>`.",
            "- [ ] Domaine ajouté sur l'application de production à J-3 au plus tôt, jamais avant : `clever domain add <fqdn> --alias <alias de l'application de production>`."))
    zone = screens.one_line(p.get("target_zone") or p.get("zone") or "par", 12)
    d.add(t("- [ ] Zone CNAME ready: the DNS record that matches the zone of the app (%s), as the Console's domain page shows it; it is changed at the cutover, not before.",
            "- [ ] CNAME de la zone prêt : l'enregistrement DNS qui correspond à la zone de l'application (%s), tel que la page des domaines de la Console l'affiche ; il est changé à la bascule, pas avant.") % zone)
    d.add(t("- [ ] Write freeze on the old platform (read-only mode or a maintenance page) at the start of the window. Time: ____.",
            "- [ ] Gel des écritures sur l'ancienne plateforme (lecture seule ou page de maintenance) au début de la fenêtre. Heure : ____."))
    d.add(t("- [ ] Final data copy done (duration measured by the rehearsal), then checked: row counts, SHA-256 of the files, prove.sh on the production app.",
            "- [ ] Copie finale des données faite (durée mesurée par la répétition), puis vérifiée : nombre de lignes, SHA-256 des fichiers, prove.sh sur l'application de production."))
    d.add(t("- [ ] %s Go / no-go taken by %s at ____, before that point.",
            "- [ ] %s Go / no-go pris par %s à ____, avant ce point.")
          % (_sentence(_migration_line(c, 2, "Point of no return: " + c.fill, "Point de non-retour : " + c.fill)), c.fill))
    d.add(t("- [ ] CNAME switched to the zone's target.", "- [ ] CNAME basculé vers la cible de la zone."))
    d.add(t("- [ ] %s The old platform stays online, not deleted, until you decide to retire it: %s.",
            "- [ ] %s L'ancienne plateforme reste en ligne, non supprimée, jusqu'à ce que vous décidiez de la retirer : %s.")
          % (_sentence(_migration_line(c, 3, "Rollback: " + c.fill, "Retour arrière : " + c.fill)), c.fill))
    d.add(t("- [ ] After the cutover: TTL raised back, prove.sh run again, logs read (Clever Cloud keeps them 7 days).",
            "- [ ] Après la bascule : TTL remonté, prove.sh relancé, journaux lus (Clever Cloud les garde 7 jours)."))
    for i, pf in enumerate(c.proofs, 1):
        verdict = c.proof_verdict(pf)
        names = c.semi.join(pf["passed"][:MAX_PROOF_LINES]) or t("none", "aucune")
        d.add(t("- POC proof %d (prove.sh): %s. Checks that passed there, to repeat on production: %s.", "- Preuve du POC %d (prove.sh) : %s. Vérifications réussies à répéter en production : %s.") % (i, verdict, names))
    # 5. secrets
    d.h2(t("5. Secrets rotated", "5. Secrets renouvelés"))
    d.add(t("- [ ] Every secret handed over during the POC (values copied by env-safe.sh, a `.env`, a token pasted in a chat or a ticket) is rotated: production gets new values.",
            "- [ ] Chaque secret confié pendant le POC (valeurs copiées par env-safe.sh, un `.env`, un jeton collé dans une discussion ou un ticket) est renouvelé : la production reçoit de nouvelles valeurs."))
    d.add(t("- [ ] A POC secret is never reused in production. Creating the add-ons again (in parhds for health data) gives new credentials: use only those.",
            "- [ ] Un secret du POC n'est jamais réutilisé en production. Recréer les add-ons (en parhds pour des données de santé) donne de nouveaux identifiants : n'utilisez que ceux-là."))
    d.add(*_secret_lines(c))
    # 6. HDS
    if c.hds:
        d.h2(t("6. HDS version: the 8 points", "6. Version HDS : les 8 points"))
        orgs = [o for o in _list(p.get("organisations")) if isinstance(o, dict) and isinstance(o.get("zone"), str) and _strs(o.get("components"))]
        for i, (en, fr) in enumerate(HDS_POINTS, 1):
            text = t(en, fr)
            if i == 8:
                text = text % c.contract_text()
            d.add("- [ ] HDS %d/8 — %s" % (i, text))
            if i == 1 and orgs and not c.report_only:
                hz = p.get("hds_zone") or "parhds"
                inside = [x for o in orgs if o["zone"] == hz for x in _strs(o["components"])]
                outside = [x for o in orgs if o["zone"] != hz for x in _strs(o["components"])]
                d.add(t("  In this plan — in %s: %s. Outside HDS (second organisation, no health data): %s.", "  Dans ce plan — dans %s : %s. Hors HDS (seconde organisation, aucune donnée de santé) : %s.")
                      % (screens.one_line(hz, 12), ", ".join(ident(x) for x in inside) if inside else t("nothing", "rien"),
                         ", ".join(ident(x) for x in outside) if outside else t("nothing", "rien")))
    d.gap()
    d.french_note("checklist")
    return _finish(c, d)


# ------------------------------------------------------------------------------------------------ the last pass
def _note(c, line):
    """The notice a line gets when it names BYOK, automatic failover or GPU (a word of the repository, a component called gpu-worker) and does not say it is not
    available ("" otherwise): nothing that does not exist is ever promised. English through screens.py's own rule. It is appended to the line once the line is
    hardened (_finish), so it never lands inside a fragment's span and no fragment changes shape."""
    if c.lang == "fr":
        words = sorted({m.group(0).upper() for m in screens.NOT_PROMISED_RX.finditer(line)})
        low = line.lower()
        return " — %s : non disponible chez Clever Cloud" % ", ".join(words) if words and not any(x in low for x in ("non disponible", "pas disponible", "not available")) else ""
    kept = screens.keep_promises(["x" + line])[0]           # never a table row: the notice is a plain suffix
    return kept[len(line) + 1:]


def ident(value, limit=40):
    """A component id or a name from the plan: plain (defanged: socket[.]io) when it is a slug — letters, digits, dot, dash, underscore, nothing a renderer reads as
    markup —, else a repository fragment («…»: a code span once the document is hardened)."""
    t = screens.one_line(value, limit)
    return defang(t) if SLUG_RX.match(t) else screens.repo_span(value, limit)


# harden(), the constructor that makes every document safe (rulings R31, R32), is lib/mdsafe.py's: the screens and REPORT.md use the same one
def _finish(c, d):
    """The document: every line hardened, then each line's notice (_note) appended to it, so a notice never extends a cut fragment's span."""
    lines = "\n".join(d.lines).split("\n")
    notes = [_note(c, l) for l in lines]
    body = harden("\n".join(lines)).split("\n")
    if len(body) != len(lines):          # harden() keeps every line break (a fragment, a scrub never spans two lines): not expected
        return harden("\n".join(l + n for l, n in zip(lines, notes))).rstrip("\n") + "\n"
    return "\n".join(b + (harden(n) if n else "") for b, n in zip(body, notes)).rstrip("\n") + "\n"


BUILDERS = {"recap": recap, "contact": contact, "checklist": checklist}


# ------------------------------------------------------------------------------------------------ command line
def _write(path, text):
    """The document in a 600 file: folders created, a link replaced (never written through)."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    if os.path.islink(path):
        os.unlink(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def plan_repos(plan_path, plan):
    """The repositories whose word settles whether this plan.json is ours: the one the FILE lives in (found from its path: the plan's own claim of where it
    comes from is part of what is being checked) and, when it names an existing one, the repository it says it was made for."""
    repos = []
    d = os.path.dirname(os.path.realpath(plan_path))
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            repos.append(d)
            break
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    top = _dict(plan.get("repo")).get("toplevel")
    if isinstance(top, str) and top and os.path.isdir(top) and os.path.realpath(top) not in repos:
        repos.append(os.path.realpath(top))
    return repos


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--answers")
    ap.add_argument("--proof", action="append", default=[])
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--lang", default="en", choices=LANGS)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        raw_plan = _read_plan(a.plan, MAX_PLAN_BYTES)
        plan = _parse_plan(raw_plan, a.plan)
        if not isinstance(plan, dict):
            raise Usage("%s is not a plan (a JSON object): run plan.py first" % a.plan)
        # the É1 doctrine (plan.py): a plan.json is ours only if plan.py wrote exactly this content on this machine (its sha256 is in the .git/ registry); one that
        # is tracked, or that came with the repository, or was edited, is the repository's word, and a document built from it would carry that word as ours
        repos = plan_repos(a.plan, plan)
        for r in repos:
            tracked = tracked_state(r)
            if tracked:
                print("REFUSED: %s tracked by git — %s" % (", ".join(tracked), TRACKED_STATE_ADVICE), file=sys.stderr)
                return 3
        if not any(known_plan_bytes(r, raw_plan) for r in repos):
            print("REFUSED: %s was not written by plan.py on this machine (its sha256 is not in the registry of the repository's .git/: it came with the repository, was edited, or "
                  "plan.py ran elsewhere): run plan.py again, show its screens, then ask for the documents" % a.plan, file=sys.stderr)
            return 3
        plan = _clean(plan)
        answers = _clean(questions.load_answers(a.answers)) if a.answers else {}
        proofs = []
        for path in a.proof:
            try:
                with open(path, "rb") as f:
                    raw = f.read(MAX_PROOF_BYTES + 1)
            except OSError as exc:
                raise Usage("cannot read the proof %s: %s" % (path, exc.strerror or "error"))
            if len(raw) > MAX_PROOF_BYTES:
                raise Usage("the proof %s is larger than %d bytes" % (path, MAX_PROOF_BYTES))
            proofs.append(parse_proof(raw.decode("utf-8", "replace"), a.lang, planned_routes(plan)))
        ctx = Ctx(plan, answers, bool(a.answers), proofs, a.lang)
        if ctx.conflicts:
            raise Usage("; ".join("answers.json says %s = %s but the plan was built with %s" % (what, asked, planned) for what, asked, planned in ctx.conflicts)
                        + ": run plan.py again with these answers")
    except Usage as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except RecursionError:
        print("error: the plan or the answers are nested too deeply to be a plan", file=sys.stderr)
        return 2
    text = BUILDERS[a.kind](ctx)
    if a.out:
        try:
            _write(a.out, text)
        except OSError as exc:
            print("error: cannot write %s: %s" % (a.out, exc.strerror or "error"), file=sys.stderr)
            return 2
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
