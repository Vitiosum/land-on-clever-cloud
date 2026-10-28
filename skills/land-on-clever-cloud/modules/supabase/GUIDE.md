# Supabase module — migrating a Supabase app to Clever Cloud

This guide is loaded by the root skill (`<skill>/SKILL.md`) only when the provenance is
Supabase. Until the developer's decision (the root journey, steps 0 to 5: silent read, the
screens, the questions, the go) it adds nothing of its own. The plan picks a level: **A** = the
app moves and Supabase stays (handled by the root journey and `<skill>/reference/supabase.md`);
**B** (data moves) and **C** (full exit) are deeper migrations, and after the go this guide
drives them: survey, decisions, cutover plan, target, HDS-ready architecture, scalability, and a
replayable acceptance that proves the migration is faithful.

## The rule that overrides everything else

> **Nothing is inferred, everything is measured.**

This is measured: on a real migration, 73 % of the risks drawn from a static analysis of the
repository were refuted, and the decisive findings only came out of a query, a `curl` or a
measurement (`modules/supabase/reference/survey.md` §1).

**No migration plan before the phase 1 survey has run.** A plan written from reading the
repository is three-quarters wrong, and convincingly wrong.

## What you produce, in order

**In a POC run** (the root skill's journey, any level), produce only `survey.json`, the
deployed target and the proofs the scripts write (manifests of `migrate-storage.py`,
`migrate-accounts.py`, outputs of `check-http.py` / `check-files.py`). The four documents
below (`decisions.md`, `cutover.md`, `acceptance.md`, `gates.md`) belong to a **full migration
engagement**: write them only when the developer asks for that engagement, and keep each one
short (one line per decision, per step, per cell).

1. `survey.json` — the measured inventory. Numbers, not prose.
2. `decisions.md` — one decision line per component.
3. `cutover.md` — the runbook, durations extrapolated from the survey.
4. The deployed target.
5. `acceptance.md` — the before/after matrix, the fingerprints, the SHA-256 proof of the
   files.

Throughout: `gates.md` (template `modules/supabase/templates/gates.md`): every gate decision,
every assumption taken for lack of an answer.

---

## Phase 0 — Framing: the common journey

### Before the go: the root skill's questions, nothing else

The questions of the root journey replace this module's own framing questions: screen 2
(`<skill>/scripts/questions.py`: health data and the HDS contract, what really runs in
production with its real sizes, the load, the service level), then the compatibility, price and
migration-sketch screens, then the go. Their answers (`.clever-landing/answers.json`) fix the
track **for the whole engagement**: zone, add-ons, scripts.

| | **Non-HDS** track | **HDS** track |
|---|---|---|
| Health data (answers.json) | no | yes or not sure |
| Zone | **`par`** (Paris) | **`parhds`** in production; the POC in `par` with synthetic data until the HDS contract is declared signed, then `parhds` |
| Another HDS zone | — | never proposed by the skill; a client who needs one changes it himself |
| Add-ons | the whole catalogue of the zone | **only those of `parhds`**; what needs no HDS goes to a second, non-HDS organisation |
| `modules/supabase/reference/hds.md` | **ignore it** | catalogue from the decisions phase, the rest before phase 4 |
| `deploy-target.sh` | `TRACK=non-hds` (zone `par`) | `TRACK=hds`: zone `par` by default (the POC, synthetic data, HDS-ready rules, a banner says so); `parhds` only with `HDS_CONTRACT=signed` (declared by the client); an HDS zone without it is refused |

**Paris first, on both tracks** (best performance, Cellar in particular).

**Non-HDS**: no HDS mention in the deliverables (no contract, no HDS zone, no `hds.md`).

**HDS: prerequisites to show the client as they are, before any technical proposal.**

1. **HDS contract signed with Clever Cloud**: *"HDS zone + signed HDS contract"*.
   Never say *"deployed in an HDS region, therefore compliant"*.
   The skill writes "HDS-ready architecture: yes" or "… missing <x>".
2. **One HDS zone, `parhds`**, the same for every resource (app, database, storage).
3. **Dedicated plans** for the data (never `dev`), **encryption at rest enabled at creation**.
4. **The client's responsibility**: application code, application identities and permissions,
   named access traceability, any application-level encryption, the GDPR obligations of the
   processing (legal basis, retention periods, DPIA, data subjects' rights). These legal points
   are never asked during the journey: they go to "To prepare for the HDS meeting" of the
   contact request (`<skill>/scripts/deliverables.py --kind contact`).

**Keycloak**: possible in HDS but billed extra, so **never recommended there**: it goes to a
second, non-HDS organisation (below). **Never in an HDS zone**: MongoDB, Matomo, FS Bucket,
Materia KV, Mailpace, Azimutt, Clever Kubernetes (CKE). Only offer
what `parhds` provides (`modules/supabase/reference/hds.md` §The add-on catalogue has gaps, to
be re-checked before each engagement). Available does not mean certified: the scope is
confirmed in the contract. Authentication of health users happens in the application (path 1:
accounts and hashes migrated). A service that needs no HDS (Keycloak, MailPace, Matomo,
preproduction, tools) goes, by default, to a **second, non-HDS organisation**, with its boundary
documented (`<skill>/reference/zones.md` §Two organisations); the client's DPO checks it.

### Say it at framing: private files need the secret key

A **private** bucket's bytes are not in the database, and reading them without a user session is
only possible with the project's **secret key** (`sb_secret_…`, or the legacy `service_role`):
a login to the source WRITES (sessions, `last_sign_in_at`). So, from the first exchange:
**"files: blocked until the secret key is provided"** — a blocking point of the plan
(`plan.py --level C` says it, and lifts it with `--storage-key-file <file>`), never an effort
"none". The developer provides it; the agent never extracts it with a CLI:

```bash
# the developer copies the key from the dashboard (Settings > API keys), then, OUTSIDE the repository:
(umask 077; pbpaste > ~/sb-secret-key) && pbcopy < /dev/null      # 600 from creation, clipboard cleared
python3 -I modules/supabase/scripts/migrate-storage.py objects.json --prefix <p>- --dry-run \
  --key-file ~/sb-secret-key        # tests the read route on one object (byte 0), writes nothing
```

The key is only sent as `apikey` on `GET` (read-only), over https, to `<ref>.supabase.co` (or the
exact host given with `--source-host`); a key file inside the repository is refused, even untracked;
the file is deleted after the migration. The agent never reads it: no CLI that prints it, no page,
screenshot or DOM where it is revealed — the developer copies it themselves.

**State right now what is out of your reach**
(`modules/supabase/reference/survey-without-access.md` §What stays out of reach): discovered in
phase 5, it blocks the data migration.

### After the POC: two questions of this module

With the root journey's after-POC question (`questions.py --after-poc`: who operates it, the DNS,
the deadline, the legal entity), ask, and log in `gates.md`:

- **Who operates after the cutover?** A client with no on-call must not inherit four containers
  to maintain.
- **The tolerated cutover window**, in minutes: a cold cutover or logical replication (phase 3).

The **Auth settings** (email confirmation, open or closed sign-up, the client's SMTP, OAuth
providers) are read by the survey, not asked (readable without a secret key:
`modules/supabase/reference/survey.md` §2). A sign-up with email confirmation cannot move
without SMTP: to be decided at gate 1.

### No answer from the client: autonomous agent mode

The root journey's rule: an unanswered question stays `[assumed]` (HDS at the slightest doubt
about the data), and nobody to ask means report only, unless a go given in advance names a plan
and a price ceiling the dry-run price stays under. Once a go exists, do not block at the
gates: take the most cautious assumption (cold cutover, survey volume **flagged as not
representative**), decide the recommended option and **log** it in `gates.md` with the time and
what it commits, to be confirmed.

