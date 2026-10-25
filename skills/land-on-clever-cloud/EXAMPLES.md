# Examples — real outputs

Generated on 2026-10-25 with the scripts of this repository at this commit, unmodified output
(skill paths shortened to `<skill>`, corpus paths to `<corpus>`). Catalog and prices read live
(`clever addon providers show`, public price grid). Nothing was created on Clever Cloud or
Vercel: provisioning is shown as a dry run (`--org personal`). The agent translates the Vision
into the developer's language.

The voting-app and Heroku checkouts are **shallow clones**: `preflight.sh` returns `RESULT ok
(deploy gated)` (exit 10) with the fix, and the survey and the plan go on.

```bash
bash <skill>/scripts/preflight.sh <repo> --health no|yes
bash <skill>/scripts/scan.sh <repo> --out scan.json
python3 -I <skill>/scripts/plan.py --scan scan.json --health no|yes --out plan.json --report REPORT.md
bash <skill>/scripts/provision.sh --plan plan.json --repo <repo> --org personal --dry-run
```

## docker-compose — dockersamples/example-voting-app

Five services plus a seeding profile: Python/Flask `vote`, Node/Socket.io `result`, .NET `worker` (no HTTP server, net7.0 in the project AND the Dockerfile, Npgsql 4.1.9, upgrade proposed as an optional change), `redis`, `db`. Compose hostnames and hard-coded credentials (kept only as local fallbacks), a latent bug (`path` without `require('path')`), the .NET retarget (project + Dockerfile) and health endpoint; NuGet signature checks off on the build VM, said in the Security line; which apps get which add-on variables; deploy order.

### Preflight (non-HDS)

```
OK    clever-tools 5.2.0
OK    logged in (profile readable; account not printed)
OK    official clever-tools skill found in ~/.agents/skills
OK    toplevel: <corpus>/example-voting-app
OK    on main — POC changes, if any, go on a new branch clever-poc (never a commit on main)
GATE  shallow clone: clever deploy refuses it ('repository is shallow'); survey and plan go on. Fix before the deploy (ask the developer; changes only git metadata): git fetch --unshallow
WARN  no git identity configured (local or global). Do NOT reuse the history's author («<commit author> <author@example.org>»): it is usually someone else (upstream). Commit — and commit-tree — with a neutral LOCAL identity: git -c user.name="Clever POC" -c user.email=clever-poc@localhost commit …; never touch the global config; say it in the report, the developer takes the commits over with git commit --amend --reset-author (or git rebase --exec 'git commit --amend --no-edit --reset-author')
OK    working tree clean
OK    no .env file tracked
OK    not linked yet
OK    .clever-landing/ will be git-excluded by 'ledger.sh init' (.git/info/exclude, no tracked file touched)
OK    non-HDS path: zone par (Paris). Nothing about health in the proposal.
RESULT ok (deploy gated) — survey and plan can go on; fix the GATE line before clever deploy
```

### Scan (summary)

```
# scan (committed files only) — example-voting-app
branch main · HEAD 63e9150 2024-10-10 «Merge pull request #380 from dockersamples/change-ports» · 51 committed files · 0 uncommitted change(s) (not deployed)
provenance: docker-compose* (strong, full), kubernetes (strong, detect-only)   (* = primary: drives the plan)
- result: node [express, socket.io]
    LATENT BUG: path used without require('path') (result/server.js:71) — the route throws, GET answers 500
    hard-coded service hosts: db (result/server.js:21)
- seed-data: docker http=no
    hard-coded service hosts: vote (seed-data/generate-votes.sh:4), vote (seed-data/generate-votes.sh:5), vote (seed-data/generate-votes.sh:6)
- vote: python [flask, gunicorn, redis]
    hard-coded service hosts: redis (vote/app.py:21)
- worker: dotnet versions: dotnet=net7.0 http=no
    hard-coded service hosts: db (worker/Program.cs:19), redis (worker/Program.cs:20), redis (worker/Program.cs:37), db (worker/Program.cs:49)
compose docker-compose.yml: vote(build:vote), result(build:result), worker(build:worker), redis(redis:alpine), db(postgres:15-alpine), seed(build:seed-data)
WARNING hard-coded credentials (masked): healthchecks/postgres.sh:7, result/server.js:21, worker/Program.cs:19, worker/Program.cs:49
```

### Vision — non-HDS path, zone `par`

