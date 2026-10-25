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

## Verdict (closed list)

- **ready** — every kept component native/add-on, no code change.
- **ready with N changes** — N = distinct files to edit plus the inputs to provide before the
  first build (a variable the code reads with no default), all ≤ M, nothing kept on a SaaS.
- **not ready — N blocking points to settle first** — a fact the POC depends on is unverified or
  missing (a port the runtime cannot serve, Node at build on the static runtime…): no go, no
  provisioning until each point is settled; the Vision lists them under **Blocking**.
- **partial landing** — something stays on a SaaS or needs an L/XL rewrite. Still propose the POC
  for what lands.
- **not a fit today** — a required piece has no path in the zone; say what would change it.
- *not yet covered — report only* — the provenance is recognised (`rules/<id>.json`,
  `coverage: detect-only`) but its mapping is not written: report, do not provision. The Vision
  then shows the changes as indicative only, "**POC:** none", and **What would be lost or must
  move** (data, files, BaaS, cron, platform APIs, from `rules/_couplings.json`) — never a price,
  never "no code change" nor "nothing lost".

The **primary provenance** drives the plan: strong signals before weak ones, then the rule's
`priority`, then its id. A development compose (in a subfolder, `.devcontainer`, Laravel Sail,
`sleep infinity`, `network_mode: service:`) is weak and never drives the plan.

## Evidence labels

- **[read]** a fact in a committed file, with `file:line`.
- **[observed]** a command ran or an endpoint answered (catalog read live, a curl, a log line).
- **[assumed]** an inference — listed under "Not verified yet", each settled by the POC.

Never write "works" without an `[observed]` proof from `prove.sh`.

## Honesty checklist (before showing the Vision)

- Every `[assumed]` is in "Not verified yet".
- Every catalogue fact (zone, plan, price) was read today by a script.
- What the developer **loses** is said as clearly as what they gain.
- The price is the script's figure, including what is *not* in the grid.
- Compliance: zone, encryption, backups, and what is still needed (HDS contract) — never "compliant".

## Gains to pick from (true for this app only)

EU hosting in Paris by a European provider · git-push deploys on native runtimes · managed,
encrypted databases with daily backups · per-second billing on one invoice · reversibility
(standard PostgreSQL/S3/Redis, no proprietary manifest) · HDS zone when health data matters.
