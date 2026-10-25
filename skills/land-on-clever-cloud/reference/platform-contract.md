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
| Workers, cron, tasks | `CC_WORKER_COMMAND[_n]` on the same app (not Docker). `clevercloud/cron.json` = JSON array of `"<cron> $ROOT/path/script.sh"`; jobs run on **every** instance with an **EMPTY environment** (no app variables, no `INSTANCE_NUMBER`, no `PORT`): `CC_PRE_RUN_HOOK` writes the needed variables to a 600 git-ignored file the job sources, then the job guards on `${INSTANCE_NUMBER:-0}` (templates: `runtimes-hooks.md`). Job output: `CMD`/`CMDOUT`/`CMDEND` lines in the app logs. One-off: `clever create --type <t> --task "<cmd>"`. |
| Docker | One container per app, no Compose, `CMD`/`ENTRYPOINT` required, `CC_RUN_COMMAND` ignored, no FS Bucket. |
| Native tooling | The native image ships ffmpeg, ImageMagick, chromium, libreoffice, tesseract, gs, pdftotext, wkhtmltopdf, gdal, exiftool, vips, rsvg-convert — not pandoc nor qpdf (it changes: check, do not assume). A static binary via `CC_PRE_BUILD_HOOK`. Prefer this to Docker. |
| Just after deploy | A 503 for ~20 s = routing propagation (`prove.sh` waits). |
| Behind a proxy | `X-Forwarded-Proto/-Host/-For`: frameworks building absolute URLs or HTTPS redirects must trust them (Django `SECURE_PROXY_SSL_HEADER`, Express `trust proxy`, Laravel TrustProxies). |
| Databases | Created on the smallest **dedicated** plan with `--option encryption=true` (only possible at creation). PostgreSQL connections: xxs 45, xs 75, s 125, m 250, l 500; DEV = 5 connections, no backups, PG 18 forced. `pool × instances ≤ limit`. |

## Agent traps — each one cost a real session

| Trap | Rule (enforced where a script exists) |
|---|---|
| `clever env import` reads STDIN and **deletes every variable absent from its input** | never; `env-safe.sh push/generate` run one `clever env set` per variable and verify the names; `selftest.sh` greps for `env import` |
| `clever env set` takes one name and one value | several: `env-safe.sh push FILE A,B` |
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
