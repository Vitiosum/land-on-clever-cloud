# Acceptance check — details of phase 6

Read in phase 6 to collect the matrix. The rules (strictly read-only source,
expected values, gate 3) are in `modules/supabase/GUIDE.md`, phase 6.

---

## 1. Source side: the SQL simulation

Through psql or the MCP `execute_sql`, one query per resource and per identity:

```sql
begin read only;
set local role authenticated;                 -- or anon, without claims
select set_config('request.jwt.claims',
  json_build_object('sub', '<uuid>', 'role', 'authenticated')::text, true);
select count(*) as visible,
       count(*) filter (where user_id <> '<uuid>') as others_rows
  from public.<table>;
rollback;
```

**Storage**: a policy that calls `storage.allow_any_operation` sees no row of
`storage.objects` without `set_config('storage.operation', …)` in the same
transaction (`modules/supabase/reference/storage.md` §8).

Same policy, same identity, no side effect — unlike a check through login and
writes, which modifies the source (observed: sequence 6 → 10, six sessions
created). Store the result in the format
`{"matrix": {resource: {identity: n}}}` for the comparison.

---

## 2. Target side, option 1: `modules/supabase/scripts/check-http.py`

Driven by `check-http.json` (example provided:
`modules/supabase/scripts/check-http.example.json`), which describes the
application's API: matrix, expected values, and **write checks** — modify
another user's row, write as another user, anonymous, tampered or expired
token, `CHECK`.

- Tokens are forged by the application itself (`token_cmd` command, e.g.
  `modules/supabase/templates/auth-nextjs/scripts/forge-token.mjs`), without
  a password. `token_cmd` is text read from a file, so it is restricted: only
  `node <path>/forge-token.mjs <arguments>` runs (no shell), and never from a
  `check-http.json` / `check-files.json` tracked by git — keep these files
  untracked (they hold no secret) or use `token_env`.
- `--compare source.json` outputs the mismatches; `--read-only` replays the
  matrix only.
- Complete it with the SQL simulation of §1 on the target (identity set by
  `set_config`, without `set local role`): both must match the source.
- `check-http.py` only sends JSON: files (multipart upload, SHA-256, headers,
  HTML/SVG refusal) go through `modules/supabase/scripts/check-files.py`
  (`modules/supabase/reference/storage.md` §8).

---

## 3. Supabase stack kept

`modules/supabase/scripts/sessions.py` (admin magic link, TOTP if `aal2`, no
password) then `modules/supabase/scripts/check-rls.py` (on the source: rule
of `modules/supabase/GUIDE.md`, phase 6).
