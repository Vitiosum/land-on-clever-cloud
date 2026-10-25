# Survey — detail of phase 1

Read in phase 1, to interpret `survey.json` before opening the trade-offs.
The access paths without a password or Docker are in
`modules/supabase/reference/survey-without-access.md`.

---

## 1. Why everything is surveyed: the reference case

On a real migration, 93 risks found by static analysis of the repository
went through adversarial verification. **68 were refuted — 73 % false
positives**, even though the analysis read the code and the official
documentation.

And no reading found the decisive findings:

| Finding | Found by |
|---|---|
| The application role was never set (race between the `AFTER INSERT` trigger and the GoTrue Admin API) | a PostgREST query under a real identity |
| An Edge Function broken from day one (`.schema()` at the end of the chain) | a `curl` |
| A `tstzrange` column queried as a timestamp | the next `curl` |
| `ivfflat` index that cannot be rebuilt on the database plan, i.e. its size (`maintenance_work_mem`) | a `VACUUM FULL` |
| The audit log heavier than the data it tracks | a `pg_database_size` |

---

## 2. HTTP probes and Auth settings

Publishable key only: `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` → code of
`/graphql/v1` and `/rest/v1/`, and `/auth/v1/settings`.

The Auth settings (question 6 of phase 0) are read without a secret key:

```bash
curl -s -H "apikey: $PUBLISHABLE_KEY" https://<ref>.supabase.co/auth/v1/settings
```

Useful fields: `disable_signup`, `mailer_autoconfirm`, `external.*`
(providers). They say what the rewritten auth must reproduce.

**`/rest/v1/` answers 401 with an `sb_publishable_…` key: this is normal**
(observed), not the sign of a closed API or of a wrong key: the root is not
a reliable probe with this key. To know whether PostgREST serves a table,
probe the table itself (`/rest/v1/<table>?select=*&limit=1`, read-only);
`/graphql/v1` is crossed with `pg_extension` (§3).

---

## 3. What `survey-supabase.sh` measures, and what each measure decides

| Measure | What it decides |
|---|---|
| Installed extensions **and their versions** | the tipping point of the whole plan |
| `/graphql/v1` probed **and** `pg_graphql` in `pg_extension` | the endpoint answers even without the extension: only the cross-check with the code decides |
| Rows per table with `count(*)` (not `reltuples`, which is -1 without `ANALYZE`) | volume and acceptance fingerprints |
| DDL of the application tables (columns, identity, CHECK, FK), policies (`using` / `with check`), GRANTs, sequence values | the target schema, the `setval` calls, the expected matrix |
| Password hashes: **count per algorithm** (`$2a$` = bcrypt), never the value | password migration, or a reset to plan |
| Public Auth settings (§2) | what the rewritten auth must reproduce |
| `SECURITY LABEL` (pgsodium / TCE) | encryption to rewrite, it is not restored |
| Roles named in the dump, and which ones carry `BYPASSRLS` | what must exist beforehand on the target |
| Tables with `FORCE ROW LEVEL SECURITY` | an import script that works on Supabase can fail elsewhere |
| Schemas exposed to PostgREST | the application is dead if you forget them |
| `pg_cron` jobs, `pg_net` triggers | logic to move out of the database |
| Vault secrets | to move into environment variables |
| Buckets: count, `public`, size limits, **allowed MIME types**; objects: count, volume, largest, active types (HTML, SVG, XML), policies of `storage.objects` | constraints held by the database, not by S3 or by `config.toml`; duration of the copy; security of the file service (`modules/supabase/reference/storage.md`) |
| Volume per table, TOAST and indexes included | the cutover window |
| `statement_timeout`, `maintenance_work_mem`, database plan (size) | the ceilings you will hit head-on |

**`statement_timeout` on Supabase** (threshold for splitting into batches,
phase 3): 3 s for `anon`, 8 s for `authenticated` (and `service_role`)
through the API, 2 min ceiling as `postgres`.

---

## 4. The three measurement traps, with figures

- **The text dump doubles the volume of vectors.** `pg_dump` writes
  `[0.123,0.456,…]` where the table stores binary `float4`. Measured: 324 MB
  of SQL for 175 MB of table. Extrapolate from the dump, never from the table.
- **A mass `UPDATE` temporarily costs twice the final space** (MVCC
  versions). Plan the margin, and a `VACUUM FULL` — which rebuilds the
  indexes, so it can be impossible on a small database plan.
- **The audit log can weigh more than the data.** If it stores the full row
  as JSONB, each vector is stored twice. Also check its purge policy: a purge
  after three years never runs.
