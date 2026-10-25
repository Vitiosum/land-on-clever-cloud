# Surveying a database without direct SQL access

A common situation in pre-sales: the client gives access to the Supabase
console, but neither the database password (it is shown only when the
project is created) nor Docker on the machine — and `supabase db dump` runs
`pg_dump` in a container, even against a remote database.

Without these two things, there is more room left than it seems.

## The Supabase MCP connector

If the Supabase MCP connector is plugged into the project, `execute_sql` runs
any query **without a password**: this is the full survey, and the most complete path.

```bash
modules/supabase/scripts/survey-supabase.sh --queries > survey.sql          # one single block
modules/supabase/scripts/survey-supabase.sh --objects-query > objects.sql   # list of the Storage objects
```

Pass `survey.sql` **as is, in one single call** to `execute_sql` (the tool
accepts the `begin read only; … rollback;` block and returns the result of
the last `select`): the `survey` column is `survey.json`, `method` included.

**Write the result without retyping it** (observed: the agent had copied it
by hand, key by key): write the tool output **as is**, text envelope
included, to a file, then take the JSON out of it. Be frank about the limit: an
MCP answer exists only in the agent's context, so every row it returns has
passed through the model (and its transcript). Fine for the survey's counts;
**never for accounts** (bcrypt hashes: `modules/supabase/scripts/migrate-accounts.py`,
psql source → target) nor for anything secret.

```bash
# mcp-output.txt = raw copy of the execute_sql answer, unedited
modules/supabase/scripts/survey-supabase.sh --extract mcp-output.txt > survey.json
modules/supabase/scripts/survey-supabase.sh --extract objects-output.txt objects > objects.json
```

`--extract` finds the column, validates it (`method` present, a list for
`objects`), names the null keys, and adds the HTTP probes if the URL and a
public key are set (`SUPABASE_URL` or `NEXT_PUBLIC_SUPABASE_URL`;
`SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY` or `SUPABASE_ANON_KEY`) — otherwise it says
the probes were skipped. A truncated output
fails instead of producing a silent partial survey. The queries on a schema
that may be missing (`cron`, `vault`, `net`, `storage`) are guarded by
`to_regclass` and are `null` instead of making the block fail. If it still
fails (permissions), `--queries-detail` prints one query per key: `null` for
the one that fails, to note, never to invent.
`objects.sql` produces `objects.json`, the input of
`modules/supabase/scripts/migrate-storage.py` (pagination beyond a few
thousand objects: `modules/supabase/reference/storage.md` §1).
`list_extensions`, `list_tables`, `list_edge_functions`, `list_migrations`,
`get_project` complete it without SQL.

**Paused project** (free plan): `get_project` returns `INACTIVE`. Restarting
it (`restore_project`) **modifies the client's project**: only with their
agreement, logged in `gates.md`. Then wait until
`get_project` returns **exactly `ACTIVE_HEALTHY`** before any survey: during
`RESTORING` / `COMING_UP`, the HTTP API already answers 200 and the queries
fail or return an incomplete state (observed).

Rules:

- **read-only**: never `apply_migration`, never an `INSERT`; for a
  simulation under an identity, `begin read only; … rollback`
  (`modules/supabase/reference/acceptance.md` §1);
- `pg_dump` stays impossible (it needs the password): the migration export
  is done with `select` (phase 5), which is enough under the thresholds of
  phase 3;
- **password hashes are not shown** in the survey: count them per
  algorithm. Extracting them for the migration is a step of phase 5, to a
  600 file outside the repository.

## What can be read without a password, and what is forbidden

After `supabase login` + `supabase link`, these commands **read**:

```bash
supabase migration list          # local vs remote state
supabase inspect db table-stats  # sizes and counters per table
supabase functions list          # state of the Edge Functions
```

`supabase inspect db --help` lists about twenty probes that are already
useful: `db-stats`, `index-stats`, `role-stats`, `long-running-queries`,
`bloat`. They go through a **temporary login role** that the CLI has the
management API create: tell the client before running them.

**Forbidden on the source, even to "read"** — these commands WRITE to the
client's project:

- `supabase config push` applies the `config.toml` of the repository (Auth,
  redirect URL, e-mail confirmation) to the remote project;
- `supabase db push` applies the local migrations; `--include-all` also
  applies **all** the migrations missing from the remote history, common in
  a cloned repository, each one in its own transaction;
- the old "throwaway migration" (a migration that raises an exception to
  bring a survey back) goes through this same `db push`: removed from the
  module.

Without psql or the MCP connector, the SQL survey cannot be done: HTTP
probes (`modules/supabase/reference/survey.md` §2), `supabase inspect db`,
and ask the client for the MCP connector, an export, or the connection
string. Missing information is noted `null` in `survey.json`, never obtained
through a write.

## Getting the connection string without guessing

Console → **Connect** → *Session pooler*. Copy the **whole** string, do not
rebuild it by hand: the host depends on the region and on the cluster number
(`aws-1-eu-west-1.pooler.supabase.com`, not `aws-0`).

**Take the Session pooler, not the direct connection**: the direct one is
IPv6 by default and fails on an IPv4 network without the dedicated add-on.

The password is never shown again. If it is lost, **do not reset it
yourself**: the reset cuts every connection that uses it (back ends, Edge
Functions, the client's tools). It is the client's decision; otherwise, MCP
path.

If the password contains special characters, it must be percent-encoded in
the URI. Read it from standard input, never as an argument (visible to `ps`,
kept in the history):

```bash
read -rs PGPWD && printf '%s' "$PGPWD" | python3 -I -c "import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read(), safe=''))"; unset PGPWD
```

The scripts of the module then read `SUPABASE_DB_URL` from the environment
and pass the password to `psql` / `pg_dump` through a 600 `PGPASSFILE`
(`modules/supabase/scripts/lib-pg.sh`), never as an argument.

## What stays out of reach

- the **database password**: a human action, console only (never shown
  again);
- installing an Integrations module (Database Webhooks, for example): a
  console button, with no CLI equivalent;
- anything that involves plaintext credentials (password hashes are not
  plaintext credentials: `modules/supabase/GUIDE.md`, phase 5);
- **the bytes of a PRIVATE bucket**: without a user session (a login writes
  to the source), only the project's **secret key** reads them. It is a
  blocking point of the plan until the developer provides it: copied from
  the dashboard (Settings > API keys) into a file OUTSIDE the repository,
  `(umask 077; pbpaste > ~/sb-secret-key)`, clipboard cleared, then
  `migrate-storage.py --key-file ~/sb-secret-key` (read-only `GET`, https to
  `<ref>.supabase.co` only). Say it at framing (`modules/supabase/GUIDE.md`, phase 0).

The **API keys**: the agent never runs `supabase projects api-keys` (it
prints the **secret** keys into the transcript) nor any CLI that extracts a
credential. The publishable key is public (it is in the app's
`NEXT_PUBLIC_*`); the secret key comes from the developer as above. Copy
nothing into a deliverable. The `sb_publishable_` / `sb_secret_` keys go in the
`apikey` header, not in `Authorization: Bearer`. The legacy `anon` /
`service_role` keys (JWT) are **deprecated by Supabase by the end of 2026**:
a client who depends on them must migrate their keys, Clever migration or
not — to flag in phase 0.

Tell the client about these points from the scoping stage: they block the
migration phase if they are found too late.
