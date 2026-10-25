# Supabase — the decision only (A / B / C)

The detailed migration method (survey, RLS rewrite, auth import, Storage → Cellar with SHA-256
verification, replayable acceptance) is the Supabase module of this skill:
**`modules/supabase/GUIDE.md`**, loaded for B and C only. Here: which landing, and the rules that
protect the source.

| Level | What moves to Clever | Effort | When |
|---|---|---|---|
| **A** App moves, Supabase stays (default POC) | the web app (native runtime) | S | see it on Clever today; zero risk |
| **B** Data moves, Auth stays | + data in an encrypted PostgreSQL add-on, accessed server-side | L | data sovereignty; HDS (data in `parhds`) |
| **C** Full exit | + auth in the app, files to Cellar, functions to routes | XL | contract, HDS, cost at scale |

`plan.py --level A|B|C`. Sovereignty/HDS only holds for B or C: in A, data stays on Supabase.

## The source is read-only — strictly

- **No login, no sign-up, no write on the source.** Logging in creates sessions and refresh
  tokens and changes `last_sign_in_at`; even refused INSERTs consume sequences.
- Landing A: the POC is plugged into a **DEV Supabase project of the developer, never
  production**. Adding the Clever URL to Auth → URL Configuration (redirect allow-list) is the
  developer's action on that dev project.
- Inventory = read-only SQL only (via the developer's connection, or the Supabase MCP
  `execute_sql`, when no DB password is available):

```sql
begin read only;
select extname, extversion from pg_extension order by 1;
select schemaname, tablename, rowsecurity from pg_tables
 where schemaname not in ('pg_catalog','information_schema','auth','storage','extensions','realtime','vault','graphql','graphql_public','pgsodium','supabase_migrations','net','cron') order by 1,2;
select count(*) as policies from pg_policies where schemaname = 'public';
select relname from pg_class where relrowsecurity and not relforcerowsecurity and relnamespace = 'public'::regnamespace;
select id, public, file_size_limit, allowed_mime_types from storage.buckets;
select count(*) from auth.users;          -- count(*), never reltuples (can be -1)
rollback;
```

- Files on the source are checked over HTTP, read-only, on the path the code really uses
  (a private bucket: `GET /storage/v1/object/authenticated/<bucket>/<object>`; SQL simulation does
  not see `storage.objects` behind `storage.allow_any_operation(...)`).
- Password hashes are never displayed nor written to a deliverable; temporary files holding them
  are deleted.

## Traps that decide the plan

- `NEXT_PUBLIC_SUPABASE_URL` / publishable key are build-time: set before the first build;
  prove with `prove.sh --asset-expect supabase.co`.
- A `service_role` / `sb_secret_` key in a public (`NEXT_PUBLIC_`) name is a leak: stop, tell.
- B/C: `FORCE ROW LEVEL SECURITY` (the owner role bypasses RLS otherwise); `auth.uid()` rewritten
  from a per-transaction setting.
- Files migrated to an app route change origin (`*.supabase.co` → the app's domain): serve with
  `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`,
  `inline` only for png/jpeg/webp/gif (else `attachment` + `application/octet-stream`), refuse
  `text/html` and `image/svg+xml` at upload — or serve from a separate cookie-less domain.