---

## Phase 1 — Survey

`modules/supabase/scripts/survey-supabase.sh` produces `survey.json`: it measures read-only,
without concluding anything. Three paths, in order of preference:

1. **psql**: the Session pooler URL in a 600 file **outside the repository**, written by the
   developer — `(umask 077; pbpaste > ~/sb-db-url)` — and given as `SUPABASE_DB_URL_FILE=~/sb-db-url`
   (survey, measure-cost) or `--db-url-file ~/sb-db-url` (migrate-accounts). Never the URL itself on
   the agent's command line (transcript, `ps`); `SUPABASE_DB_URL=` is for a human's own shell.
2. **Supabase MCP connector**, with no password and no `service_role`: `--queries` prints a
   single block to pass to `execute_sql`; the agent writes its output to a file, which becomes
   `survey.json` through `--extract`, never retyped. Be frank: through MCP, **every row the
   query returns passes through the model's context** (and its transcript) — fine for counts,
   not for accounts. The bcrypt hashes go with `migrate-accounts.py` (psql source → target,
   below), never through MCP. No write (`apply_migration` excluded) —
   `modules/supabase/reference/survey-without-access.md` §The Supabase MCP connector.
3. **Neither**: HTTP probes and `supabase inspect db`, the rest `null`; never `db push` nor
   `config push`, which write to the source (`survey-without-access.md`).

