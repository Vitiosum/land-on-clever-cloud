# Runtimes, hooks, workers — what the platform does by itself and what it does not

Versions below were **read** in the runtime pages on 2026-10-24
(`https://www.clever.cloud/developers/doc/deploy/applications/<runtime>/index.html.md`).
Re-read the page before setting a runtime variable; `rules/_runtimes.json` holds the same data
for `plan.py`.

| Runtime (`--type`) | Detected from | Start | Must know |
|---|---|---|---|
| `node` (Node.js & Bun) | `package.json` | `scripts.start` or `main`; `CC_RUN_COMMAND` overrides | runs install, **not build**: `CC_POST_BUILD_HOOK="npm run build"`; devDependencies need `CC_NODE_DEV_DEPENDENCIES=install`; **no `NODE_ENV=production` before the build**; lockfile picks npm/pnpm/yarn/bun; version: `engines.node` (wins) or `CC_NODE_VERSION`, `.nvmrc` ignored |
| `python` | `requirements.txt` (legacy) or native uv — only with `uv.lock` **and** `CC_PYTHON_UV_RUN_COMMAND` (then `CC_PYTHON_MODULE`/`BACKEND` are ignored). Observed 2026-10-26 (tests T4, T5): `uv.lock` alone falls back to the legacy mode and fails; the plan sets `CC_PYTHON_UV_RUN_COMMAND="uv run --no-sync gunicorn <module>"` (no `--bind`: gunicorn reads `PORT`) or `"uv run --no-sync uvicorn --host 0.0.0.0 --port 8080 <module>"` (not run by a shell: no `$VAR`), no `CC_PYTHON_VERSION` (the interpreter follows `requires-python`), `CC_PYTHON_MANAGE_TASKS` is **not run**: the Django tasks go to `CC_POST_BUILD_HOOK="uv run --no-sync python manage.py migrate --noinput"` (once per deploy); `FORWARDED_ALLOW_IPS=*` with gunicorn or uvicorn (else redirects leave as `http://`) | `CC_PYTHON_MODULE="pkg.module:app"` behind `CC_PYTHON_BACKEND` = daphne, gunicorn, uvicorn or uwsgi (default uwsgi) | `CC_RUN_COMMAND` ⇒ listen on **9000**; versions accepted: 3.9 (EOL) … **3.14** (no 3.15 on 2026-10-24) via `CC_PYTHON_VERSION`; `.python-version` is not mentioned by the page: pin explicitly; Django: `CC_PYTHON_MANAGE_TASKS="migrate --no-input, collectstatic --noinput"` (the format that worked on 2026-10-25) runs **during the build** (legacy mode only) and reached the linked PostgreSQL add-on (observed: `Applying …` lines); observed with `CC_PYTHON_MODULE` + `CC_PYTHON_BACKEND=gunicorn`: Clever binds gunicorn to `127.0.0.1:9000` behind nginx (public 8080), ignores `bind` from `gunicorn.conf.py`, starts 2 workers, and applies the file's `worker_class`; Celery: `CC_PYTHON_CELERY_MODULE`, beat `CC_PYTHON_CELERY_USE_BEAT=true` |
| `dotnet` | `*.csproj` / `*.fsproj` | the published binary; `CC_RUN_COMMAND` possible | versions: 6.0 (EOL), **8 (default)**, 9, 10.0 (LTS) via `CC_DOTNET_VERSION`; non-LTS and older are not supported (net7.0 must be retargeted); `ASPNETCORE_URLS=http://0.0.0.0:8080` preset; `CC_DOTNET_PROJ` when several projects, `CC_DOTNET_TFM` when several targets; NuGet restore `NU3018` on Clever's build VM, reproduced 4/4 (SDK 8.0.132, the VM's code-signing trust chain; a local SDK restores fine) → set `DOTNET_NUGET_SIGNATURE_VERIFICATION=false` before the first build (the plan does), say it disables signature verification there, keep it until the platform is fixed; ignore `NU3042` noise |
| `php` | `composer.json` | Apache + PHP-FPM serving `CC_WEBROOT` (absolute from the app root, `/public` for Laravel/Symfony) | composer runs `--no-dev`; `CC_PHP_VERSION` 5.6 … 8.5 (default 8.5; doc read 2026-10-25), set from `require.php`; Laravel `APP_KEY` generated as `base64:` + 32 bytes (`env-safe.sh generate --format laravel`) |
| `go` | `go.mod` | the built module | `CC_GO_PKG` = the package path given to `go install` (e.g. `<module>/cmd/server`) when `main` is not at the root |
| `ruby` | `Gemfile` (+ `Gemfile.lock`) | puma via rackup | `CC_RAKEGOALS="db:migrate, assets:precompile"` |
| `rust` | `Cargo.toml` + `Cargo.lock` | the binary target | `CC_RUST_BIN` when several |
| `maven` / `gradle` / `jar` / `war` | build files | per `clevercloud/*.json` | `CC_JAVA_VERSION` |
| `static` | `index.html` / SSG config | SWS, or Caddy if a `Caddyfile` exists | SWS caches js/css one year and serves the whole repo; SPA fallback needs a Caddyfile at the root (`:8080 { root * dist ; encode gzip ; try_files {path} /index.html ; file_server }`, taken into account by itself); Node 24, npm 11 and Bun 1.4.3 are available at build (test T5, 2026-10-26); `CC_WEBROOT` shrinks the build cache (473 MB → 920 KB) |
| `linux` | anything | `CC_RUN_COMMAND`, mise task, Makefile | tools through `mise.toml` |
| `docker` | `Dockerfile` | `CMD`/`ENTRYPOINT` | `PORT=8080` imposed; no workers, cron, FS Bucket; `CC_DOCKER_EXPOSED_HTTP_PORT` |

