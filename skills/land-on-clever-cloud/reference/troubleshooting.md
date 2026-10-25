# Troubleshooting — the message you see is a symptom, the cause is above it

Read the failing deployment from the TOP: `clever activity` for the id, then
`clever logs --deployment-id <id> --until now | bash <skill>/scripts/mask.sh` (or `--since 20m --until
now`; the app's logs can print its own variables: always through `mask.sh`). Match the
**first** abnormal line, not the last one. Two fix attempts, then stop and report what you
observed, the cause you believe, and the next thing to try. Never loop on redeploys.

| First abnormal line | Cause | Fix |
|---|---|---|
| `Killed` during build, then "not listening on 8080" | out of memory on the build VM | `clever scale --build-flavor L`; keep the instance small |
| `Killed` at run time, no trace | out of memory on the instance | bigger `--flavor`, or reduce workers (gunicorn, Node cluster) |
| "listening on localhost instead of publicly" | bind `127.0.0.1` | bind `0.0.0.0` (`HOST=0.0.0.0`, `--host 0.0.0.0`, `next start -H 0.0.0.0`) |
| app logs "listening on 3000/5000/8000" | hard-coded port | read `$PORT`; Docker: `CC_DOCKER_EXPOSED_HTTP_PORT` |
| "not listening on 8080" and nothing started | no start command; Python with `CC_RUN_COMMAND` on 8080 (must be 9000); a worker with no HTTP server | `scripts.start`/`main`, `CC_PYTHON_MODULE`, port 9000, or a health endpoint |
| stack trace with `undefined` / `KeyError` / "Missing env" (sometimes on the first request after start) | variable missing | `env-safe.sh diff`, set it, `clever restart` |
| `ECONNREFUSED 127.0.0.1:5432` / `getaddrinfo ENOTFOUND db` | compose hostname or local DB expected; add-on not linked | read `POSTGRESQL_ADDON_*`/`REDIS_*`; `clever service link-addon`; restart |
| Redis `NOAUTH` / `WRONGPASS` | the add-on requires a password the code does not send | build the client from the add-on variables (URL with password) |
| `next: not found`, `tsc: not found`, `vite: not found` at build | devDependencies not installed | `CC_NODE_DEV_DEPENDENCIES=install`; and no `NODE_ENV=production` before the build |
| build passes, `next start` fails "Could not find a production build" | the runtime never ran the build | `CC_POST_BUILD_HOOK="npm run build"` |
| module not found that works locally | no lockfile, or case-sensitive import (macOS vs Linux) | commit the lockfile; fix the import case |
| Django answers 400 everywhere (deploy "succeeded") | `ALLOWED_HOSTS` in local mode (production branch gated on Heroku's `DYNO`) | explicit production switch; `prove.sh --expect` catches it |
| infinite redirect / `ERR_TOO_MANY_REDIRECTS` | HTTPS redirect without trusting `X-Forwarded-Proto` | `SECURE_PROXY_SSL_HEADER`, `trust proxy`, TrustProxies |
| Django 500 with WhiteNoise "Missing staticfiles manifest" | `collectstatic` never ran | `CC_PYTHON_MANAGE_TASKS="collectstatic --noinput"` |
| `.NET` build: "framework not supported" / SDK missing | target not accepted (non-LTS / old) | retarget to an accepted version (`runtimes-hooks.md`) |
| `.NET` restore: `NU3018 … signing certificate is not trusted by the trust provider` | Clever's .NET build VM (SDK 8.0.132): its code-signing trust chain — reproduced 4/4 on 2026-10-25, not a defect of the project (a local SDK restores fine) | `clever env set DOTNET_NUGET_SIGNATURE_VERIFICATION false --alias <alias>` then `clever deploy --alias <alias> --same-commit-policy rebuild` (or `deploy.sh`); it disables NuGet signature verification on the build VM: say it, keep it until the platform is fixed — do not remove it "to test" (that build fails for sure). Ignore the `NU3042` warnings. |
| `NU1903` warning (a package has a known high-severity vulnerability) | outdated dependency, e.g. Npgsql 4.1.9 | not a blocker: report it (Vision "Watch"), `dotnet list package --vulnerable` |
| app `stopped` (after `clever stop`, or a POC paused) and needed again | not an error | `clever restart --alias <alias>` starts it again from the last build (observed 2026-10-25); no new deploy needed |
| `[ERROR] You have performed that request too much.` / HTTP 429 | Clever API rate limit; the app-creation limit can last hours (seen > 8 h) and seems shared by the organisation or account | `provision.sh` waits with jitter up to `--max-wait` per step, then exits 12 with a diagnostic (reads OK, creates refused). After 2 blocked runs: stop (each try may extend the window), report "blocked by the Clever API", ask Clever support; later, re-run the same `--approve` (existing resources skipped, nothing rolled back) |
| GET / answers 500 right after a "successful" build, health check fails | a latent bug in the code (e.g. `path` used without `require('path')`) | `scan.sh` flags missing Node core requires; fix the code, redeploy |
| 200 but blank page; "supabaseUrl is required" in the browser | build-time variable absent at build | set it, rebuild; `prove.sh --asset-expect` |
| 503 for ~20 s right after a successful deploy | routing propagation | wait (`prove.sh` retries) |
| 504 after exactly 180 s | reverse-proxy request timeout | move long work to a worker, a Task or polling |
| "too many connections" | `pool × instances` above the plan limit | lower the pool, or a bigger plan (read the limit live) |
| cron fires N times, or never does anything | one cron per instance, and jobs run with an EMPTY environment (`$INSTANCE_NUMBER` unset) | the env-file pattern of `runtimes-hooks.md`: `CC_PRE_RUN_HOOK` writes the variables, the job sources them and guards on `${INSTANCE_NUMBER:-0}` |
| a cron job "runs" but nothing happens | the job could not see the app's variables (empty cron environment) | same pattern; read `CMDOUT` lines in the logs |
| uploads vanish after redeploy | ephemeral disk | Cellar (or FS Bucket outside HDS) |
| external DB/API refuses the connection | IP allow-list at the provider; IPv6-only host (Supabase direct DB) | allow the zone's ranges / Unique IP; use the provider's IPv4 pooler |
| "same commit" and nothing happens | nothing new pushed | `clever deploy --same-commit-policy rebuild` |
| "failed to push some refs" / not fast-forward | the POC app's remote has other commits (an earlier attempt, another machine) | `deploy.sh` decides: it forces only an app with the plan's prefix that is in the ledger, after saving the remote head as a local branch `clever-backup/<alias>-<time>`; otherwise exit 13 — ask the developer, never force |
| deploy refused in a worktree | `clever deploy` refuses worktrees | `--branch` from the main checkout |
| deploy refused: "repository is shallow" (`git rev-parse --is-shallow-repository` = true; CI checkouts, `git clone --depth 1`) | either a real shallow push problem, or ANY other push refusal: clever-tools 5.2.0 replaces every push error by this message when `.git/shallow` exists (`src/commands/deploy/deploy.command.js:147-155`) — typically a non-fast-forward when a new root snapshot lands on an app already deployed from an earlier one | with a remote: `git fetch --unshallow`. Without one: a root commit with the same tree on the POC branch, then deploy that branch: `ROOT=$(git commit-tree "clever-poc^{tree}" -m "Clever POC (root commit)")`, `git branch -f clever-poc-deploy "$ROOT"`, `deploy.sh --alias <alias> --branch clever-poc-deploy` (it detects the divergence before the push and applies the prefix + ledger force rule). It is a **snapshot**: redo both commands after every new commit on `clever-poc`; teardown: `git branch -D clever-poc-deploy`. `preflight.sh` detects it (exit 10, deploy gated). |
| `clever ssh --command` errors "cannot attach stdin to a TTY-enabled container" | no TTY (Docker runtime) | use logs and HTTP |

Nothing matches: `clever env set CC_TROUBLESHOOT true`, redeploy (the VM stays up after a
failure for `clever ssh`), then remove the variable.