Plus the HTTP probes with the publishable key (`modules/supabase/reference/survey.md` §2). Then
`modules/supabase/scripts/measure-cost.sh` times `pg_dump` without keeping the dumps; the
rebuild of vector indexes is measured **on the target** after the data migration
(`--target-index`).

The script passes the whole document through the shared masking helper
(`python3 -I "<skill>/scripts/lib/common.py" mask-json`) before it reaches `survey.json`.

**Storage**: `survey.json` counts buckets, objects, volume and active types, read from the
database, not from `config.toml`; the object list comes out separately (`--objects`), for the
data migration (`modules/supabase/reference/storage.md` §1).

**Before the decisions, read `modules/supabase/reference/survey.md` §3**: every measurement in
`survey.json` and the decision it drives.

**Three measurement traps** (figures: `modules/supabase/reference/survey.md` §4): extrapolate
from the size of the **dump**, never of the table (text doubles the size of vectors); a mass
`UPDATE` transiently costs **double**, and its `VACUUM FULL` rebuilds the indexes; the **audit
log** can weigh more than the data.

---

## Phase 2 — Decisions → GATE 1

### The constraint that decides before anything else

**The managed PostgreSQL add-on opens, by default, no superuser, no role creation and no
logical replication** (support studies roles, PITR, replicas and extensions case by case: a
delay and a written agreement, not a wall). Yet the self-hosted Supabase stack requires
dedicated roles (`modules/supabase/reference/clever-cloud.md` §0): **it is not redeployed as
is.** Three paths, in order of preference:

1. **Migrate the application, not Supabase**: a classic application backend on the add-on
   replaces PostgREST + GoTrue + Realtime. The simplest and cheapest to operate: very often the
   right answer. **Trap no. 1: `FORCE ROW LEVEL SECURITY`** (phase 5).
2. **PostgreSQL on CKE** if the stack must stay identical: **non-HDS only** (public beta, no HDS
   zone); managed service lost, to be announced as a beta.
3. **Dedicated add-on + support tickets**, nothing promised without a written answer.
   **In HDS, the only alternative to path 1.**

To decide **before** the table: it changes half of its lines.

### One line per component, four outcomes, in this order

1. **Do not move it** — the most profitable, the most forgotten: a component that is enabled
   but never called is removed (reference case: `pg_graphql` installed, no `/graphql/v1`
   call), and you write it down: a saving you can put a figure on.
2. **Managed add-on** (PostgreSQL, Cellar, Redis, Pulsar, Keycloak, Materia KV; **in HDS, only
   those of the zone**): backups, monitoring and version upgrades leave the client. Limits:
   `modules/supabase/reference/clever-cloud.md` §2. **Storage**: bytes in Cellar, metadata and
   policies of `storage.objects` in the database (one Cellar key opens every bucket); served by
   the app, they change origin: stored XSS, to be closed (`modules/supabase/reference/storage.md`
   §7).