## Hooks (environment variables, in order)

`CC_PRE_BUILD_HOOK` → dependencies → `CC_POST_BUILD_HOOK` → build cache → `CC_PRE_RUN_HOOK`
→ start → `CC_RUN_SUCCEEDED_HOOK` / `CC_RUN_FAILED_HOOK`. A failing hook fails the deployment.
`CC_PRE_RUN_HOOK` runs on **every instance at every start**: a migration there must be
idempotent and guarded with several instances. `CC_POST_BUILD_HOOK` runs **once per deploy**, whatever the
number of instances, and sees the linked add-on (tests T2, T5, 2026-10-26): **migrations go there**. Both are
run by a shell (`cd backend && uv run --no-sync bash scripts/prestart.sh` worked), as `CC_RUN_COMMAND` is;
Clever does not expand `$VAR` in the *value of an environment variable* (test T1, 2026-10-25).

## Provenance concepts → Clever

| Elsewhere | On Clever |
|---|---|
| Procfile `web:` | the app; start set explicitly (`CC_PYTHON_MODULE`+backend or `CC_RUN_COMMAND`) |
| Procfile `worker:`, a compose worker built from the same context, a systemd/supervisor worker | `CC_WORKER_COMMAND_0`, `_1`… on the same app (no extra cost, not on Docker); Celery: `CC_PYTHON_CELERY_MODULE`; a second app only for separate scaling or another runtime |
| Procfile `beat:` / `clock:` / `cron:` / `scheduler:`, a Celery beat | **an app of its own with ONE instance** (min == max == 1): a beat starts on every instance with no lock, so N instances run each task N times (test T2, 2026-10-26). `CC_PYTHON_CELERY_USE_BEAT=true` (Celery) or `CC_WORKER_COMMAND_0` (other) on that app only; tasks idempotent; deployed LAST (after the app that migrates); the web app's secrets COPIED to it (`env-safe.sh copy`), its default domain removed after its first deploy only on the developer's explicit yes (`clever domain rm`: it republishes the web app's routes, nothing serves users from it; IRREVERSIBLE: a default domain cannot be added back once removed; declined, the app keeps serving the web app's routes, an env route included, on that domain); the plain beat uses the native Celery variables (observed in legacy mode only, test T2, 2026-10-26) with `CELERY_BEAT_LOGLEVEL=INFO`, `-S` carried by `CELERY_BEAT_SCHEDULER`, any other flag keeps the whole command in `CC_WORKER_COMMAND_0`; proven by `Scheduler: Sending due task` in its logs; `clevercloud/cron.json` runs on the designated app only (`CRON_RUNS_HERE=1`); optional `clever config set zero-downtime false` on it |
| Heroku `release:` | Django, legacy mode: `CC_PYTHON_MANAGE_TASKS` (once per build); uv mode: `CC_POST_BUILD_HOOK` through `uv run --no-sync` (once per deploy); other stacks: `CC_POST_BUILD_HOOK` (once per deploy; `CC_PRE_RUN_HOOK` would run it on every instance) |
| Prisma on SQLite (`datasource` `provider = "sqlite"`; not yet proven in a real test) | `provider = "postgresql"` and `url = env("POSTGRESQL_ADDON_URI")`; when the code also reads `DATABASE_URL` (the plan maps it), `env("DATABASE_URL")` stays and the shell sets it: `export DATABASE_URL="${DATABASE_URL:-$POSTGRESQL_ADDON_URI}";` at the head of `CC_POST_BUILD_HOOK` and in front of the start (`CC_RUN_COMMAND`); Prisma 7 (no url in the block): the URL in `prisma.config.ts`. One strategy: the SQLite migrations do not apply (`migration_lock.toml`) — a PostgreSQL baseline (`prisma migrate dev --name init` against a PostgreSQL database), committed, then `CC_POST_BUILD_HOOK` = `prisma generate` (unless a `build` / `postinstall` script runs it) `&& prisma migrate deploy && <the build>` — migrate BEFORE the build (a Next.js build prerenders pages that query the tables), once per deploy, never `CC_PRE_RUN_HOOK` (a start script that migrates is moved there; a release line's `prisma migrate deploy --schema …` moves before the build, its flags kept); `CC_NODE_DEV_DEPENDENCIES=install`, and `prisma` added to devDependencies when only `@prisma/client` is listed (`npx` would fetch the latest CLI); no psycopg nor pg, a SQLite driver adapter swapped for `@prisma/adapter-pg`. The rows: AFTER that first deploy, app stopped (`clever stop`, the add-on stays reachable), `migrate-sqlite.py` into the empty tables (their foreign keys dropped for the copy and re-added, `_prisma_migrations` not copied), then `clever restart` — Next.js: a rebuild (`deploy.sh --alias <alias> [--branch …] --same-commit-policy rebuild`, the same `--branch` as the first deploy). `--pg-expect-tables` passes on empty tables: the copy's `VERDICT: PASS` and row counts are its proof |
| Scheduler / `vercel.json` crons / `pg_cron` / a VM crontab (`cron-job.sh run <cmd>`) | `clevercloud/cron.json` + `templates/cron/` (`cron-env.sh` by `CC_PRE_RUN_HOOK`, `cron-job.sh`) — below |
| `app.json` `generator: secret` | `env-safe.sh generate NAME` (never printed) |
| compose service with `ports` | an app; compose service without ports and without HTTP: needs a health endpoint, or `CC_WORKER_COMMAND` |
| compose `profiles:` (seed, tools) | not started by default: drop, or a Clever Task |
| formation / dyno sizes | flavors: the POC on nano (XS for Next.js that optimises its images, test T6, 2026-10-26) + build M; production XS × 2 by default (`scripts/lib/sizing.py`), adjusted from the metrics afterwards |

## Proven in real tests (2026-10-25/26), not said elsewhere on this page

| Test | Fact |
|---|---|
| T1 (Go, 2026-10-25) | `CC_GO_PKG` builds the package it names: Go 1.27.3, build in 37 s |
| T2 (Django + Celery, 2026-10-26) | `CC_PYTHON_VERSION=3.10` gave Python 3.10.22; `runtime.txt`, `Procfile` and `.buildpacks` are ignored (set the start explicitly); `CC_PYTHON_MANAGE_TASKS` runs once per build, also with 2 instances; native Celery (`CC_PYTHON_CELERY_*`) keeps HTTP on 8080 (nginx → gunicorn on 9000); without `DATABASE_URL`, Django ran on SQLite with the build, the health check and HTTP 200 all green and 0 table in PostgreSQL (`prove.sh --pg-expect-tables` catches it) |
| T3 (voting app, 2026-10-26) | three apps from one repository, one `APP_FOLDER` each; `clever restart` of a stopped app: first 200 after 25–28 s with the build cache, 49 s without, data intact; a worker that sleeps 100 ms per item did 9.5 votes/s (about 285/s without the sleep): measure before sizing on throughput |
| T4 (Django + uv, 2026-10-26) | psycopg 3 (pure Python) works, libpq is present; a Django app with no database configured answers 200 on `/`, so the default health check sees nothing: a health route that touches the database, then `CC_HEALTH_CHECK_PATH` after the proof |
| T5 (FastAPI uv workspace + React with Bun, 2026-10-26) | a uv virtual workspace runs with `CC_PYTHON_UV_RUN_COMMAND`; a variable set on the app wins over the repository's `.env` |
| T6 (Next.js, 2026-10-26) | pnpm is picked through Corepack; a build M takes 17–35 s; `pnpm start` listens on 8080; nano does not hold Next.js image optimisation (`Killed`, about 3 min of outage), XS does (peak 538 MB); memory metrics: `GET /v4/stats/organisations/<org>/resources/<app>/metrics?only=mem` (one point a minute) |

## Cron jobs — the validated pattern (jobs run with an EMPTY environment)

Observed 2026-10-25: a `clevercloud/cron.json` job only gets `HOME, LC_*, LOGNAME, PATH, PWD,
SHELL, SHLVL, USER` — no app variables, no `INSTANCE_NUMBER`, no `PORT`. A guard such as
`[ "$INSTANCE_NUMBER" = "0" ]` therefore never runs anything. The pattern that worked uses two committed files, the same for every provenance (a Vercel cron,
a Supabase `pg_cron` job…): copy them from `<skill>/templates/cron/` into the app's `clevercloud/`
folder, executable (`git update-index --chmod=+x`), and set three variables (names, not values):

| File / variable | Role |
|---|---|
| `clevercloud/cron.json` | JSON array of strings, `$ROOT` substituted by Clever, UTC: `["0 6 * * * $ROOT/clevercloud/cron-job.sh GET /api/cron"]` |
| `clevercloud/cron-env.sh` (`templates/cron/cron-env.sh`) | run by `CC_PRE_RUN_HOOK="bash clevercloud/cron-env.sh"` at each start: writes `INSTANCE_NUMBER`, `PORT`, the names listed in `CRON_VARIABLES` **and the runtime's own `PATH`** (plus `PYTHONPATH`, `VIRTUAL_ENV`, `NODE_PATH`, `GEM_*`, `BUNDLE_PATH`, `JAVA_HOME`, `GOPATH` when set; `node_modules/.bin` after it) to `clevercloud/.cron-env` (mode 600) — without them a job runs the SYSTEM python, without the app's dependencies (`ModuleNotFoundError`, observed 2026-10-25); it prints the `python3` / `node` the jobs will use in the start logs |
| `clevercloud/cron-job.sh` (`templates/cron/cron-job.sh`) | sources `.cron-env`, refuses without `INSTANCE_NUMBER`, runs on instance 0 only, calls `METHOD PATH` on `CRON_BASE_URL` or `http://127.0.0.1:$PORT` with the secret (variable named by `CRON_TOKEN_VAR`, default `CRON_SECRET`; **required** — the job stops without it, `CRON_NO_TOKEN=1` for a route public on purpose) in a 600 header file read by `curl -H @file`; `cron-job.sh run COMMAND…` runs the app's own command (no route, no token) |
| `CRON_VARIABLES="CRON_SECRET"` | the names the job needs; the values stay app variables |
| `CRON_RUNS_HERE=1` | on the ONE app that runs the jobs: `clevercloud/cron.json` is deployed to every app built from the repository (a beat / scheduler app split from the web app), so `cron-job.sh` runs the job only where this marker is in `.cron-env`, and exits 0 with one line elsewhere (no error); the instance-0 rule still applies on the designated app |

Add `clevercloud/.cron-env` to `.gitignore`. The job output is in the app logs as `CMD` /
`CMDOUT` / `CMDEND` lines: `prove.sh --alias <alias> --cron-expect "<text of the output>"`.
`selftest.sh` runs both scripts as they are, with an empty environment.

The route itself refuses when the secret is unset (otherwise `Bearer undefined` is accepted) and
compares in constant time (a plain `!==` leaks the secret's prefix through response timing):
```ts
import { timingSafeEqual } from "crypto";
function authorized(req: Request): boolean {
  const secret = process.env.CRON_SECRET;
  if (!secret) return false;                                   // unset: refuse
  const got = Buffer.from(req.headers.get("authorization") ?? "");
  const want = Buffer.from(`Bearer ${secret}`);
  return got.length === want.length && timingSafeEqual(got, want);
}
```
Python: `hmac.compare_digest(got, want)`; Go: `subtle.ConstantTimeCompare`; PHP: `hash_equals`.

### Proving the job before stopping the app

A stopped app runs no cron job, and the real schedule may be hours away. On the POC branch only:

1. set the schedule to `*/2 * * * *` in `clevercloud/cron.json`, commit `temp: cron every 2 min (POC proof)`;
2. `bash <skill>/scripts/deploy.sh --alias <alias>`, then
   `bash <skill>/scripts/prove.sh https://<domain> --alias <alias> --cron-expect '<text of the job output>' --cron-wait 300`
   (it re-reads the logs every 20 s until a `CMDOUT` line with that text appears: `CMD`, `CMDOUT`, `CMDEND`);
3. put the original schedule back, commit, deploy again (or `git revert` the temp commit).

If you cannot do it, say exactly: "route proven (401 without the secret, 200 with it), scheduled job
not proven".
