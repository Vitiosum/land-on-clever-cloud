# Decision rules — how plan.py places a component, and how you explain it

`plan.py` applies these rules; this page is for explaining its output, adjusting it when the
code says otherwise, and for provenances it does not cover yet.

## Placement ladder (highest rung that works without a trick)

1. **native** — a Clever runtime runs the code as is (default for every listed language).
2. **addon** — a managed service: PostgreSQL, MySQL, Redis, MongoDB, Elasticsearch, Cellar,
   Pulsar, Keycloak, Matomo, Metabase… **only if available in the chosen zone** (`zones.md`).
3. **docker** — a system dependency or language the native image and `mise.toml` cannot provide.
4. **cke** — Clever Kubernetes, only when the workload *is* Kubernetes or more than ~3 custom
   containers must stay containers together; BETA; **never in HDS**.
5. **keep-saas** — the app calls a SaaS over HTTPS and nothing replaces it at equal effort
   (Stripe, Supabase Auth short term…). The app still lands. Name it.
6. **rewrite** — tied to a proprietary runtime (Firestore triggers, Vercel Blob, Durable
   Objects, Lambda event sources). Give the target and the effort.
7. **drop** — not needed on Clever (reverse proxy, mail catcher, DB admin UI, seeding profile).

`blocked` = no fallback in the zone → verdict "not a fit today".

## Effort (use these words)

| Effort | Meaning | Typical |
|---|---|---|
| none | config only | env vars, add-on link |
| S | < 1 hour | read add-on variables instead of a hostname, bind 0.0.0.0, uncomment a driver, retarget a framework |
| M | half a day | add a health endpoint to a worker, S3 switch, cron.json |
| L | 1–3 days | replace an auth SaaS with a library, rewrite a data access layer |
| XL | > 1 week | change the data model, replace a realtime engine |

Auth is never S. An `[assumed]` L says which single test would settle it.

The migration sketch of screen 4 turns the heaviest of the final plan — every planned change (the health route and the
`DATABASE_URL` mapping included) and every must-move item — into a bucket with a range of person-days, labelled
`[estimated]`: S 1–3 (none and S), M 4–8, L 9–20 (L and XL). Its reason names the driver ("heaviest planned change: S,
2 changes in 1 file; must move: M"); "no code change planned" only when there is none.

## Compatibility status (screen 3)

Each row of the table is `today → Clever equivalent → status`, worst first: **not in catalogue** (no managed
equivalent: a rewrite, a fallback), **stays elsewhere** (a SaaS kept), **adaptation** (a code or config change),
**native** (as is). The losses come from `rules/_losses.json`, each with its source (logs kept 7 days, no review apps,
no one-off containers, PITR to set up with Clever…).

## Verdict (closed list, two levels)

The **POC verdict** is one of the lines below. The **production verdict** is "ready", "blocked — <why>" (an HDS
contract not signed, a missing HDS-ready item, PITR or a replica to set up, a component not a fit, a database beyond
the catalogue) or "not assessed — report only". A production "blocked" is not a "no": each reason has its hand-off.

- **ready** — every kept component native/add-on, no code change.
- **ready with N changes** — N = distinct files to edit plus the inputs to provide before the
  first build (a variable the code reads with no default), all ≤ M, nothing kept on a SaaS.
- **not ready — N blocking points to settle first** — a fact the POC depends on is unverified or
  missing (a port the runtime cannot serve, Node at build on the static runtime…): no go, no
  provisioning until each point is settled; screen 1 lists them under "Blocks the POC until settled".
- **partial landing** — something stays on a SaaS or needs an L/XL rewrite. Still propose the POC
  for what lands.
- **not a fit today** — a required piece has no path in the zone; say what would change it.
- *not yet covered — report only* — the provenance is recognised (`rules/<id>.json`,
  `coverage: detect-only`) but its mapping is not written: report, do not provision. The screens
  then say "not assessed", show what would have to move as indicative only (data, files, BaaS,
  cron, platform APIs, from `rules/_couplings.json`) and `Price: none — report only` — never "no
  code change" nor "nothing lost".

The **primary provenance** drives the plan: strong signals before weak ones, then the rule's
`priority`, then its id. A development compose (in a subfolder, `.devcontainer`, Laravel Sail,
`sleep infinity`, `network_mode: service:`) is weak and never drives the plan.

## Evidence labels

- **[read]** a fact in a committed file, with `file:line`.
- **[observed]** a command ran or an endpoint answered (catalog read live, a curl, a log line).
- **[assumed]** an inference or a default taken for an unanswered question, settled by the POC or by an answer.
- **[declared]** what the developer states and the skill cannot check (an HDS contract signed, today's plans).
- **[estimated]** a figure of the migration sketch (downtime, effort): replaced by a measure after the POC.

Never write "works" without an `[observed]` proof from `prove.sh`.

## Honesty checklist (before showing a screen)

- Every `[assumed]` is in "Not verified yet".
- Every catalogue fact (zone, plan, price) was read today by a script.
- What the developer **loses** is said as clearly as what they gain.
- The price is the script's figure, including what is *not* in the grid (`NOT PRICED`, `INCOMPLETE`).
- HDS: "HDS-ready architecture: yes" or "… missing <x>", zone, encryption, backups, what is still needed (the HDS
  contract); never the word "compliant" nor "conforme".
- Nothing that does not exist is promised: BYOK/KMS, automatic failover, GPU, Kubernetes in HDS, encryption at rest of
  Cellar, a contractual RTO in the standard offer appear only on a line that says "not available".

## Sizing, proven in real tests (`scripts/lib/sizing.py`)

| Rule | Proof |
|---|---|
| nano and pico only for standby or an autoscaling floor (reduced CPU priority): a production app is XS at least | the skill's rule, 2026-10-26 |
| Next.js that optimises its images: XS at least; in nano, 50 `/_next/image` at 4 in parallel gave `Killed`, 502/503 and about 3 min of outage; XS held (peak 538 MB) | test T6, 2026-10-26 |
| production: 2 instances by default, the 1-instance floor shown too | the skill's rule, 2026-10-26 |
| old instances stay about 2 min during a deploy: `pool × processes × instances × 2 ≤ the plan's connection limit` | test T2, 2026-10-26 |
| a database plan follows the real data × 2, the connections and the RAM, never the smallest by default: `xxs_tny` caps at 512 MB | test T4, 2026-10-26 |
| a beat / cron / scheduler runs in an app of its own with ONE instance: with 2 instances Celery beat ran every task twice | test T2, 2026-10-26 |
| autoscaling: scale-up about 1 min after a sustained load, scale-down about 11 min 30 after it ends: pre-scale a known event at H-1 | test T1, 2026-10-25 (one measurement) |
| PostgreSQL restore from a workstation: about 28 MB/s (10 GB ≈ 6–12 min, 15–30 min to be safe): the downtime of the sketch, always measured before a window is promised | test T4, 2026-10-26 |
| the POC runs on these plans: databases on the realistic production plan (a plan or encryption only changes through a migration), apps on the smallest size that fits | the skill's rule, 2026-10-26 |

## Gains to pick from (true for this app only)

EU hosting in Paris by a European provider · git-push deploys on native runtimes · managed,
encrypted databases with daily backups · per-second billing on one invoice · reversibility
(standard PostgreSQL/S3/Redis, no proprietary manifest) · HDS zone when health data matters.