3. **Application rewrite**, often simpler, and testable outside production. **Edge Functions**:
   survey runtime (Deno), `SUPABASE_*` variables, `verify_jwt`; rewrite for the Node.js runtime
   (Node or Bun), or a Deno Docker image. **`pg_cron` + `pg_net`** → a Clever scheduled task
   calling the same URL (`<skill>/templates/cron/`: `cron.json`, `cron-job.sh`, `cron-env.sh`;
   in the app `clevercloud/cron-job.sh POST /tasks/reminder`,
   `CC_PRE_RUN_HOOK="bash clevercloud/cron-env.sh"`, `CRON_RUNS_HERE=1`, `CRON_VARIABLES="API_SERVICE_TOKEN …"`,
   `CRON_BASE_URL`, `CRON_TOKEN_VAR=API_SERVICE_TOKEN` — defaults `CRON_SECRET` and port 8080,
   token required unless `CRON_NO_TOKEN=1`; empty job environment, multi-instance,
   Docker: `modules/supabase/reference/clever-cloud.md` §3 CRON).
4. **Container**, last, at its real cost (build, monitoring, patches, version upgrades).
   **Three containers is an operations job**: beyond three interdependent ones, go back to
   path 1. CKE only outside HDS.

**Extensions**: cross the survey's extensions with `modules/supabase/reference/clever-cloud.md`
§1: five Supabase extensions are missing there, others go through a ticket, and `dev` has none.

**The gate**: **a single recommendation per component**, with its cost and what you lose by
taking the other one — not a menu, the client would decide on criteria they do not have.
Validated in one go, on the complete table.

---

## Phase 3 — Cutover plan

Durations **extrapolated from the survey**, never estimated. The runbook gives the exact order
of operations and the expected output at each step, the **point of no return** (named,
announced), the rollback and its deadline, what is done cold or hot.

Every mass operation is split into batches: a managed `statement_timeout` silently kills a
one-block job (Supabase values: `modules/supabase/reference/survey.md` §3). **When it is not
needed, say so**, with thresholds read from the measurements:

- **no batches** if the longest measured operation (`measure-cost.sh`) stays under a tenth of
  the `statement_timeout` of the session that will run it;
- **no logical replication** if dump + transfer + import + acceptance fit in the tolerated
  window (asked after the POC, phase 0). A common case — and it is not open by default on the add-on.

The runbook then says "cold, no batches", with figures.

---

## Phase 4 — Target (and HDS) → GATE 2

**Every creation carries an explicit `--region`, `--plan` and encryption, on both tracks.**
Otherwise `clever create` / `clever addon create` take `par` (non-HDS), the `dev` plan (5
connections, no extension, no backup) and no encryption, which can only be set **at creation**
(`--option encryption=true`) — afterwards, it is a migration through support. **Encrypted from
creation, on the production plan, so you never migrate later**: every database that allows it
(PostgreSQL, MySQL, Redis, MongoDB, Elasticsearch) is created on a dedicated plan **with
encryption at rest**. The `dev` plan is only accepted for a throwaway trial, on explicit request.
At the slightest doubt about future health data, the target is `parhds` (the POC stays in `par`
with synthetic data until the HDS contract is signed): `TRACK=hds deploy-target.sh` creates in
`par` by default, on the HDS-ready rules (dedicated encrypted plans, only services `parhds` also
offers), prints the "SYNTHETIC DATA ONLY" banner, and moves to `parhds` only with
`HDS_CONTRACT=signed`, the client's declaration (never checked by the skill: `[declared]`).
**One creation path per case.** Level **A** (the app moves, Supabase stays): the root
`<skill>/scripts/provision.sh`, driven by `plan.json`. Levels **B and C** (a survey exists):
`modules/supabase/templates/deploy-target.sh`, driven by `survey.json` (buckets, PostgreSQL
version, HDS track). Never both for the same target: the second would see the first's
resources as "reuse" and its ledger would miss them.
`modules/supabase/templates/deploy-target.sh` applies these rules, `--org` included, on the
plan the go named: `PG_PLAN=<plan.json: the postgresql-addon plan> TRACK=<track>
deploy-target.sh <prefix> <orga_…> survey.json` (`PG_PLAN` is required: the script never picks a
plan, so it creates the plan screen 4 priced). **Dry run
by default**: it shows the plan, the price read live (`<skill>/scripts/price.py`) and a token
(`TOKEN: <12 hex>`, computed by `<skill>/scripts/lib/approval.py`); it only executes with
`--approve <token>`. The token proves that what runs is what was shown, not that a human said
yes: the agreement is the developer's explicit answer naming plan and price. Exit code 75:
application creation limit (hours). Commands, hooks, cron:
`modules/supabase/reference/clever-cloud.md` §3.

