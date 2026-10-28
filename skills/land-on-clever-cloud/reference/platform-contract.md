# Platform contract and agent traps

Verified with clever-tools 5.2.0 and the official documentation on 2026-10-24. Any page of
the docs has a Markdown twin: append `index.html.md`. Index: `https://www.clever.cloud/developers/llms.txt`.
Command syntax: the official skill (`npx skills add CleverCloud/clever-tools`) or
`clever help <group> <command>` (`clever <group> <command> --help` prints the global help).

## What every app must satisfy

| Rule | Detail |
|---|---|
| Listen on `0.0.0.0:$PORT` | `PORT=8080`, also in Docker (it overrides the Dockerfile's `ENV PORT`). Bound to localhost → "listening on localhost instead of publicly". |
| Something must listen on 8080 | Before any HTTP check the deployer checks the public port. A process with no HTTP server (a queue worker) cannot be an app on its own as is: add a minimal health endpoint, or run it with `CC_WORKER_COMMAND` on an app of the same runtime. |
| Health check | Default `GET /`, any 200–499 passes — **a 400 or a blank page passes the deploy**. `CC_HEALTH_CHECK_PATH` makes it strict (2xx). A failed health check lasts ~4 min 20. |
| Port 9000 | Python with `CC_RUN_COMMAND`, and any app behind Request Flow middleware, listens on 9000. |
| Deploy = committed HEAD | `clever deploy` pushes the current branch's HEAD; uncommitted and ignored files are absent. It refuses git worktrees (use `--branch`). `clever deploy --commit` does not exist: `clever restart --commit <id>`. |
| Build on a separate VM | Sized by `clever scale --build-flavor` (M by default in this skill). A build `Killed` = out of memory at build: raise the build flavor, not the instance. Node heap is set from the flavor (`--max-old-space-size=268` in nano). |
| Flavors | A new app is created XS: `clever scale --flavor nano` for the smallest. Lowering `--min-flavor` does not shrink a running instance (`--flavor` + restart). |
| Variables | Change → `clever restart`. Build-time variables (`NEXT_PUBLIC_*`, `VITE_*`, `REACT_APP_*`, `NUXT_PUBLIC_*`, `PUBLIC_*`) are inlined at build: set them before the first build; a change needs a rebuild (`clever deploy --same-commit-policy rebuild`). |
| Add-on variables | Linking injects `<KIND>_ADDON_*` (e.g. `POSTGRESQL_ADDON_URI`, `CELLAR_ADDON_HOST` = bare host). Read them in code rather than copying values. List names with `env-safe.sh names`. |
| Ephemeral disk | Each deploy is a fresh VM. Files → Cellar (S3 SigV4, region `us-east-1`; provisioning creates the add-on, not the bucket). FS Bucket only when software needs a filesystem: mount a relative path (e.g. `/data`), test write → restart → read; not with Docker; never in HDS. |
| Workers, cron, tasks | `CC_WORKER_COMMAND[_n]` on the same app (not Docker). `clevercloud/cron.json` = JSON array of `"<cron> $ROOT/path/script.sh"`; jobs run on **every** instance with an **EMPTY environment** (no app variables, no `INSTANCE_NUMBER`, no `PORT`): `CC_PRE_RUN_HOOK` writes the needed variables to a 600 git-ignored file the job sources, then the job guards on `${INSTANCE_NUMBER:-0}` (templates: `runtimes-hooks.md`). `cron.json` is deployed to every app built from the repository: `cron-job.sh` runs the job only on the app that carries `CRON_RUNS_HERE=1` (the plan sets it on one app) and exits 0 with one line elsewhere — without it, the job runs nowhere. Job output: `CMD`/`CMDOUT`/`CMDEND` lines in the app logs. One-off: `clever create --type <t> --task "<cmd>"`. |
| Docker | One container per app, no Compose, `CMD`/`ENTRYPOINT` required, `CC_RUN_COMMAND` ignored, no FS Bucket. |
| Native tooling | The native image ships ffmpeg, ImageMagick, chromium, libreoffice, tesseract, gs, pdftotext, wkhtmltopdf, gdal, exiftool, vips, rsvg-convert — not pandoc nor qpdf (it changes: check, do not assume). A static binary via `CC_PRE_BUILD_HOOK`. Prefer this to Docker. |
| Just after deploy | A 503 for ~20 s = routing propagation (`prove.sh` waits). |
| Behind a proxy | `X-Forwarded-Proto/-Host/-For`: frameworks building absolute URLs or HTTPS redirects must trust them (Django `SECURE_PROXY_SSL_HEADER`, Express `trust proxy`, Laravel TrustProxies). |
| `$VAR` in a value | Clever does **not** expand `$VAR` in the value of an environment variable: the app receives the literal text (test T1, 2026-10-25). `CC_RUN_COMMAND` and the hooks are run by a shell; a command that needs `$REDIS_URL` goes there (`CC_RUN_COMMAND='<start> --cache-server="$REDIS_URL"'`), or the app reads the variable itself. `CC_PYTHON_UV_RUN_COMMAND` is not run by a shell. |
| Zero-downtime | ON by default (clever-tools 5.2.0): `clever config get zero-downtime` before promising anything; change it with `clever config set zero-downtime true|false` (the enable and disable options of `clever config update` change nothing). Off, a redeploy answers 503 for the whole build + start, with 2 instances too (test T6, 2026-10-26). |
| Beat, cron, scheduler | One instance, in an app of its own: Celery's beat starts on every instance with no lock (test T2, 2026-10-26). The old instances stay about 2 min after a deploy. |
| A route that returns the environment | Path routing closes it with no code change: `clever domain add <name>.cleverapps.io/<path>` for each path the app serves (a converter route by its static prefix: `/photos/`), THEN `clever domain rm <default domain>` (only once every add succeeded: an add that fails, a name taken, stops it); every other path gets the router's 404 (test T1, 2026-10-25). Never the removal first; no prefix may cover the closed path; `/` covers everything. The removal is irreversible: the default domain cannot be added back ("You are not allowed to use app-….cleverapps.io", observed 2026-10-27). Undo, once the route is gone from the code: `clever domain add <name>.cleverapps.io` routes the root again. |
| Autoscaling | Scale-up about 1 min after a sustained load, scale-down about 11 min 30 after it ends (test T1, 2026-10-25, one measurement): a known event is pre-scaled at H-1 (`clever scale --min-instances <n> …`). |
| Redis eviction | `noeviction` by default: a full cache refuses writes (right for a queue); LRU needs a ticket to Clever support [read doc 2026-10-25]. |
| Database version | `clever addon create … --addon-version <v>` picks the major (16 gave PostgreSQL 16.15, test T4, 2026-10-26); the catalogue (`clever addon providers show <provider>`) lists the offered versions per plan. |
| Databases | Created on the **realistic production plan** the plan sized (the real data × 2, connections, RAM; never the smallest by default: `xxs_tny` caps at 512 MB, test T4, 2026-10-26), with `--option encryption=true` (only possible at creation): a plan or encryption only changes through a migration, so the POC uses the production plan. PostgreSQL connections: xxs 45, xs 75, s 125, m 250, l 500; DEV = 5 connections, no backups, PG 18 forced. `pool × processes × instances × 2 ≤ limit` (old instances stay about 2 min per deploy, test T2, 2026-10-26). A fresh PostgreSQL add-on is version 18 by default (18.6 observed, test T2, 2026-10-26) and may already hold PostGIS tables: count the app's own tables (`prove.sh --pg-expect-tables` does). |

## Agent traps — each one cost a real session

| Trap | Rule (enforced where a script exists) |
|---|---|
| `clever env import` reads STDIN and **deletes every variable absent from its input** | never; `env-safe.sh push/generate` run one `clever env set` per variable and verify the names; `selftest.sh` greps for `env import` |
| `clever env set` takes one name and one value | several: `env-safe.sh push FILE A,B` |
| a value that starts with `-` (`clever env set NAME -abc…`): clever-tools 5.2.0 parses with cliparse 0.5.0, then minimist, so `-abc` is read as short options and the value is lost, becomes `true` or is silently empty (about 1 random token in 64 starts with `-`) | `env-safe.sh generate` never draws such a value (`common.safe_secret`); `push`, `copy` and `provision.py` refuse one (exit 6 / 10), names only: set it in the Console |
| `clever env set NAME VALUE` has no stdin or file input (5.2.0): the value is on that command line for the time of the call (`ps` shows it to another user of the machine) | known limit, said as is: run on a single-user machine; `env-safe.sh` keeps the value out of bash variables and output. feature request for Clever: let `clever env set` read the value from stdin (an option that does not exist yet) |
| `clever applications list` without `--org` lists the apps of EVERY organisation of the account | `provision.sh` reuses an app only if its owner is the target (`user_…` for `--org personal`, else the org) |
| a committed `.clever.json` or `.clever-landing/` (`.git/info/exclude` does not cover tracked files) | `preflight.sh` BLOCKs; `ledger.sh`, `provision.sh`, `deploy.sh` refuse (exit 10) |
| `clever logs` streams forever, even with `--since` | always `--until now` (`prove.sh` does) |
| `clever login` opens a browser | check `clever profile`; the developer logs in, in their own terminal |
| `clever open` / `clever console` open the Console, not the app | `clever domain`, then `prove.sh` |
| `clever addon create` without `--plan` takes `dev`, without `--region` takes `par` | `provision.sh` always passes both |
| `--yes` skips the paid-plan confirmation | only after an explicit yes naming plan and price (`provision.sh --approve TOKEN`) |
| `clever ssh --command` fails without a TTY (Docker runtime) | prove over HTTP and logs |
| Same commit deployed twice | `clever deploy --same-commit-policy rebuild` |
| Several orgs on the account | ask once, pass `--org` explicitly |
| Git toplevel is `$HOME` | stop (`scan.sh`, `ledger.sh`, `provision.sh`, `preflight.sh` refuse) |
| A `.gitignore` `node_modules/` does not cover a symlink named `node_modules` | stage files by name, never `git add -A` / `.` |
| `clever create` prints `$ git add .` / `git commit -m "My changes"` | ignore it (rule 12); `provision.sh` filters it; `.clever.json` is git-excluded by `ledger.sh init` |
| `clever env import-vars` is broken in 5.2.0 (one empty variable per character, variable not set; CleverCloud/clever-tools#1195) | never; one `clever env set NAME "$V"` per variable, then verify the names (`env-safe.sh`) |
| `clever applications` (linked apps) has no `--org`; `clever applications list --org <org>` does | list an organisation's apps with `clever applications list --org <org> --format json` (entries carry `app_id`), its add-ons with `clever addon list --org <org> --format json` |
| API rate limit: `[ERROR] You have performed that request too much.` | `provision.sh` spaces creates, waits with jitter up to `--max-wait` (300 s) per step, then exits 12 (reads OK, creates refused); the app-creation limit can last hours and seems shared by the org/account: after 2 blocked runs, stop and contact support |
| `vercel env ls` prints a truncated prefix of Config values; `vercel env pull` writes ALL variables | never run `vercel env …` yourself: `source-vercel.sh names` / `secret NAME --out <600 file>` / `blobs` (the full pulled file is deleted) |
| a resource with the plan's name already exists (an earlier run) | `provision.sh` links it (`clever link <app_id> --alias …`, add-on `service link-addon`) and records it as `reused` in the ledger; a reused add-on's owner is not checked by the script (only its plan and encryption): confirm it in the dry run |
| `clever env` prints values between quotes | never parse secrets yourself: `prove.sh --auth-env NAME --alias …` reads and unquotes it into a 600 header file |
| `provision.sh` without `--org` | refused: name the organisation, or `--org personal` explicitly |
| Shallow clone: `clever deploy` refuses it — and in a shallow clone ANY push error is reported as "repository is shallow" (clever-tools 5.2.0, `deploy.command.js:147-155`) | `git fetch --unshallow`, or a root commit with the same tree deployed with `--branch`; `deploy.sh` checks ancestry against the deployed head before pushing instead of trusting the message |

## No account yet

The developer signs up at `https://console.clever-cloud.com/` and adds a payment method there
(their action, never the agent's), then runs `clever login` in their own terminal. The survey,
the plan and the price work without an account.
