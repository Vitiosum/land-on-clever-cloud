# Clever Cloud — reference sheet for a Supabase migration

Checked in August 2026 against the official documentation, updated on
24 October 2026 (clever-tools 5.2.0, `clever addon providers show`). 192 facts collected,
**116 confirmed after adversarial refutation, 76 discarded**. Each statement carries
its source.

> The documentation lives under `clever.cloud/developers/` and was reorganised:
> PostgreSQL → `/doc/deploy/databases/postgresql/`, Docker →
> `/doc/deploy/applications/docker/`, Node.js → `/doc/deploy/applications/nodejs/`,
> Cellar → `/doc/deploy/storage/cellar/`, Keycloak → `/doc/deploy/services/keycloak/`,
> CLI → `/doc/cli-reference/`. The old paths redirect (sometimes through a
> `meta refresh`, with an HTTP 200). Up-to-date index:
> `https://www.clever.cloud/developers/llms.txt`.

---

## 0. The finding that decides the target architecture

**The managed PostgreSQL add-on opens by default neither superuser nor role
creation.** The docs explicitly invite you to contact support for an advanced
administration need ("explain your use case, and we will work with you
to find a solution"); PITR and replicas also go through support.

The documentation lists as not available by default: database creation,
**user administration** (only the owner and the read-only accounts managed by
the control plane exist), server configuration changes, extension
installation, replica creation.
([PostgreSQL add-on docs](https://www.clever.cloud/developers/doc/deploy/databases/postgresql/))

Yet the self-hosted Supabase stack **requires dedicated roles**:

| Component | Required roles |
|---|---|
| PostgREST | `authenticator` (LOGIN NOINHERIT) + `anon`, `authenticated`, `service_role`, all GRANTed to `authenticator` |
| GoTrue | `supabase_auth_admin`, which must exist **before** the first start — GoTrue applies its DDL migrations at launch but creates neither the database nor the `auth` schema |
| Storage | `supabase_storage_admin` |
| Realtime | a **superuser role or one with `REPLICATION`** — PostgreSQL reserves slot management to these roles |

**Consequence: the Supabase stack is not redeployed as is on the managed
add-on.** Three paths, in Clever Cloud first order of preference:

> **Trap of path 1, found in code review**: the application then connects
> with the add-on's role, which **owns the tables** and bypasses RLS without
> `FORCE ROW LEVEL SECURITY`. Rule, check and corollary:
> `modules/supabase/GUIDE.md`, phase 5; import under `FORCE` and rewrite of
> `auth.uid()`: `modules/supabase/reference/data-migration.md`.

1. **You do not migrate Supabase, you migrate the application.** Replace
   PostgREST + GoTrue + Realtime with a classic application backend that talks
   to the PostgreSQL add-on. RLS becomes application authorisation, or
   stays in the database with a single role. It is the simplest, most
   native solution, and the cheapest to operate.
2. **PostgreSQL on CKE** (Clever Cloud Kubernetes) if the Supabase stack must
   be kept identical: you get full control back, at the price of
   operating an unmanaged database. **Non-HDS track only**:
   CKE is in public beta, spread over three datacenters in Paris,
   `clever k8s create` has no zone option and the docs mention no
   HDS. It is not a path for a healthcare client.
   ([CKE docs](https://www.clever.cloud/developers/doc/deploy/kubernetes/))
3. **Dedicated add-on + support tickets.** The docs state that these operations
   are not available *by default* and invite you to contact support. To
   qualify case by case — promise nothing without a written answer.

**Realtime corollary**: `wal_level=logical`, slots and logical decoding
are not available by default on the add-on — the only documented
replication is **physical** (up to 2 read-only replicas, created by
support). Postgres Changes therefore implies a request to support, never to
promise without a written agreement; otherwise, CKE (outside HDS).

---

## 1. PostgreSQL extensions, plans and versions

**45 by default**, enabled with `CREATE EXTENSION` without a ticket. **10 on
request**, by ticket in the Ticket Center. **The `dev` plan has no
extension**, neither by default nor on request: a schema that uses `vector`
or `postgis` requires a dedicated plan.
([PostgreSQL docs](https://www.clever.cloud/developers/doc/deploy/databases/postgresql/))

Names to use: those of `CREATE EXTENSION`, not those of the projects
(`vector`, not `pgvector`; `http`, not `pgsql-http`).

| Supabase extension | At Clever Cloud | Note |
|---|---|---|
| `postgis` (+ `postgis_raster`, `postgis_topology`, `postgis_tiger_geocoder`) | **by default** (not on `dev`) | |
| `vector` (pgvector) | **by default** (not on `dev`) | "Vector data type and ivfflat and hnsw access methods" |
| `pgcrypto`, `uuid-ossp`, `pg_trgm`, `btree_gist`, `unaccent`, `hstore`, `citext` | **by default** (not on `dev`) | `gen_random_uuid()` is native since PG 13 |
| `pg_stat_statements`, `hypopg`, `postgres_fdw` | **by default** (not on `dev`) | |
| `plv8` | **PG ≤ 15 only** | "not supported on PostgreSQL 16+"; the default version is 18 |
| `adminpack` | PG ≤ 16 only | not relevant for Supabase |
| `pg_cron`, `pg_net`, `http` | **on request** (never on `dev`) | support ticket |
| `pgaudit`, `pg_partman`, `pg_repack`, `pgtap`, `timescaledb`, `rum`, `pg_ivm` | **on request** (never on `dev`) | |
| **`pgsodium`** | **unavailable** | → `pgcrypto`, or application-side encryption |
| **`supabase_vault`** | **unavailable** | → environment variables |
| **`pgmq`** | **unavailable** | → Pulsar add-on (`beta` plan), or a queue table + scheduled job |
| **`pgjwt`** | **unavailable** | → JWT signing in the application |
| **`pg_graphql`** | **unavailable** | → not worth porting 9 times out of 10 |

No documented way to add an extension outside these two lists:
ask support, promising nothing. The docs do not say whether the
catalog depends on the zone: to confirm in an HDS zone.

**Versions**: the CLI offers 13 to 18 on dedicated plans, **18 by default**
(`clever addon providers show postgresql-addon`; the docs list 14 to 18).
**The `dev` plan only accepts 18** ("selected shared cluster only supports
version 18", observed in testing). Always pass `--addon-version`: if the
source is on 17, it is a version upgrade to test (extensions, `plv8`).

**Plans**: the default of `clever addon create` without `--plan` is the cheapest,
so `dev`. Always pass `--plan`.

| Plan | Max connections | Max size | Backups |
|---|---|---|---|
| `dev` (shared) | **5** | 256 MB | **none** |
| `xxs_*` | 45 | 512 MB to 20 GB | daily, 7 kept |
| `xs_*` | 75 | 2 to 80 GB | same |
| `s_*` | 125 | 5 to 160 GB | same |
| `m_*` · `l_*` · `xl_*` · `xxl_*` | 250 · 500 · 750 · 900 | | same |
| `xxxl_*` · `4xl_*` · `5xl_*` · `6xl_*` | 1200 · 1500 · 1800 · 2100 | | same |

**Smallest dedicated plan: `xxs_tny`** (45 connections, 512 MB, daily
backups kept 7 days, `encryption` option), ≈ €4.97/month
(€0.0068/h, grid of 2026-10-25) against €0.0073/h for `xxs_sml`: the
floor of an encrypted POC, where `dev` is neither encryptable nor backed up.
`modules/supabase/templates/deploy-target.sh` keeps `xxs_sml` by default (standard
size of the Clever demos); `PG_PLAN=xxs_tny` for the floor. The plan and the price
show in the dry run: price read live by `<skill>/scripts/price.py`, bound to the
plan by the approval token of `<skill>/scripts/lib/approval.py` (`--approve <token>`).

Source: `clever addon providers show postgresql-addon` (2026-10-24, 2026-10-25) — read
it again before sizing, it is authoritative.

**Backups**: daily, 7 days, on dedicated plans; **none on
`dev`**. Frequency and retention can be changed through support. List:
`clever database backups <addon-id>`. **PITR (pgBackRest) on request to
support — a billed service** (setup task, on quote).

**Encryption at rest**: LUKS2/aes-xts, **dedicated plans only**, **not
active by default**, **self-service at creation**:
`clever addon create postgresql-addon … --option encryption=true`. Support
only steps in to encrypt an **existing** add-on, by migration. Same
option on Redis and MySQL.
([encryption at rest docs](https://www.clever.cloud/developers/doc/security/encryption-at-rest/))

---

## 2. Add-ons: mapping to the Supabase stack

| Add-on | Replaces | Verified limits |
|---|---|---|
| **PostgreSQL** | the Supabase database | no superuser; roles, PITR and replicas **on request to support**; see §0 and §1 |
| **Cellar** (S3) | Storage | one key gives access to **all** the buckets of the add-on. A second add-on is used to give a **restricted** access (e.g. read-only) through a bucket policy whose `Principal` is its ADDON ID; it does not restrict the keys of the first one. Pre-signed URLs, ACLs, bucket policies, CORS. HDS zones: `parhds`, `rbxhds` (not `grahds`) |
| **Pulsar** | `pgmq` | **single `beta` plan**, `par` and `parhds` only: to flag before replacing `pgmq` in production. Biscuit tokens; infinite retention by default, Cellar offload disabled by default |
| **Keycloak** | GoTrue | **no HDS zone**. Console on `/admin`, credentials through `CC_KEYCLOAK_ADMIN` and `CC_KEYCLOAK_ADMIN_DEFAULT_PASSWORD` |
| **Redis** | cache | `CONFIG` and `CLUSTER` forbidden; modules and backup settings (daily, 7 days) on request to support; `encryption` option at creation |
| **Materia KV** | small shared state | **beta**, zone `par` only: 128 MB per add-on, 5 MB per value, 10 MB per transaction, and the docs **explicitly advise against sensitive or production data**. No HDS zone |
| **CKE** (Kubernetes) | the whole stack | **public beta, Paris, outside HDS**. Vanilla Kubernetes; block storage through CSI (Ceph/RBD), enabled at creation (`clever k8s create --persistent-storage`) or later (`clever k8s add-persistent-storage`) — one-way activation |

**Cellar — injected variables**: exactly three, `CELLAR_ADDON_HOST`,
`CELLAR_ADDON_KEY_ID`, `CELLAR_ADDON_KEY_SECRET`. `HOST` is documented as
a **bare host name, without scheme** (prefix `https://` in the S3 client), but
some examples in the docs write it with one: **check the real value**
(`clever env`). SigV4 signing: region `us-east-1` (verified with `curl
--aws-sigv4`; the JS SDK and the `aws` CLI also worked with `default`).
Bucket to create, global names, recent `aws` CLI: `modules/supabase/reference/storage.md` §4.

**Cellar — not documented**: lifecycle rules, event
notifications, maximum object size, multipart specifications. Do not
assume they exist.

**Storage: never use the local disk** — VMs are disposable, it is
lost at every redeploy. FS Bucket is reserved for applications designed
for a file system, and the docs **strongly advise against** putting
data there. For persistent data: Cellar.

---

## 3. Deployment

### `clever.json` does not exist

There is **no deployment manifest** like `app.json` or
`fly.toml`. The only file is `.clever.json`, written at the root by
`clever create` / `clever link`: it is a **local link file of the
CLI**, not read by the platform at deploy time. It contains `apps[]` with
`app_id`, `org_id`, `name`, `alias`, `deploy_url`, `git_ssh_url` — no
secret, but **a local link, kept out of the repository**: exclude it with
`.git/info/exclude` (done by `modules/supabase/templates/deploy-target.sh`). The prompt
`$ git add .` / `git commit -m "My changes"` that `clever create` prints must be
ignored: never `git add .`.
([source](https://www.clever.cloud/developers/doc/cli-reference/))

Configuration relies on three mechanisms:

1. **`CC_*` environment variables** — the main mechanism;
2. the language's native files, detected automatically;
3. the files in `clevercloud/`: `cron.json`, `jar.json`…

### Deployment hooks

Five, all as environment variables, in lifecycle order:

| Hook | When |
|---|---|
| `CC_PRE_BUILD_HOOK` | before fetching dependencies; its failure fails the deployment |
| `CC_POST_BUILD_HOOK` | after the build; its failure fails the deployment |
| `CC_PRE_RUN_HOOK` | before start; its failure fails the deployment |
| `CC_RUN_SUCCEEDED_HOOK` / `CC_RUN_FAILED_HOOK` | after |

### Node.js: the build does not run `scripts.build`

The build phase **installs the dependencies, nothing else**; the start
runs `scripts.start` (or `main`), or `CC_RUN_COMMAND`. An application that
builds itself (Next.js, Vite, TypeScript…) therefore requires
`CC_POST_BUILD_HOOK="npm run build"` (observed on both cold tests).
And **devDependencies are not installed if `NODE_ENV=production`**:
a build that needs them (Tailwind, TypeScript…) fails. Set
`CC_NODE_DEV_DEPENDENCIES=install`, or do not set `NODE_ENV=production`
before the build.
([Node.js docs](https://www.clever.cloud/developers/doc/deploy/applications/nodejs/))

### Docker

- `Dockerfile` **at the root of the app folder** (repository root, or
  `APP_FOLDER` in a monorepo). Different name → `CC_DOCKERFILE`.
- **Only instruction required by the platform: `CMD` (or `ENTRYPOINT`)**.
- **`CC_RUN_COMMAND` has no effect on Docker.**
- **Docker Compose is not supported**: one application = one container.
- **FS Buckets are not reachable** from Docker.
- Private registry: `CC_DOCKER_LOGIN_USERNAME`, `CC_DOCKER_LOGIN_PASSWORD`,
  `CC_DOCKER_LOGIN_SERVER`. Also useful to get past the anonymous pull limits
  of Docker Hub.
- `CC_MOUNT_DOCKER_SOCKET=true` exposes the host's Docker socket (default
  `false`) — the docs warn that it **breaks all isolation**: never
  for code you do not control.
- Port: `CC_DOCKER_EXPOSED_HTTP_PORT`, **8080 by default**.
- **Crons (`clevercloud/cron.json`) do not exist on Docker.**

### CRON — `clevercloud/cron.json`

Three documented restrictions, and two that matter:

1. `@reboot` **unavailable** — the crontab is added after start.
2. Paths must be absolute: an array of strings
   `"<cron> $ROOT/clevercloud/cron-job.sh POST /tasks/<name>"`, `$ROOT` written as is (not
   `${ROOT}`), substituted at install time.
3. **No clustering** of jobs.
4. **Crons run on EVERY scaler.** With several instances, the
   same job runs several times: **deduplication is up to the
   user**, and the only documented method is a test on
   `INSTANCE_NUMBER`. **With zero-downtime**, old and new instance
   carry the same number for a few minutes: disable
   zero-downtime, or make the jobs idempotent or locked in the database.
5. **A job runs with an EMPTY environment** (`HOME`, `LC_*`, `LOGNAME`,
   `PATH`, `PWD`, `SHELL`, `SHLVL`, `USER` only): neither the application's
   variables, nor `INSTANCE_NUMBER`, nor `PORT` (environment printed by
   a debug job, 2026-10-25). The shebang `#!/bin/bash -l` is not enough.
   Fix: the shared templates `<skill>/templates/cron/cron-env.sh` and
   `cron-job.sh`, copied into `clevercloud/`. `CC_PRE_RUN_HOOK="bash clevercloud/cron-env.sh"`
   writes at start the variables listed in `CRON_VARIABLES` (e.g.
   `CRON_VARIABLES="API_SERVICE_TOKEN"`), plus `INSTANCE_NUMBER`, `PORT`,
   `CRON_BASE_URL` and `CRON_TOKEN_VAR`, into `clevercloud/.cron-env` (mode `600`,
   ignored by git), which `cron-job.sh` sources. `cron-job.sh POST /tasks/<name>`
   runs on instance 0 only, calls `CRON_BASE_URL` (e.g. the internal API URL;
   default `http://127.0.0.1:$PORT`) with the token of the variable named by
   `CRON_TOKEN_VAR` (e.g. `CRON_TOKEN_VAR=API_SERVICE_TOKEN`) as a Bearer header;
   without `.cron-env`, the job stops with an error instead of running N times.
   Defaults of the code: `CRON_TOKEN_VAR` → `CRON_SECRET`; `PORT` → `8080`;
   no `INSTANCE_NUMBER` → job refused (no deduplication possible); the token is
   **required** — an empty or missing token stops the job before any call
   (`CRON_NO_TOKEN=1` only for a route that is public on purpose); a name listed
   in `CRON_VARIABLES` but absent from the instance gives a warning in the start
   logs (name only). `API_SERVICE_TOKEN` is only an example name.

Job output: `clever logs --until now` (`CMD`, `CMDOUT`,
`CMDEND` lines).

And: **no cron on a Docker application**; servers **in UTC**
(`15 3 * * *` = 5:15 in Paris in summer).

It is trap number 1 when replacing `pg_cron` with Clever Cloud cron.

### Scaling — `clever scale`

| Option | Effect |
|---|---|
| `--flavor`, `--instances` | fixed scaling |
| `--min-flavor` / `--max-flavor` | vertical autoscaling |
| `--min-instances` / `--max-instances` | horizontal autoscaling |
| `--build-flavor` | size of the dedicated build instance, or `disabled` |

`--flavor` is mutually exclusive with `--min-flavor`/`--max-flavor` (according to
the docs; the CLI help does not say so).

**An application is created in `XS`**: `clever scale --flavor nano` (or
`--min-flavor nano`) if the smallest is the goal — and then a dedicated build
instance.

**`--build-flavor` is the answer to the case "the runtime flavor cannot
rebuild the index"**: a dedicated, bigger build instance, without
paying for that flavor all the time. On the reference case, the application could
not rebuild its own vector index on its own flavor —
32 MB of `maintenance_work_mem` against 65 required.

**Connections and autoscaling**: `pool × instances` must stay under the
connection limit of the plan (§1). A pool of 20 with 3 instances (60) saturates an
`xxs_*` plan (45); on `dev` (5), a pool of 2 at most, acceptance checks included.

### clever-tools

- **Relaunch a stopped app**: `clever restart --alias <alias>` (observed 2026-10-25: it starts an
  app in the `stopped` state, no new deploy needed); `clever stop --alias <alias>` stops it again.

```bash
clever addon providers                          # catalog
clever addon providers show postgresql-addon    # plans, connections, zones, versions, options
clever create --type node NAME --alias ALIAS --region par --org ORG_ID   # zone and org ALWAYS explicit
clever addon create postgresql-addon NAME --plan xxs_sml --addon-version 18 \
  --region par --org ORG_ID --option encryption=true --yes      # same; --link accepts only ONE alias
clever service link-addon NAME --alias ALIAS    # links an existing add-on
clever env set NAME "$VALUE" --alias ALIAS >/dev/null   # ONE variable per command (value in argv for the duration of the call)
clever scale --min-instances 1 --max-instances 3 --build-flavor M
clever database backups ADDON_ID                # available backups
```

**Setting variables: `clever env set`, one command per variable, nothing
else** (`>/dev/null`: the value is not printed). The two other ways
are to be banned, verified in clever-tools 5.2.0:

- **`clever env import` erases all the application's variables** before
  loading the new list (warning of `clever help env import`);
- **`clever env import-vars` is broken**: `import-vars ZQ_TESTVAR` creates one
  variable per character (`Z`, `Q`, `_`…, `_` receives the value of the shell's
  `$_`) and does not set the requested variable (#1195, reproduced on a test
  app on 2026-10-25).

**Limit of `clever env set`**: the value is an argument of the command,
visible to `ps` on the developer's machine **for the duration of the call**;
clever-tools 5.2.0 has no standard input for values. Never printed
nor logged, but not "never on the command line": do not set a
secret from a shared machine. Feature request to bring to Clever:
let `clever env set` read the value from stdin (an option that does not exist yet). Everywhere else (psql, pg_dump, curl), a
secret goes through a 600 file (`PGPASSFILE`, `curl -H @file`, `curl -K`).

Safe loop over a variables file (names validated, values never
printed): `modules/supabase/templates/deploy-target.sh` §Environment variables. List the **names**
only (a multi-line value would leak its continuation lines with a
`sed 's/=.*/…/'`):
`clever env --alias ALIAS | grep -Eo '^[A-Za-z_][A-Za-z0-9_]*='`.

In HDS: `--region parhds` (or the chosen HDS zone) instead of `par`, on
every command.

Help of a subcommand: `clever help addon create`, not
`clever addon create --help` (which prints the general help in 5.2.0).

Linking an add-on automatically exposes its environment variables to
the application; without an app, they can be read with `clever addon env <id>`
(never printed in a deliverable).

**`--org` on every creation**: without it, the resource goes into the account's
default space. `clever applications` has no `--org` option in
5.2.0: list an organisation with
`clever curl https://api.clever-cloud.com/v2/organisations/<org>/applications`
(and `/addons`), or `clever addon list --org <org> --format json`.

**Rate limit**: `[ERROR] You have performed that request too much.`
On app creations, it lasted more than 8 h once reached
(≈ 30 apps created and deleted within a few hours in one org); reads,
and `clever addon create` at least once, went through meanwhile.
Space out creations, create add-ons and buckets first, resume
later (`deploy-target.sh`: code 75); value and duration not documented,
to ask Clever.

---

## 4. Containerising the Supabase stack

Use only if §0 was settled in favour of CKE (outside HDS) or of a
dedicated plan opened by support. Beyond three interdependent containers —
typically PostgREST + GoTrue + Realtime + imgproxy — ask the question of
path 1 again before stacking applications. **The same `JWT_SECRET` is shared** by GoTrue, PostgREST, Storage and
Realtime — it is the cross-cutting trap of self-hosting.

### PostgREST

- Image `postgrest/postgrest`, **v16.5** noted on 2026-10-24. Scheme
  MAJOR.PATCH, no v15, v14 branch still maintained. Check the tag
  before deploying.
- Port **3000**. Separate administration server, disabled by default.
- Roles: `authenticator` + at least one target role, GRANTed to `authenticator`.
- Each exposed schema must have `USAGE` granted to **all** active roles.

### GoTrue (`supabase/auth`)

**Five strictly required variables**, and only five:

| Variable | Role |
|---|---|
| `GOTRUE_DB_DRIVER=postgres` | |
| `GOTRUE_DB_DATABASE_URL` | connection as `supabase_auth_admin` |
| `GOTRUE_JWT_SECRET` | shared with the other components |
| `API_EXTERNAL_URL` | |
| `GOTRUE_SITE_URL` | default redirect destination, **and implicit allow-list**: any URI sharing the host is accepted as `redirect_to` |

The `supabase_auth_admin` role must exist **before** the first start.

**Custom access token hook**: the function must return **the whole**
`claims` object, not only the added claims — GoTrue **replaces**
the whole set of claims with the hook's output, it does not merge.
And `supabase_auth_admin` has by default **no rights on `public`**: grant it
`USAGE` on the schema holding the function, `EXECUTE` on the function,
then revoke execution from the client roles. If it reads a table, it also needs
`SELECT` **and a policy** if RLS is enabled.

### Realtime

- Image `supabase/realtime`, **v2.147.3** noted on 2026-10-24. Port **4000**.
- Start: `CMD ["/app/bin/server"]` (the image's `run.sh` entrypoint is
  inherited). `/app/limits.sh` does not exist.
- **Multi-tenant**: the socket resolves the tenant from the subdomain, and the
  connection fails if no tenant exists. When self-hosting,
  `SEED_SELF_HOST=true` runs the seed script at start.
  **No channel works until a tenant is created.**
- Postgres Changes requires `wal_level=logical`, a slot, a publication, and a
  role with `REPLICATION`. See §0.

### Storage API

- Image `supabase/storage-api`, **v1.84.23** noted on 2026-10-24.
  Start: `CMD ["node", "dist/start/server.js"]`.
- Role `supabase_storage_admin`; applies its migrations at start and creates
  the `storage` schema.
- Variables: `DATABASE_URL`, `AUTH_JWT_SECRET`, `ANON_KEY`, `SERVICE_KEY`,
  `POSTGREST_URL`.
- **Trap**: the `file` backend relies on extended attributes (xattr).
  On Docker Desktop macOS, bind mounts do not support them.
  With Cellar, use the S3 backend.
- S3-compatible API enabled by default; `S3_PROTOCOL_ACCESS_KEY_ID/SECRET`
  do not enable it, they provide static credentials.

### imgproxy

- Image `ghcr.io/imgproxy/imgproxy`, **v4.0.19** noted on 2026-10-24.
  Port **8080**, the one Clever Cloud expects by default.
- **No required variable**: the container starts bare.
- No database dependency.
- **URL signature disabled by default**, "highly recommended" in
  production: `IMGPROXY_KEY` and `IMGPROXY_SALT`, **in hexadecimal** — imgproxy
  refuses to start if the value cannot be decoded.
- S3/Cellar sources: `IMGPROXY_USE_S3=true`, `IMGPROXY_S3_ENDPOINT`,
  `IMGPROXY_S3_REGION`, `IMGPROXY_S3_ALLOWED_BUCKETS`.
- **Supabase trap**: the default configuration assumes that Storage and
  imgproxy **share the same file system** (same mounted volume). This
  assumption fails as soon as the two are split into separate applications.

---

## 5. What remains to confirm

To ask support before committing:

1. **Extension catalog in an HDS zone**: the docs do not say whether it depends on
   the zone.
2. **Role creation on a dedicated plan**: possible by ticket, or not? The
   answer decides between the managed add-on and, outside HDS, CKE.
3. **`wal_level=logical` and replication slots** on a dedicated plan: same
   question, it decides the fate of Realtime.
4. **Encryption at rest of an existing add-on**: delay and downtime of
   the migration (at creation, it is `--option encryption=true`, no ticket).
5. **Maximum Cellar object size** and lifecycle policy — not
   documented.