**Non-HDS**: go to gate 2.

**HDS**: HDS is designed here, not at the end of the project. Before drawing the target, read
`modules/supabase/reference/hds.md`: architecture requirements (named traceability first),
false beliefs and non-binding text never to quote, good practices never to present as
obligations, questions still open.

**A resource outside the HDS zone gives no signal**: once the contract is signed, a resource
created without `--region parhds` leaves the certified scope even "in Paris", and the application talks to it without complaint.
Fixing it requires a migration with downtime, **outside the certified scope in the meantime**
(`modules/supabase/reference/hds.md` §Zone choice).

**Gate 2**: validate the target architecture and the budget before deploying.

---

## Phase 5 — Data migration

Order: roles → schema → **accounts (`auth.users`)** → data → sequences → objects → **trigger**
→ **files**. Roles first when the target has them (stack kept): a schema that names a missing
role fails. Accounts **before** data (foreign key to `auth.users`).

### Path 1: a single role, owner of the tables

- **It bypasses RLS** unless `ALTER TABLE … FORCE ROW LEVEL SECURITY` is set on every protected
  table: without that `FORCE`, every policy is ignored, with no error. Check, which must return
  zero rows:
  `select relname from pg_class where relrowsecurity and not relforcerowsecurity;`
- **Once `FORCE` is set, the owner sees 0 rows** in `psql`, for lack of an identity. **This is
  not data loss**: check under an identity, or count inside the import transaction.
- **`auth.uid()` is rewritten**: a minimal `auth` schema (`auth.users` + `auth.uid()`, which
  reads `request.jwt.claim.sub` **and** `request.jwt.claims`) keeps the policies identical.
  Identity set **per transaction, never per session** (the pool would reuse it); with no
  identity, no rows — never an error, never everything
  (`modules/supabase/reference/data-migration.md` §1).
- **Import under `FORCE`**: the owner's `INSERT`s are refused. Lift `FORCE` inside the import
  transaction itself, or set the identity of each owner per batch
  (`modules/supabase/reference/data-migration.md` §2).
- **Sequences**: `setval` on the **surveyed** value (`business_sequences` in `survey.json`),
  not on `max(id)` if values were consumed.
- **Sign-up trigger** (`handle_new_user`) **after** the import, otherwise one empty profile per
  imported account; under `FORCE`, this `security definer` is subject to the policies: the app
  sets the new account's identity inside the sign-up transaction.
- **Files** last, resumable: `modules/supabase/scripts/migrate-storage.py` (`--key-file` for a
  private bucket; the object list by `survey-supabase.sh --objects`, run by the script through
  psql, not copied from an MCP output)
  (`modules/supabase/reference/storage.md` §3-6, §9).

### Passwords are migrated

The **bcrypt** hashes (`$2a$`) of `auth.users.encrypted_password`, migrated as they are, are
verified by any bcrypt library: **no user has to reset their password**. Login, sign-up,
session: `modules/supabase/templates/auth-nextjs/`
(`modules/supabase/reference/data-migration.md` §3). Sessions and refresh tokens are not
migrated: users log in again on first access, to be announced.

