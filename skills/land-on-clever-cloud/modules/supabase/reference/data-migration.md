# Data migration — phase 5 code (track 1)

Read this in phase 5 when the target drops PostgREST and GoTrue (track 1): a
single role, owner of the tables, and `FORCE ROW LEVEL SECURITY` set. The
rules are in `modules/supabase/GUIDE.md`, phase 5; here, the checked code.

---

## 1. `auth.uid()` outside Supabase

A minimal `auth` schema (`auth.users` + `auth.uid()`) keeps the policies
**as is**. The application sets the identity **per transaction**, never
per session (the pool would reuse it):

```sql
create or replace function auth.uid() returns uuid language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid $$;
-- in the application, for each request:
begin; select set_config('request.jwt.claim.sub', $1, true); … ; commit;
```

`current_setting(…, true)` + `nullif`: without an identity, `auth.uid()` is
`null`, so no row — never an error, never everything. It reads both
keys, as Supabase does: the app sets `request.jwt.claim.sub`, the acceptance
simulation (`modules/supabase/reference/acceptance.md` §1) sets `request.jwt.claims`; reading only the first one
returns 0 everywhere when the source simulation is replayed on the target.
Storage: `storage.operation()` is rewritten the same way (`modules/supabase/reference/storage.md` §5).

---

## 2. Import under `FORCE RLS`

The owner's `INSERT`s are refused by the policies. Two patterns, both
checked:

```sql
begin;
alter table public.t no force row level security;
\i import_t.sql                       -- or COPY
select setval(pg_get_serial_sequence('public.t','id'), (select max(id) from public.t));
alter table public.t force row level security;
commit;                               -- no open window: all or nothing
```

or set the identity of each owner during the import
(`set_config('request.jwt.claim.sub', <user_id>, true)` per batch
of user rows), which also makes the `WITH CHECK`s pass.

The `setval` of the first pattern holds for a sequence never consumed beyond
`max(id)`; otherwise, take the **surveyed** value (`modules/supabase/GUIDE.md`, phase 5).

---

## 3. Rewritten authentication: `modules/supabase/templates/auth-nextjs/`

With GoTrue dropped (track 1), the application takes over sign-in, sign-up and
sign-out. Next.js template (App Router) checked on the real migration of
2026-10-25 (right and wrong passwords of the migrated accounts, sign-up
with a trigger under `FORCE`); dependencies `pg`, `bcryptjs`, `jose`:

| File | Role |
|---|---|
| `lib/db.ts` | pool on `POSTGRESQL_ADDON_URI`, `withIdentity()`: identity and `storage.operation` per transaction |
| `lib/auth.ts` | `BCRYPT_COST` = the cost of the migrated hashes (survey: `auth.password_hash_cost`, e.g. `06`), used for the dummy hash and new accounts; `checkPassword()`: bcrypt on `encrypted_password`, fake hash for an unknown e-mail, **banned (`banned_until` in the future) and unconfirmed accounts refused** as in GoTrue; `createUser()`: e-mail in lowercase, identity set before the `INSERT`, without `RETURNING` |
| `lib/session.ts` | `httpOnly` cookie carrying an HS256 JWT signed by `SESSION_SECRET` (`jose`), 1 h; secret **mandatory** (32 bytes), otherwise an error |
| `instrumentation.ts` | checks `SESSION_SECRET` at startup: without it, the instance does not start |
| `lib/ratelimit.ts` | in-memory rate limiting (per instance), JSON body capped at 10 KB |
| `app/api/auth/login`, `signup`, `logout` | `POST`; `login`: identical 401 in every refusal case, 429 beyond 10 attempts; `signup` **closed by default** (`SIGNUP_ENABLED=true` opens it), 8-character password, 409 if the e-mail exists; `logout`: **relative** redirect to `/login` |
| `scripts/forge-token.mjs` | acceptance token signed by the same secret: the `token_cmd` of `check-http.json` |

Safe defaults, to open only for a surveyed reason:
`SIGNUP_ENABLED=true` if the source had `disable_signup=false`;
`AUTH_SKIP_CONFIRMATION=true` if it had `mailer_autoconfirm=true`;
`APP_ORIGIN` (public origin of the app) closes sign-out to other sites.
Add to the target `create unique index on auth.users (lower(email));`: without
it, `Victim@x` and `victim@x` coexist. Rate limiting is per
instance: with several instances, a Redis counter for a global cap.

Pitfalls covered: **sign-out behind the Clever router** — `req.nextUrl`
carries the internal host and `X-Forwarded-Host` can come from the client: no
absolute URL built on a header, relative redirect; **Supabase sessions
not migrated**: sign-in again on first access, to announce; **without SMTP**,
account confirmed by default at sign-up, a gap to log at gate 1.
`SESSION_SECRET` is set with
`clever env set SESSION_SECRET "$(openssl rand -hex 32)" --alias <app> >/dev/null`:
never printed, but present on the `clever` command line for the duration of
the call (clever-tools 5.2.0 has no standard input for values).
