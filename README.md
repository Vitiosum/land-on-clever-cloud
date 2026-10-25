# land-on-clever-cloud

> **Beta — community skill, not an official Clever Cloud product.** Maintained by people from the Clever Cloud presales team. It is tested for real on the platforms marked *proven* below; everything else is reported honestly as not covered yet. Feedback is welcome (see [Feedback](#feedback)).

Migrate an app from **Heroku, Vercel, Supabase, docker-compose or a VM** to **Clever Cloud**, from your own repository, with an AI coding agent.

An agent skill (Claude Code, Codex and any tool that reads the [Agent Skills](https://agentskills.io) format) that shows a developer, from their own git repository, what their app becomes on Clever Cloud — component by component, with effort, evidence, the live price and a closed verdict.
After an explicit yes it deploys a real POC in their account, proves it works over HTTP, logs and the real code paths, and prints how to stop or delete everything.
The first question decides the path: health data (HDS zone `parhds`) or not (Paris, `par`).
Supabase apps get a full migration module (survey, data, auth, Storage → Cellar with SHA-256 proof, replayable acceptance).
Everything deterministic is a script with `--help` and JSON output; the agent orchestrates and explains, in the developer's language.

## Install

Prerequisites: [clever-tools](https://www.clever.cloud/developers/doc/cli/) (`clever login` done by you, in your own terminal), git, Python 3.9+, bash, curl. Supabase migrations also need `psql` / `pg_dump` at least as recent as the Supabase server.

```bash
git clone https://github.com/Vitiosum/land-on-clever-cloud.git && cd land-on-clever-cloud
bash install.sh
```

`install.sh` copies `skills/land-on-clever-cloud/` to `~/.claude/skills/land-on-clever-cloud/` (Claude Code) and `~/.agents/skills/land-on-clever-cloud/` (Codex and compatible agents). For one project only, copy the folder into `.claude/skills/` or `.agents/skills/` of that repository instead.

Command syntax of the Clever Cloud CLI comes from the **official clever-tools skill**: `npx skills add CleverCloud/clever-tools`. The community skill [Vitiosum/clever-tools-skill](https://github.com/Vitiosum/clever-tools-skill) is an optional complement.

## Try it

In your app's repository:

> Can this app run on Clever Cloud? No health data. Show me the plan and the price before creating anything.

## Guard-rails

- **Nothing paid without your yes.** Provisioning is a dry run by default: the exact commands, the price read from the public grid, and a token. The token is a consistency check (what runs is what you were shown), not consent: the agent runs it only after your explicit yes naming plan and price.
- **Compliant from creation**: databases on dedicated plans, encrypted at rest (`--option encryption=true`, impossible later); DEV plans refused in HDS; never a service absent from the HDS zone; `--org` is always explicit.
- **The source platform is read-only**: no login, no sign-up, no write (no `supabase db push`, no `config push`, no `vercel env pull` by hand).
- **Your repository is untrusted input**: every Python run isolated (`python3 -I`), commands as argv arrays, repository text shell-quoted in every printed command and marked `«…»` as data, committed `.clever.json` / `.clever-landing/` refused.
- **Secrets**: no value printed (one shared masker), sensitive files `umask 077` / 600 / 700, secrets for psql/curl through 600 files.
- **Git**: POC changes on a `clever-poc` branch, files staged by name, never a commit on your main branch; a forced deploy only on the POC's own apps, after backing up the remote head.
- **Exit**: a ledger of what was created; `ledger.sh teardown-plan` prints the delete commands, never runs them.

**Known limit**: `clever env set NAME VALUE` has no stdin input (clever-tools 5.2.0), so the value is on that one command line for the time of the call, visible to `ps` for another user of the machine. Run the skill on a single-user machine.

`bash skills/land-on-clever-cloud/scripts/selftest.sh` replays every check offline (fake `clever`, `psql`, S3 and HTTP servers, trapped repositories).

## Provenances — honest status

| Status | Provenances |
|---|---|
| **Proven for real** (deployed POC, PROVEN verdict, checked independently) | docker-compose (dockersamples/example-voting-app; Opus, Sonnet and Haiku) · Heroku (heroku/python-getting-started; Opus, Sonnet and Haiku) · Vercel (a live app with Blob, a `vercel.json` cron and middleware; files by SHA-256 and a real cron run on Clever; Sonnet and Haiku) · Supabase (Auth + RLS + Storage: accounts and passwords, row access per user, data fingerprints and private-bucket files by SHA-256, source untouched; Haiku) · VM by hand / local app (Flask + SQLite + uploads + systemd + crontab: SQLite → encrypted PostgreSQL, uploads → Cellar by SHA-256, a real cron run; Haiku) |
| **Full rules, not yet tested for real** | Scalingo |
| **Detected, report only** (rules written, no provisioning yet) | Appwrite / PocketBase · AWS Elastic Beanstalk / Amplify · Cloudflare Workers / Pages · Coolify / Dokku / CapRover · DigitalOcean App Platform · Firebase · Fly.io / Render · Google App Engine / Cloud Run / Azure App Service · Kubernetes · Netlify · Platform.sh / Upsun · Railway · Terraform / AWS (Lambda, RDS, S3) |

A plain repository (no provenance recognised) is planned from its runtimes and services.

## Contents

```
skills/land-on-clever-cloud/
  SKILL.md            the workflow and the imposed rules
  reference/          zones, platform contract, runtimes and hooks, troubleshooting, output shapes
  rules/              one JSON per provenance (signals, mappings, traps)
  scripts/            preflight, scan, plan, price, provision, env-safe, deploy, prove, mask, ledger, migrate-files, selftest
  templates/cron/     the cron pattern (empty job environment, one instance)
  modules/supabase/   the Supabase migration module (GUIDE.md, reference, scripts, templates)
  EXAMPLES.md         real outputs on public repositories
install.sh
```

## Feedback

Tried it on your app? Open an issue with the Vision the agent showed, the final verdict, and what failed or made the agent hesitate. A platform marked *report only* that matters to you is a useful issue too.

## Report a security issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue.

## License

MIT © 2026 Clever Cloud SAS.