They are not plaintext credentials, but they are authentication data: copy them with
`modules/supabase/scripts/migrate-accounts.py` (`--db-url-file ~/sb-db-url`, the Session pooler URL
in a 600 file outside the repository; `--target-env-file` written by `<skill>/scripts/env-safe.sh
env-file`): psql reads
the source in a read-only session and loads the target in one transaction, through a 600 file
in a 700 temp folder removed at the end; it prints counts and an md5 fingerprint only, and
refuses a non-empty target. Never through MCP or the agent's context, **never in a
deliverable** (`survey.json`, `acceptance.md`, log) — only their count per algorithm and an md5
fingerprint.

**Not restored as they are**: transparent-encryption `SECURITY LABEL`s, Vault secrets,
sessions and tokens.

---

## Phase 6 — Acceptance → GATE 3

**The deliverable that proves the migration**: a resource × identity matrix, surveyed on the
source, replayed on the target. Identical = faithful migration; a cell that changes points to
the problem. Plus two md5 fingerprints: sorted business rows, accounts (id, email, hash).

Replayable months later thanks to three choices: **real sessions**, tokens refreshed before
querying (otherwise everything fails, like a regression); **expected values in isolation**
(`0`, `>0`), the corpus varies, the rules do not; **server-side count**, otherwise you read the
pagination cap.

**Files**: SHA-256 of the source = read back from Cellar (manifest of `migrate-storage.py`) =
served by the app (`modules/supabase/scripts/check-files.py`, plus headers, refusal of
HTML/SVG, someone else's file; the UPDATE policy is proven by a replacement, not by a 409).
Rights on the source side: HTTP read, or SQL with `storage.operation` set
(`modules/supabase/reference/storage.md` §8).

### Source side: read-only, nothing else

**On the source: never a login, never a write.** A refused `INSERT` still consumes the
sequence; every login creates a session and a token, and changes `last_sign_in_at`. The matrix
is surveyed by **SQL simulation** in `begin read only … rollback`, identity set with
`set_config`, through psql or MCP `execute_sql`
(`modules/supabase/reference/acceptance.md` §1).

### Target side: depending on the path

- **Path 1**: `modules/supabase/scripts/check-http.py` replays the matrix and the **write
  checks**, plus the same SQL simulation on the target; both must give the source's result
  (`modules/supabase/reference/acceptance.md` §2).
- **Stack kept**: `modules/supabase/scripts/sessions.py` then
  `modules/supabase/scripts/check-rls.py`, on the source only with the client's agreement,
  because they open sessions there (`modules/supabase/reference/acceptance.md` §3).

Migrated passwords are checked on the **target** (right and wrong), never on the source.

**Gate 3**: present matrices, fingerprints, file proof and checks; log them.

---

## Scalability: two figures, not a speech

1. **`pool × instances ≤ connection limit of the add-on`**: the first wall of autoscaling,
   acceptance included.
2. **The minimum flavor is chosen on the peak** (build, restore, index rebuild), not on the
   nominal load; small run flavor → dedicated build instance.

Figures and `--build-flavor`: `modules/supabase/reference/clever-cloud.md` §1 and §3 Scaling.

---

## Files

`modules/supabase/scripts/` and `modules/supabase/templates/` (including `auth-nextjs/`): each
one cited at its phase; cron from the shared `<skill>/templates/cron/` (`cron.json`,
`cron-job.sh`, `cron-env.sh`); price from `<skill>/scripts/price.py`; masking from
`<skill>/scripts/lib/common.py mask-json`; `modules/supabase/scripts/selftest.sh` checks the
guards offline; `Dockerfile.*` for a kept stack only
(`modules/supabase/reference/clever-cloud.md` §4). `modules/supabase/reference/`: load each
file when a pointer cites it, not in advance; `hds.md` only serves the HDS track.