> ## example-voting-app on Clever Cloud — Ready with 5 changes
> vote, result, worker land natively; redis, db become managed add-ons; seed is not needed.
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | vote | built from vote/ | native: Python runtime, gunicorn, app:app | S | [read] docker-compose.yml:6 |
> | result | built from result/ | native: Node.js runtime (start: node server.js) | S | [read] docker-compose.yml:27 |
> | worker | built from worker/ | native: .NET runtime | M | [read] docker-compose.yml:43 |
> | redis | redis:alpine | addon: Redis add-on s_mono, encrypted | none | [read] docker-compose.yml:54 |
> | db | postgres:15-alpine | addon: PostgreSQL add-on xxs_tny, encrypted (volume db-data → add-on) | none | [read] docker-compose.yml:64 |
> | seed | built from seed-data/ (profile seed) | drop: seeding job (compose profile): a Clever Task if needed | none | [read] docker-compose.yml:81 |
>
> **Changes (5 files):**
> - `result/server.js:21,71` — latent bug: `path` used without require('path') → 500, health check fails; host 'db' hard-coded (+ credentials, masked) → add-on variables, local fallback kept (S)
> - `vote/app.py:21` — host 'redis' hard-coded → add-on variables, local fallback kept (S)
> - `worker/Dockerfile:11,25` — FROM sdk:7.0 → 8.0 (so docker-compose keeps working with net8.0); FROM runtime:7.0 → 8.0 (so docker-compose keeps working with net8.0) (S)
> - `worker/Program.cs:19,20,37` — add a minimal HTTP endpoint on 8080 (no HTTP server today; C# snippet: reference/dotnet-worker-health.md); hosts 'db'/'redis' hard-coded (+ credentials, masked) → add-on variables, local fallback kept (M)
> - `worker/Worker.csproj:5` — retarget net7.0 → net8.0 (not supported) (S)
> - (fallbacks: today's values are used only when the add-on variable is absent, so docker-compose keeps working locally)
> - optional (not counted, only with a yes): `worker/Worker.csproj:10` — upgrade Npgsql 4.1.9 → 8.x (fixes NU1903); NpgsqlConnection/NpgsqlCommand as used here are expected to be compatible — confirm with a dotnet build before keeping it
> **Add-on variables go to:** redis → vote, worker · db → result, worker
> **Deploy order:** worker first, then vote, result — a reader started before the worker creates its tables logs errors like `relation … does not exist` until then (benign)
>
> **What you gain:** hosted in Paris by a European provider; git-push deploys on native runtimes (no image to maintain); managed PostgreSQL/Redis, encrypted at rest from day one, with daily backups; per-second billing on one invoice.
> **What you lose / stays elsewhere:** nothing functional; your compose/local setup stays for development.
> **Config (env vars, no code):** vote: CC_PYTHON_MODULE=app:app, CC_PYTHON_BACKEND=gunicorn · result: defaults · worker: DOTNET_NUGET_SIGNATURE_VERIFICATION=false
> **Not verified yet:** Node version not pinned (result/package.json engines.node): latest LTS will be used · WebSockets (result/server.js:7) through Clever's reverse proxy: prove the data path (socket.io event 'scores'); keep 1 instance (no sticky sessions needed).
> **Watch:** credentials hard-coded in 4 place(s) (masked): never reused on Clever · dependency Npgsql 4.1.9 (worker/Worker.csproj:10): high-severity advisory NU1903 (seen in a .NET build, 2026-10-25): optional upgrade proposed in Changes.
> **Security:** introduced by this plan: worker: DOTNET_NUGET_SIGNATURE_VERIFICATION=false turns off NuGet signature checks on Clever's build VM — NU3018 reproduced 4/4 there (SDK 8.0.132: the VM's code-signing trust chain, not a defect of this project; a local SDK restores fine). Keep it until the platform is fixed.
> **Compliance-ready:** zone par · databases encrypted at rest from creation · backups: Daily - 7 Retained · for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration).
> **POC:** 3 apps nano + build M, redis s_mono (encrypted), postgresql xxs_tny (encrypted), zone par ≈ 0.039 €/h (live grid, read today) — a 2-hour test ≈ 0.08 €; ~28 €/month if never stopped; builds ≈ 0.026 € per full deploy (5-min builds). Add-ons ≈ 0.014 €/h (~10 €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 5 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · go without worker · report only**
> Details: `.clever-landing/REPORT.md`

### Vision — HDS path, zone `parhds`

> ## example-voting-app on Clever Cloud — Ready with 5 changes
> vote, result, worker land natively; redis, db become managed add-ons; seed is not needed.
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | vote | built from vote/ | native: Python runtime, gunicorn, app:app | S | [read] docker-compose.yml:6 |
> | result | built from result/ | native: Node.js runtime (start: node server.js) | S | [read] docker-compose.yml:27 |
> | worker | built from worker/ | native: .NET runtime | M | [read] docker-compose.yml:43 |
> | redis | redis:alpine | addon: Redis add-on s_mono, encrypted | none | [read] docker-compose.yml:54 |
> | db | postgres:15-alpine | addon: PostgreSQL add-on xxs_tny, encrypted (volume db-data → add-on) | none | [read] docker-compose.yml:64 |
> | seed | built from seed-data/ (profile seed) | drop: seeding job (compose profile): a Clever Task if needed | none | [read] docker-compose.yml:81 |
>
> **Changes (5 files):**
> - `result/server.js:21,71` — latent bug: `path` used without require('path') → 500, health check fails; host 'db' hard-coded (+ credentials, masked) → add-on variables, local fallback kept (S)
> - `vote/app.py:21` — host 'redis' hard-coded → add-on variables, local fallback kept (S)
> - `worker/Dockerfile:11,25` — FROM sdk:7.0 → 8.0 (so docker-compose keeps working with net8.0); FROM runtime:7.0 → 8.0 (so docker-compose keeps working with net8.0) (S)
> - `worker/Program.cs:19,20,37` — add a minimal HTTP endpoint on 8080 (no HTTP server today; C# snippet: reference/dotnet-worker-health.md); hosts 'db'/'redis' hard-coded (+ credentials, masked) → add-on variables, local fallback kept (M)
> - `worker/Worker.csproj:5` — retarget net7.0 → net8.0 (not supported) (S)
> - (fallbacks: today's values are used only when the add-on variable is absent, so docker-compose keeps working locally)
> - optional (not counted, only with a yes): `worker/Worker.csproj:10` — upgrade Npgsql 4.1.9 → 8.x (fixes NU1903); NpgsqlConnection/NpgsqlCommand as used here are expected to be compatible — confirm with a dotnet build before keeping it
> **Add-on variables go to:** redis → vote, worker · db → result, worker
> **Deploy order:** worker first, then vote, result — a reader started before the worker creates its tables logs errors like `relation … does not exist` until then (benign)
>
> **What you gain:** hosted in Paris (HDS zone) by a European provider; git-push deploys on native runtimes (no image to maintain); managed PostgreSQL/Redis, encrypted at rest from day one, with daily backups; per-second billing on one invoice.
> **What you lose / stays elsewhere:** nothing functional; your compose/local setup stays for development.
> **Config (env vars, no code):** vote: CC_PYTHON_MODULE=app:app, CC_PYTHON_BACKEND=gunicorn · result: defaults · worker: DOTNET_NUGET_SIGNATURE_VERIFICATION=false
> **Not verified yet:** Node version not pinned (result/package.json engines.node): latest LTS will be used · WebSockets (result/server.js:7) through Clever's reverse proxy: prove the data path (socket.io event 'scores'); keep 1 instance (no sticky sessions needed).
> **Watch:** credentials hard-coded in 4 place(s) (masked): never reused on Clever · dependency Npgsql 4.1.9 (worker/Worker.csproj:10): high-severity advisory NU1903 (seen in a .NET build, 2026-10-25): optional upgrade proposed in Changes.
> **Security:** introduced by this plan: worker: DOTNET_NUGET_SIGNATURE_VERIFICATION=false turns off NuGet signature checks on Clever's build VM — NU3018 reproduced 4/4 there (SDK 8.0.132: the VM's code-signing trust chain, not a defect of this project; a local SDK restores fine). Keep it until the platform is fixed.
> **Compliance-ready:** zone parhds · databases encrypted at rest from creation · backups: Daily - 7 Retained · still needed for health data: an HDS contract signed with Clever Cloud (the zone alone is not compliance).
> **POC:** 3 apps nano + build M, redis s_mono (encrypted), postgresql xxs_tny (encrypted), zone parhds ≈ 0.039 €/h (live grid, read today) — a 2-hour test ≈ 0.08 €; ~28 €/month if never stopped; builds ≈ 0.026 € per full deploy (5-min builds). Add-ons ≈ 0.014 €/h (~10 €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 5 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · go without worker · report only**
> Details: `.clever-landing/REPORT.md`

### Provision dry run — non-HDS path

```
# provision plan — zone par — DRY RUN (nothing executed)
organisation: personal space (explicit)
 1. clever create --type python example-voting-app-poc-vote --alias vote --region par
 2. clever scale --alias vote --flavor nano --build-flavor M --instances 1
 3. clever create --type node example-voting-app-poc-result --alias result --region par
 4. clever scale --alias result --flavor nano --build-flavor M --instances 1
 5. clever create --type dotnet example-voting-app-poc-worker --alias worker --region par
 6. clever scale --alias worker --flavor nano --build-flavor M --instances 1
 7. clever addon create redis-addon example-voting-app-poc-redis --plan s_mono --region par --option encryption=true --link vote
 8. clever service link-addon example-voting-app-poc-redis --alias worker
 9. clever addon create postgresql-addon example-voting-app-poc-db --plan xxs_tny --region par --option encryption=true --link result
10. clever service link-addon example-voting-app-poc-db --alias worker
11. clever env set APP_FOLDER vote --alias vote
12. clever env set CC_PYTHON_BACKEND gunicorn --alias vote
13. clever env set CC_PYTHON_MODULE app:app --alias vote
14. clever env set APP_FOLDER result --alias result
15. clever env set APP_FOLDER worker --alias worker
16. clever env set DOTNET_NUGET_SIGNATURE_VERIFICATION false --alias worker
17. bash <skill>/scripts/env-safe.sh verify APP_FOLDER,CC_PYTHON_BACKEND,CC_PYTHON_MODULE --alias vote
18. bash <skill>/scripts/env-safe.sh verify APP_FOLDER --alias result
19. bash <skill>/scripts/env-safe.sh verify APP_FOLDER,DOTNET_NUGET_SIGNATURE_VERIFICATION --alias worker
price (grid read by plan.py at 2026-10-25T15:05:18Z — run plan.py again for today's): 0.0387 EUR/h running · 2 h ≈ 0.08 EUR · ~28.29 EUR/month if never stopped · builds ≈ 0.026 EUR each
approval token: 7b2880f29661 — a CONSISTENCY check, not a proof of consent: it binds these commands, the price above (as written in plan.json), the skill's scripts and rules, and the verified state of reused add-ons. Show this list and price to the developer; only after their explicit yes naming plan and price, run again with --approve 7b2880f29661
```

## Heroku — heroku/python-getting-started

Django behind gunicorn, a commented `release:` phase, `DJANGO_SECRET_KEY` as `generator: secret`, a committed `.env` (no secret-like names: guidance given), Python 3.15 (3.14 max on Clever, with the source and what to do). Production settings switch on Heroku's `DYNO`: the plan adds `APP_ENV=production`, set before the first build.

### Preflight (non-HDS)

```
OK    clever-tools 5.2.0
OK    logged in (profile readable; account not printed)
OK    official clever-tools skill found in ~/.agents/skills
OK    toplevel: <corpus>/python-getting-started
OK    on main — POC changes, if any, go on a new branch clever-poc (never a commit on main)
GATE  shallow clone: clever deploy refuses it ('repository is shallow'); survey and plan go on. Fix before the deploy (ask the developer; changes only git metadata): git fetch --unshallow
WARN  no git identity configured (local or global). Do NOT reuse the history's author («<commit author> <author@example.org>»): it is usually someone else (upstream). Commit — and commit-tree — with a neutral LOCAL identity: git -c user.name="Clever POC" -c user.email=clever-poc@localhost commit …; never touch the global config; say it in the report, the developer takes the commits over with git commit --amend --reset-author (or git rebase --exec 'git commit --amend --no-edit --reset-author')
OK    working tree clean
WARN  tracked env file(s): «.env» — values already in git history: if any is a secret, the developer rotates it (names only are shown by scan.sh)
OK    not linked yet
OK    .clever-landing/ will be git-excluded by 'ledger.sh init' (.git/info/exclude, no tracked file touched)
OK    non-HDS path: zone par (Paris). Nothing about health in the proposal.
RESULT ok (deploy gated) — survey and plan can go on; fix the GATE line before clever deploy
```

### Scan (summary)

```
# scan (committed files only) — python-getting-started
branch main · HEAD 561bc55 2026-10-10 «Update to Python 3.15 (#288)» · 34 committed files · 0 uncommitted change(s) (not deployed)
provenance: heroku* (strong, full)   (* = primary: drives the plan)
- .: python [django, gunicorn, dj-database-url, whitenoise] versions: python=3.15
Procfile: web, #release
WARNING tracked env files: «.env» (3 names)
```

### Vision — non-HDS path, zone `par`

> ## python-getting-started on Clever Cloud — Ready with 2 changes
> web lands natively; postgresql becomes managed add-on.
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | Procfile web: gunicorn --config… | native: Python runtime, gunicorn, gettingstarted.wsgi:application | S | [read] Procfile:1 |
> | release | Procfile release: ./manage.py migrate --no-input | native: CC_PYTHON_MANAGE_TASKS: migrate at build (commented in Procfile today) | none | [read] Procfile:7 |
> | postgresql | PostgreSQL used in code | addon: PostgreSQL add-on xxs_tny, encrypted (optional; SQLite today) | none | [read] gettingstarted/settings.py:17 |
>
> **Changes (2 files):**
> - `gettingstarted/settings.py:52,71,143` — production settings gated on Heroku's DYNO → also switch on APP_ENV=production (set by provision); SECURE_SSL_REDIRECT needs SECURE_PROXY_SSL_HEADER behind Clever's proxy; DATABASE_URL → fall back to POSTGRESQL_ADDON_URI (S)
> - `requirements.txt` — uncomment psycopg (S)
>
> **What you gain:** hosted in Paris by a European provider; git-push deploys on native runtimes (no image to maintain); managed PostgreSQL, encrypted at rest from day one, with daily backups; per-second billing on one invoice.
> **What you lose / stays elsewhere:** nothing functional for this app.
> **Config (env vars, no code):** web: CC_PYTHON_MODULE=gettingstarted.wsgi:application, CC_PYTHON_BACKEND=gunicorn, CC_PYTHON_VERSION=3.14, APP_ENV=production, CC_PYTHON_MANAGE_TASKS=migrate --no-input, collectstatic --noinput; DJANGO_SECRET_KEY generated on Clever, never shown
> **Not verified yet:** Python 3.14 on Clever (highest accepted, runtime page read 2026-10-24); .python-version says 3.15: keep it (not read by Clever) or set 3.14 on clever-poc · local disk write at gettingstarted/settings.py:151: the disk is ephemeral — Cellar or a database if it must persist.
> **Watch:** `.env` is committed (3 names, none secret-like, values not shown): pushed but not loaded by Clever — harmless here; optional: `git rm --cached -- .env` + .gitignore on clever-poc.
> **Compliance-ready:** zone par · databases encrypted at rest from creation · backups: Daily - 7 Retained · for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration).
> **POC:** 1 app nano + build M, postgresql xxs_tny (encrypted), zone par ≈ 0.015 €/h (live grid, read today) — a 2-hour test ≈ 0.03 €; ~11 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Add-ons ≈ 0.007 €/h (~5 €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 2 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · go without postgresql · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Vision — HDS path, zone `parhds`

> ## python-getting-started on Clever Cloud — Ready with 2 changes
> web lands natively; postgresql becomes managed add-on.
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | Procfile web: gunicorn --config… | native: Python runtime, gunicorn, gettingstarted.wsgi:application | S | [read] Procfile:1 |
> | release | Procfile release: ./manage.py migrate --no-input | native: CC_PYTHON_MANAGE_TASKS: migrate at build (commented in Procfile today) | none | [read] Procfile:7 |
> | postgresql | PostgreSQL used in code | addon: PostgreSQL add-on xxs_tny, encrypted (optional; SQLite today) | none | [read] gettingstarted/settings.py:17 |
>
> **Changes (2 files):**
> - `gettingstarted/settings.py:52,71,143` — production settings gated on Heroku's DYNO → also switch on APP_ENV=production (set by provision); SECURE_SSL_REDIRECT needs SECURE_PROXY_SSL_HEADER behind Clever's proxy; DATABASE_URL → fall back to POSTGRESQL_ADDON_URI (S)
> - `requirements.txt` — uncomment psycopg (S)
>
> **What you gain:** hosted in Paris (HDS zone) by a European provider; git-push deploys on native runtimes (no image to maintain); managed PostgreSQL, encrypted at rest from day one, with daily backups; per-second billing on one invoice.
> **What you lose / stays elsewhere:** nothing functional for this app.
> **Config (env vars, no code):** web: CC_PYTHON_MODULE=gettingstarted.wsgi:application, CC_PYTHON_BACKEND=gunicorn, CC_PYTHON_VERSION=3.14, APP_ENV=production, CC_PYTHON_MANAGE_TASKS=migrate --no-input, collectstatic --noinput; DJANGO_SECRET_KEY generated on Clever, never shown
> **Not verified yet:** Python 3.14 on Clever (highest accepted, runtime page read 2026-10-24); .python-version says 3.15: keep it (not read by Clever) or set 3.14 on clever-poc · local disk write at gettingstarted/settings.py:151: the disk is ephemeral — Cellar or a database if it must persist.
> **Watch:** `.env` is committed (3 names, none secret-like, values not shown): pushed but not loaded by Clever — harmless here; optional: `git rm --cached -- .env` + .gitignore on clever-poc.
> **Compliance-ready:** zone parhds · databases encrypted at rest from creation · backups: Daily - 7 Retained · still needed for health data: an HDS contract signed with Clever Cloud (the zone alone is not compliance).
> **POC:** 1 app nano + build M, postgresql xxs_tny (encrypted), zone parhds ≈ 0.015 €/h (live grid, read today) — a 2-hour test ≈ 0.03 €; ~11 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Add-ons ≈ 0.007 €/h (~5 €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 2 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · go without postgresql · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Provision dry run — non-HDS path

```
# provision plan — zone par — DRY RUN (nothing executed)
organisation: personal space (explicit)
 1. clever create --type python python-getting-started-poc-web --alias web --region par
 2. clever scale --alias web --flavor nano --build-flavor M --instances 1
 3. clever addon create postgresql-addon python-getting-started-poc-postgresql --plan xxs_tny --region par --option encryption=true --link web
 4. clever env set APP_ENV production --alias web
 5. clever env set CC_PYTHON_BACKEND gunicorn --alias web
 6. clever env set CC_PYTHON_MANAGE_TASKS 'migrate --no-input, collectstatic --noinput' --alias web
 7. clever env set CC_PYTHON_MODULE gettingstarted.wsgi:application --alias web
 8. clever env set CC_PYTHON_VERSION 3.14 --alias web
 9. bash <skill>/scripts/env-safe.sh generate DJANGO_SECRET_KEY --alias web
10. bash <skill>/scripts/env-safe.sh verify APP_ENV,CC_PYTHON_BACKEND,CC_PYTHON_MANAGE_TASKS,CC_PYTHON_MODULE,CC_PYTHON_VERSION,DJANGO_SECRET_KEY --alias web
price (grid read by plan.py at 2026-10-25T15:05:21Z — run plan.py again for today's): 0.0151 EUR/h running · 2 h ≈ 0.03 EUR · ~11.05 EUR/month if never stopped · builds ≈ 0.009 EUR each
approval token: 4b743377d7d2 — a CONSISTENCY check, not a proof of consent: it binds these commands, the price above (as written in plan.json), the skill's scripts and rules, and the verified state of reused add-ons. Show this list and price to the developer; only after their explicit yes naming plan and price, run again with --approve 4b743377d7d2
```

## Vercel — Next.js + Vercel Blob + vercel.json cron + middleware

A live Vercel test source. `@vercel/blob` is a rewrite whose target is a Clever service: the Cellar add-on is created and priced; every call site is a change at file:line, plus the shared S3 client file and the SDK pinned to the latest stable version read on npm with its date; `vercel.json` and the `next.config` images host are kept and said harmless; existing files go through `migrate-files.py`. The cron becomes `clevercloud/cron.json` + job script + `CC_PRE_RUN_HOOK` env file. The source has **no auth mechanism**: upload and delivery stay anonymous as on Vercel, shown as an OPEN RISK with the next step; the other weaknesses are reported, never copied as-is.

### Preflight (non-HDS)

```
OK    clever-tools 5.2.0
OK    logged in (profile readable; account not printed)
OK    official clever-tools skill found in ~/.agents/skills
OK    toplevel: <corpus>/vercel-blob-source
OK    on main — POC changes, if any, go on a new branch clever-poc (never a commit on main)
WARN  no git identity configured (local or global). Do NOT reuse the history's author («<commit author> <author@example.org>»): it is usually someone else (upstream). Commit — and commit-tree — with a neutral LOCAL identity: git -c user.name="Clever POC" -c user.email=clever-poc@localhost commit …; never touch the global config; say it in the report, the developer takes the commits over with git commit --amend --reset-author (or git rebase --exec 'git commit --amend --no-edit --reset-author')
OK    working tree clean
OK    no .env file tracked
OK    not linked yet
OK    .clever-landing/ will be git-excluded by 'ledger.sh init' (.git/info/exclude, no tracked file touched)
OK    non-HDS path: zone par (Paris). Nothing about health in the proposal.
RESULT green
```

### Scan (summary)

```
# scan (committed files only) — vercel-blob-source
branch main · HEAD bf3df6d 2026-10-25 «Store Blob public (adaptation du test : la CLI interdit à u…» · 27 committed files · 0 uncommitted change(s) (not deployed)
provenance: vercel* (strong, full)   (* = primary: drives the plan)
- .: node [next] versions: node=22.x http=no
SECURITY (source app, reported only): upload-token route without authentication: anyone can obtain an upload token (anonymous uploads) (app/api/upload/route.ts:8)
SECURITY (source app, reported only): authentication left as a TODO by the template (app/api/blob/route.ts:9)
SECURITY (source app, reported only): authentication left as a TODO by the template (app/api/upload/route.ts:16)
SECURITY (source app, reported only): compares a header with `Bearer ${process.env.CRON_SECRET}`: if the variable is unset, 'Bearer undefined' is accepted — refuse when it is missing, and compare in constant time (crypto.timingSafeEqual), never with !== (app/api/cron/route.ts:7)
SECURITY (source app, reported only): files served with a Content-Type taken from upload metadata (client-chosen): once files share the app's origin, serve with nosniff + CSP sandbox, inline only png/jpeg/webp/gif (app/api/blob/route.ts:26)
SECURITY (source app, reported only): content type checked only against what the client declares (allowedContentTypes): verify the bytes server-side (app/api/upload/route.ts:19)
untracked env files (content not read): «.env.example», «.env.local»
```

### Vision — non-HDS path, zone `par`

> ## vercel-blob-source on Clever Cloud — Ready with 10 changes
> web, middleware land natively; cellar becomes managed add-on; blob-storage (rewrite).
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | Next.js/Node on Vercel | native: Node.js runtime (start: next start) | none | [read] package.json |
> | cellar | (new) target of the @vercel/blob rewrite | addon: Cellar (S3) add-on S | none | [read] package.json |
> | blob-storage | @vercel/blob (4 call sites) | rewrite: Cellar (S3 API) replaces Vercel Blob: server-side upload, delivery route, same… | M | [read] package.json |
> | middleware | Next.js middleware (middleware.ts) | native: runs inside next start on the Node server (no edge network); its headers still… | none | [read] middleware.ts |
> | cron-api-cron | vercel.json cron 0 6 * * * /api/cron | native: clevercloud/cron.json + a job script; jobs run with an EMPTY environment… | S | [read] vercel.json |
>
> **Changes (10 files):**
> - `.gitignore` — ignore .clever-cron.env and .clever-cron.header (S)
> - `app/api/blob/route.ts:18` — delivery → GetObject from Cellar, stream with nosniff + CSP sandbox, inline only png/jpeg/webp/gif (S)
> - `app/api/cron/route.ts:7,11` — refuse when CRON_SECRET is unset (else 'Bearer undefined' passes); compare in constant time; put() → PutObject to Cellar (same key) (S)
> - `app/api/upload/route.ts:8` — client-token upload (handleUpload) → server upload route: keep anonymous as in the source (open risk), check type/size on the bytes, PutObject to Cellar (M)
> - `components/uploader.tsx:30` — uploader → POST the file (FormData) to the new upload route (S)
> - `lib/cellar.ts` — new: one shared S3Client (endpoint https://$CELLAR_ADDON_HOST, region us-east-1, forcePathStyle, CELLAR_ADDON_KEY_ID/SECRET) + the bucket name, used by the routes (S)
> - `package.json` — replace @vercel/blob by @aws-sdk/client-s3@3.1156.0 (latest stable on npm, published 2026-10-22, read 2026-10-25: re-check with npm view @aws-sdk/client-s3 version) and commit the lockfile (S)
> - `clevercloud/` — new files cron-api-cron.sh, cron-env.sh, cron.json: Clever cron for the Vercel cron, empty cron environment handled (S; detail in REPORT.md)
> **Files to migrate:** existing objects → Cellar bucket `vercel-blob-source-poc-files` (created by the script; global name) with `scripts/migrate-files.py` — keys kept, SHA-256 verified, manifest; the source token stays in a 600 file, never shown.
>
> **What you gain:** hosted in Paris by a European provider; git-push deploys on native runtimes (no image to maintain); managed Cellar (S3); per-second billing on one invoice.
> **What you lose / stays elsewhere:** blob-storage → rewrite.
> **Config (env vars, no code):** web: CC_POST_BUILD_HOOK=pnpm run build, CC_NODE_DEV_DEPENDENCIES=install, CELLAR_BUCKET=vercel-blob-source-poc-files, CC_PRE_RUN_HOOK=bash clevercloud/cron-env.sh; CRON_SECRET generated on Clever, never shown
> **Kept as is:** `vercel.json` — ignored by Clever (harmless); delete it when you leave Vercel · `next.config.js:4` — images host *.vercel-storage.com: harmless (files now come from the app's own route); remove after the migration
> **Security:** OPEN RISK: the source has NO auth mechanism, so upload and delivery stay anonymous as on Vercel (kept, not fixed by this POC) — next step: an upload secret checked by the route, or an auth library · source app, reported only, never copied as-is: upload-token route without authentication (app/api/upload/route.ts:8) · compares a header with `Bearer ${process.env.CRON_SECRET}` (app/api/cron/route.ts:7) · files served with a Content-Type taken from upload metadata (client-chosen) (app/api/blob/route.ts:26) · content type checked only against what the client declares (allowedContentTypes) (app/api/upload/route.ts:19).
> **Compliance-ready:** zone par · Cellar has no at-rest encryption option (encrypt sensitive files in the app if required) · for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration).
> **POC:** 1 app nano + build M, cellar S, zone par ≈ 0.008 €/h (live grid, read today) — a 2-hour test ≈ 0.02 €; ~6 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Cellar: free up to 100 MB stored; then ~0.0208 EUR/GB/month; outbound ~0.09 EUR/GB. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 10 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Vision — HDS path, zone `parhds`

> ## vercel-blob-source on Clever Cloud — Ready with 10 changes
> web, middleware land natively; cellar becomes managed add-on; blob-storage (rewrite).
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | Next.js/Node on Vercel | native: Node.js runtime (start: next start) | none | [read] package.json |
> | cellar | (new) target of the @vercel/blob rewrite | addon: Cellar (S3) add-on S | none | [read] package.json |
> | blob-storage | @vercel/blob (4 call sites) | rewrite: Cellar (S3 API) replaces Vercel Blob: server-side upload, delivery route, same… | M | [read] package.json |
> | middleware | Next.js middleware (middleware.ts) | native: runs inside next start on the Node server (no edge network); its headers still… | none | [read] middleware.ts |
> | cron-api-cron | vercel.json cron 0 6 * * * /api/cron | native: clevercloud/cron.json + a job script; jobs run with an EMPTY environment… | S | [read] vercel.json |
>
> **Changes (10 files):**
> - `.gitignore` — ignore .clever-cron.env and .clever-cron.header (S)
> - `app/api/blob/route.ts:18` — delivery → GetObject from Cellar, stream with nosniff + CSP sandbox, inline only png/jpeg/webp/gif (S)
> - `app/api/cron/route.ts:7,11` — refuse when CRON_SECRET is unset (else 'Bearer undefined' passes); compare in constant time; put() → PutObject to Cellar (same key) (S)
> - `app/api/upload/route.ts:8` — client-token upload (handleUpload) → server upload route: keep anonymous as in the source (open risk), check type/size on the bytes, PutObject to Cellar (M)
> - `components/uploader.tsx:30` — uploader → POST the file (FormData) to the new upload route (S)
> - `lib/cellar.ts` — new: one shared S3Client (endpoint https://$CELLAR_ADDON_HOST, region us-east-1, forcePathStyle, CELLAR_ADDON_KEY_ID/SECRET) + the bucket name, used by the routes (S)
> - `package.json` — replace @vercel/blob by @aws-sdk/client-s3@3.1156.0 (latest stable on npm, published 2026-10-22, read 2026-10-25: re-check with npm view @aws-sdk/client-s3 version) and commit the lockfile (S)
> - `clevercloud/` — new files cron-api-cron.sh, cron-env.sh, cron.json: Clever cron for the Vercel cron, empty cron environment handled (S; detail in REPORT.md)
> **Files to migrate:** existing objects → Cellar bucket `vercel-blob-source-poc-files` (created by the script; global name) with `scripts/migrate-files.py` — keys kept, SHA-256 verified, manifest; the source token stays in a 600 file, never shown.
>
> **What you gain:** hosted in Paris (HDS zone) by a European provider; git-push deploys on native runtimes (no image to maintain); managed Cellar (S3); per-second billing on one invoice.
> **What you lose / stays elsewhere:** blob-storage → rewrite.
> **Config (env vars, no code):** web: CC_POST_BUILD_HOOK=pnpm run build, CC_NODE_DEV_DEPENDENCIES=install, CELLAR_BUCKET=vercel-blob-source-poc-files, CC_PRE_RUN_HOOK=bash clevercloud/cron-env.sh; CRON_SECRET generated on Clever, never shown
> **Kept as is:** `vercel.json` — ignored by Clever (harmless); delete it when you leave Vercel · `next.config.js:4` — images host *.vercel-storage.com: harmless (files now come from the app's own route); remove after the migration
> **Security:** OPEN RISK: the source has NO auth mechanism, so upload and delivery stay anonymous as on Vercel (kept, not fixed by this POC) — next step: an upload secret checked by the route, or an auth library · source app, reported only, never copied as-is: upload-token route without authentication (app/api/upload/route.ts:8) · compares a header with `Bearer ${process.env.CRON_SECRET}` (app/api/cron/route.ts:7) · files served with a Content-Type taken from upload metadata (client-chosen) (app/api/blob/route.ts:26) · content type checked only against what the client declares (allowedContentTypes) (app/api/upload/route.ts:19).
> **Compliance-ready:** zone parhds · Cellar has no at-rest encryption option (encrypt sensitive files in the app if required) · still needed for health data: an HDS contract signed with Clever Cloud (the zone alone is not compliance).
> **POC:** 1 app nano + build M, cellar S, zone parhds ≈ 0.008 €/h (live grid, read today) — a 2-hour test ≈ 0.02 €; ~6 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Cellar: free up to 100 MB stored; then ~0.0208 EUR/GB/month; outbound ~0.09 EUR/GB. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 10 files; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Provision dry run — non-HDS path

```
# provision plan — zone par — DRY RUN (nothing executed)
organisation: personal space (explicit)
 1. clever create --type node vercel-blob-source-poc-web --alias web --region par
 2. clever scale --alias web --flavor nano --build-flavor M --instances 1
 3. clever addon create cellar-addon vercel-blob-source-poc-cellar --plan S --region par --link web
 4. clever env set CC_NODE_DEV_DEPENDENCIES install --alias web
 5. clever env set CC_POST_BUILD_HOOK 'pnpm run build' --alias web
 6. clever env set CC_PRE_RUN_HOOK 'bash clevercloud/cron-env.sh' --alias web
 7. clever env set CELLAR_BUCKET vercel-blob-source-poc-files --alias web
 8. bash <skill>/scripts/env-safe.sh generate CRON_SECRET --alias web
 9. bash <skill>/scripts/env-safe.sh verify CC_NODE_DEV_DEPENDENCIES,CC_POST_BUILD_HOOK,CC_PRE_RUN_HOOK,CELLAR_BUCKET,CRON_SECRET --alias web
price (grid read by plan.py at 2026-10-25T15:05:24Z — run plan.py again for today's): 0.0083 EUR/h running · 2 h ≈ 0.02 EUR · ~6.08 EUR/month if never stopped · builds ≈ 0.009 EUR each
approval token: b6fd7b8bdf79 — a CONSISTENCY check, not a proof of consent: it binds these commands, the price above (as written in plan.json), the skill's scripts and rules, and the verified state of reused add-ons. Show this list and price to the developer; only after their explicit yes naming plan and price, run again with --approve b6fd7b8bdf79
```

## Supabase — a todo app with per-user RLS (levels A and B)

A small Next.js todo app on Supabase, written for this example: `@supabase/ssr` in the browser, a `todos` table whose four RLS policies compare `auth.uid()` with `user_id`, Supabase Auth sign-in. Level **A** (the default POC) moves the app and keeps Supabase, plugged into a DEV Supabase project of the developer. Level **B** moves the data to an encrypted PostgreSQL add-on: the browser `.from()` calls become server routes and RLS is rewritten with `FORCE ROW LEVEL SECURITY`; from there the agent follows `modules/supabase/GUIDE.md` (survey, gates, data, acceptance). The source is never written to.

```bash
python3 -I <skill>/scripts/plan.py --scan scan.json --health no --level A|B --out plan.json --report REPORT.md
```

### Preflight (non-HDS)

```
OK    clever-tools 5.2.0
OK    logged in (profile readable; account not printed)
OK    official clever-tools skill found in ~/.agents/skills
OK    toplevel: <corpus>/supabase-todo
OK    on main — POC changes, if any, go on a new branch clever-poc (never a commit on main)
WARN  no git identity configured (local or global). Do NOT reuse the history's author («<commit author> <author@example.org>»): it is usually someone else (upstream). Commit — and commit-tree — with a neutral LOCAL identity: git -c user.name="Clever POC" -c user.email=clever-poc@localhost commit …; never touch the global config; say it in the report, the developer takes the commits over with git commit --amend --reset-author (or git rebase --exec 'git commit --amend --no-edit --reset-author')
OK    working tree clean
OK    no .env file tracked
OK    not linked yet
OK    .clever-landing/ will be git-excluded by 'ledger.sh init' (.git/info/exclude, no tracked file touched)
OK    non-HDS path: zone par (Paris). Nothing about health in the proposal.
RESULT green
```

### Scan (summary)

```
# scan (committed files only) — supabase-todo
branch main · HEAD 0f4a567 2026-10-25 «todo app on Supabase (RLS per user)» · 9 committed files · 0 uncommitted change(s) (not deployed)
provenance: supabase* (strong, full)   (* = primary: drives the plan)
- .: node [next] http=no
```

### Vision — level A (app moves, Supabase stays), zone `par`

> ## supabase-todo on Clever Cloud — Partial landing
> web lands natively; supabase (keep-saas).
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | front/API (often on Vercel) | native: Node.js runtime (start: next start) | S | [read] package.json |
> | supabase | Supabase (DB, Auth, Storage…) | keep-saas: stays on Supabase for the POC — plugged into a DEV Supabase project, never… | none | [read] supabase |
>
> **Changes (1 file):**
> - `package-lock.json` — commit a lockfile (S)
>
> **What you gain:** hosted in Paris by a European provider; git-push deploys on native runtimes (no image to maintain); per-second billing on one invoice.
> **What you lose / stays elsewhere:** supabase → keep-saas.
> **Config (env vars, no code):** web: CC_POST_BUILD_HOOK=npm run build, CC_NODE_DEV_DEPENDENCIES=install
> **Not verified yet:** Node version not pinned (./package.json engines.node): latest LTS will be used.
> **Compliance-ready:** zone par · for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration).
> **POC:** 1 app nano + build M, zone par ≈ 0.008 €/h (live grid, read today) — a 2-hour test ≈ 0.02 €; ~6 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 1 file; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Vision — level B (data moves, Auth stays), zone `par`

> ## supabase-todo on Clever Cloud — Partial landing
> web lands natively; database becomes managed add-on; supabase-rest (rewrite), auth (keep-saas).
>
> | Component | Today | On Clever Cloud | Effort | Evidence |
> |---|---|---|---|---|
> | web | front/API (often on Vercel) | native: Node.js runtime (start: next start) | S | [read] package.json |
> | database | Supabase PostgreSQL | addon: PostgreSQL add-on xxs_tny, encrypted | none | [read] supabase |
> | supabase-rest | PostgREST calls from the browser (3) | rewrite: PostgreSQL add-on accessed server-side; RLS rewritten with FORCE ROW LEVEL… | L | [read] supabase |
> | auth | Supabase Auth | keep-saas: Supabase Auth kept; JWT verified server-side | S | [read] supabase |
>
> **Changes (1 file):**
> - `package-lock.json` — commit a lockfile (S)
>
> **What you gain:** hosted in Paris by a European provider; git-push deploys on native runtimes (no image to maintain); managed PostgreSQL, encrypted at rest from day one, with daily backups; per-second billing on one invoice.
> **What you lose / stays elsewhere:** supabase-rest → rewrite; auth → keep-saas.
> **Config (env vars, no code):** web: CC_POST_BUILD_HOOK=npm run build, CC_NODE_DEV_DEPENDENCIES=install
> **Not verified yet:** Node version not pinned (./package.json engines.node): latest LTS will be used.
> **Compliance-ready:** zone par · databases encrypted at rest from creation · backups: Daily - 7 Retained · for health data later: parhds + HDS contract — choose it now if unsure (changing zone later is a migration).
> **POC:** 1 app nano + build M, postgresql xxs_tny (encrypted), zone par ≈ 0.015 €/h (live grid, read today) — a 2-hour test ≈ 0.03 €; ~11 €/month if never stopped; builds ≈ 0.009 € per full deploy (5-min builds). Add-ons ≈ 0.007 €/h (~5 €/month) keep billing after `clever stop` until deleted: delete them at the end of the POC. Stop or delete in one command each.
> **Repo:** branch `clever-poc`, 1 file; nothing on `main`. `.clever-landing/` is git-excluded.
>
> Reply: **go · report only**
> Details: `.clever-landing/REPORT.md` (… = shortened here, full text there)

### Provision dry run — level B

```
# provision plan — zone par — DRY RUN (nothing executed)
organisation: personal space (explicit)
 1. clever create --type node supabase-todo-poc-web --alias web --region par
 2. clever scale --alias web --flavor nano --build-flavor M --instances 1
 3. clever addon create postgresql-addon supabase-todo-poc-database --plan xxs_tny --region par --option encryption=true --link web
 4. clever env set CC_NODE_DEV_DEPENDENCIES install --alias web
 5. clever env set CC_POST_BUILD_HOOK 'npm run build' --alias web
 6. bash <skill>/scripts/env-safe.sh verify CC_NODE_DEV_DEPENDENCIES,CC_POST_BUILD_HOOK --alias web
price (grid read by plan.py at 2026-10-25T15:36:56Z — run plan.py again for today's): 0.0151 EUR/h running · 2 h ≈ 0.03 EUR · ~11.05 EUR/month if never stopped · builds ≈ 0.009 EUR each
approval token: 8ea741df78c8 — a CONSISTENCY check, not a proof of consent: it binds these commands, the price above (as written in plan.json), the skill's scripts and rules, and the verified state of reused add-ons. Show this list and price to the developer; only after their explicit yes naming plan and price, run again with --approve 8ea741df78c8
```

### Level B, then: the Supabase module

REPORT.md ends with `detailed migration method: modules/supabase/GUIDE.md`. The module's own steps, each read-only on the source or a dry run first:

```bash
(umask 077; pbpaste > ~/sb-db-url)   # the developer pastes the Session pooler URL: never on the agent's command line
SUPABASE_DB_URL_FILE=~/sb-db-url bash <skill>/modules/supabase/scripts/survey-supabase.sh > survey.json   # or --queries / --extract (MCP)
SUPABASE_DB_URL_FILE=~/sb-db-url bash <skill>/modules/supabase/scripts/measure-cost.sh > measures.json
TRACK=non-hds bash <skill>/modules/supabase/templates/deploy-target.sh <prefix> <orga_id> survey.json          # dry run: plan, price, TOKEN
python3 -I <skill>/modules/supabase/scripts/migrate-storage.py objects.json --dry-run                          # then --create-buckets --manifest
python3 -I <skill>/modules/supabase/scripts/check-http.py check-http.json --json target.json --compare source.json   # per identity
```

## What differs between the two paths

These repositories only use PostgreSQL, Redis and Cellar, all available in `parhds`, so the component
tables match; the differences are the zone, the compliance-ready line (HDS contract still needed) and
`--region parhds` in every create. A repository needing MongoDB, Keycloak, Matomo, MailPace, FS Bucket
or Clever Kubernetes gets a different answer in HDS (rewrite, in-app auth, or the two-organisation option
with its boundary): `scripts/selftest.sh` exercises exactly that; see `reference/zones.md`.
