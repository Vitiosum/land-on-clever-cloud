# Storage → Cellar — file migration

Read this as soon as the survey counts at least one bucket. The short rules
are in `modules/supabase/GUIDE.md` (phases 1, 2, 5 and 6); here, the detail
and the code. The facts come from two successful cold tests (same source
project, two agents, SHA-256 digests checked independently); the source of
each point is at the end of this sheet.

---

## 1. Storage survey

`survey-supabase.sh` puts two keys in `survey.json`:

| Key | Content | What it decides |
|---|---|---|
| `buckets` | per bucket: `public`, `limit_bytes` (`file_size_limit`), `mime_types` (`allowed_mime_types`), objects, volume, largest object | constraints to reproduce on upload, name of the Cellar bucket |
| `storage` | totals, `mime_types` present, **`active_types`** (HTML, SVG, XML, JS already stored), `without_owner`, `multipart_etag`, policies of `storage.objects` per command, **`policies_by_operation`** | serving security (§7), acceptance (§8), copy duration |

- **The database is the truth, not the repository**: `allowed_mime_types` and
  `file_size_limit` are read in `storage.buckets`. Observed: the
  `supabase/config.toml` declared `allowed_mime_types = ["image/*"]`, the
  database said `null` (everything accepted). The project's **global limit** is
  not in the database: read it in the console (Storage, Settings).
- **The policies** of `storage.objects` are in the `policies` key (table
  `storage.objects`). `policies_by_operation > 0`: they depend on
  `storage.operation()` (`storage.allow_any_operation(...)`), which changes the
  SQL simulation (§8) and the app code (§2).
- **The object list** does not go into `survey.json`:
  `survey-supabase.sh --objects > objects.json` (psql) or `--objects-query`
  passed to `execute_sql` (MCP). Beyond a few thousand objects through MCP,
  paginate on the key (`where (bucket_id, name) > (…) order by bucket_id, name
  limit 5000`) and concatenate. Never through the API: `/object/list` returns `[]`
  anonymously.
- **Object names can be personal data**
  (`invoice-dupont.pdf`): `objects.json` and the manifest stay in the
  client's workspace, outside the repository; the deliverable quotes their summary.

Object count and volume go into the runbook (phase 3): time
`migrate-storage.py` on a sample (`--only`, `seconds` in the
manifest) and extrapolate, as for the dump.

---

## 2. Decision: bytes in Cellar, metadata and rights in the database

**Recommended (track 1)**:

- the **bytes** in a **Cellar** add-on (S3);
- `storage.buckets` and `storage.objects` **recreated in the database** (minimal
  schema), with the **source policies copied as is** and
  `storage.operation()` rewritten (§5);
- the **application** replaces the Storage API with two or three routes:
  download (row visible under the identity with `storage.operation =
  'object.get_authenticated'`, then Cellar read), upload (`INSERT` into
  `storage.objects` under the identity, so the source `WITH CHECK`, then
  Cellar `PUT`, in the same transaction; **without `RETURNING`**, §9),
  listing (`object.list`).

Why: **one Cellar key opens every bucket of the add-on**
(`modules/supabase/reference/clever-cloud.md` §2); fine-grained access must stay in
PostgreSQL, as at Supabase. The rights matrix is then replayed in SQL on both sides
(observed: 26 identical cells).

The other track observed — rights rewritten in the code (name prefixed by the
owner's id, writes limited to the prefix) — also works, but each
policy becomes code to review and the matrix can no longer be replayed in SQL. Keep
it only for two or three trivial rules, and write it down at gate 1.

**Not portable as is** (find them in the code, decide):
image transformations (`modules/supabase/reference/clever-cloud.md` §4 imgproxy), signed URLs
`createSignedUrl` (Cellar pre-signed URLs: not tested here), resumable
TUS uploads (not tested). **HDS**: Cellar exists in `parhds` (the only
HDS zone this skill proposes); Cellar has no `encryption` option (`clever addon
providers show`): a required encryption is done in the application.

---

## 3. Export from the source, without `service_role`

The source is only **read** (GET): no login (it creates sessions and tokens),
no upload.

1. **Publishable key** in `apikey`:
   `GET /storage/v1/object/authenticated/<bucket>/<object>` answers **200 without
   a session** if a SELECT policy allows it (common case of avatars);
   `/object/public/<bucket>/<object>` for a public bucket (on a private
   bucket, it answers 400). `migrate-storage.py --route auto` tries one
   then the other and records the one that worked.
2. **Otherwise** (no policy allows reading): ask the client for **a key**
   (`sb_secret_…`, passed through a variable: `--key-env`, revoked after the
   cutover) or **an export** (`DIR/<bucket>/<object>`:
   `--source-dir DIR`). Neither goes into a deliverable.

---

## 4. The Cellar bucket

- **Global names** (across all accounts): `avatars` is taken. Prefix
  (`<project>-avatars`), and pass the prefix to the app
  (`CELLAR_BUCKET_PREFIX`); S3 rules: 3 to 63 characters, lowercase,
  digits, `-`, `.` — a Supabase id with an uppercase letter or `_` is renamed
  (`--bucket src=dest`). `HEAD` of the bucket: 403 = name taken elsewhere (or
  credentials refused).
- **Provisioning creates the add-on, not the bucket**:
  `modules/supabase/templates/deploy-target.sh` creates it (step 3: `migrate-storage.py
  --buckets <ids> --prefix <prefix> --create-buckets`, credentials read with
  `clever addon env`, without an app), or `migrate-storage.py --create-buckets`
  with the object list, or an idempotent `CC_PRE_RUN_HOOK`
  (`HeadBucket`, otherwise `CreateBucket`) so that a recreated environment
  repairs itself. Created private: a direct anonymous `GET` answers 403 (observed
  in path style and in vhost style).
- **Signature**: SigV4, region **`us-east-1`**; `CELLAR_ADDON_HOST` is a
  host **without scheme** (prefix it with `https://`):
  `curl -q --aws-sigv4 "aws:amz:us-east-1:s3" -K <(printf 'user = "%s:%s"\n' "$CELLAR_ADDON_KEY_ID" "$CELLAR_ADDON_KEY_SECRET") "https://$CELLAR_ADDON_HOST/<bucket>/<object>"`
  (credentials through `-K` from `printf`, a shell builtin: never in
  the argv of `curl`, visible with `ps`).
- **Recent `aws` CLI** (observed in 2.23+): `put-object` fails
  (`argument of type 'NoneType' is not a container or iterable`) without
  `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` and
  `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required`. JS SDK v3: same
  settings (`requestChecksumCalculation` / `responseChecksumValidation:
  'WHEN_REQUIRED'`) and `forcePathStyle: true`.

---

## 5. Metadata and identity

**Minimal schema**: `storage.buckets` (`id`, `name`, `public`,
`file_size_limit`, `allowed_mime_types`…) and `storage.objects` (`bucket_id`,
`name`, `owner`, `owner_id`, `metadata` with `size`, `mimetype`, `eTag`,
`cacheControl`), `unique (bucket_id, name)`, RLS **and** `FORCE`. The rows
are imported with the data, in the transaction of `modules/supabase/reference/data-migration.md` §2:

```sql
alter table storage.objects no force row level security;
insert into storage.objects (id, bucket_id, name, owner, owner_id, created_at, updated_at, metadata, version, user_metadata)
  select id, bucket_id, name, owner, owner_id, created_at, updated_at, metadata, version, user_metadata
    from jsonb_populate_recordset(null::storage.objects, (:'export'::jsonb)->'objects');
alter table storage.objects force row level security;
```

**Storage functions**: `storage.allow_any_operation` is copied as is
(`pg_get_functiondef` on the source); `storage.operation()`, set at
Supabase by the Storage API, is rewritten and the app sets it per transaction:

```sql
create or replace function storage.operation() returns text language sql stable as
$$ select current_setting('storage.operation', true) $$;
-- app: select set_config('request.jwt.claim.sub', $1, true), set_config('storage.operation', 'object.get_authenticated', true);
```

**Two identity keys**: the source-side simulation sets
`request.jwt.claims` (JSON), the app sets `request.jwt.claim.sub`. Without both,
"the same simulation on the target" returns 0 everywhere. `auth.uid()` therefore reads
both, as Supabase does (`modules/supabase/reference/data-migration.md` §1):

```sql
create or replace function auth.uid() returns uuid language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid $$;
```

On the add-on there is only one role: no `set local role` in the
target simulation.

---

## 6. Migration order

**accounts → data → trigger → files**, then acceptance:

1. `auth.users`, `auth.identities` (foreign keys);
2. business tables, `storage.buckets`, `storage.objects`, `setval` — one
   transaction, `NO FORCE` → import → `FORCE`;
3. **the `on_auth_user_created` trigger (`handle_new_user`) after the import**:
   set before, it creates an empty profile for each imported account, and the
   profile import fails on the primary key;
4. the **bytes**: `migrate-storage.py`, last because it is the longest,
   resumable (`--resume manifest.json` skips the PASS ones) and idempotent.
   Hot cutover: list again after the write freeze and run again with
   `--resume` for the delta.

```bash
survey-supabase.sh --objects > objects.json        # or --objects-query (MCP)
migrate-storage.py objects.json --prefix acme- --dry-run
migrate-storage.py objects.json --prefix acme- --create-buckets --manifest storage-manifest.json
```

---

## 7. Security: the files change origin

At Supabase, files are served from `*.supabase.co`. Served by
the application, they are served **from its domain**: an uploaded HTML or SVG
becomes a **stored XSS** (session cookies, API calls on behalf of the
victim). Worse when the source accepts anonymous uploads and
`allowed_mime_types = null` (observed). Found by a review, not by the
skill: always apply it.

Every response that serves a file carries:

- `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: default-src 'none'; sandbox`;
- `Content-Disposition: inline` **only** for `image/png`, `image/jpeg`,
  `image/webp`, `image/gif`; everything else as
  `attachment` + `Content-Type: application/octet-stream`;
- an **escaped** file name (RFC 6266): never an unescaped quote, `\` or
  line break — an ASCII fallback and the encoded form:

```ts
const name = path.split('/').pop() || 'file'
const ascii = name.replace(/[^\w.\-]/g, '_')
const enc = encodeURIComponent(name).replace(/['()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase())
headers['Content-Disposition'] = `attachment; filename="${ascii}"; filename*=UTF-8''${enc}`
```

On upload: **refuse** `text/html`, `application/xhtml+xml`, `image/svg+xml`,
`text/xml`, `application/xml`, **and** the extensions `.html`, `.htm`,
`.xhtml`, `.svg`, `.svgz`, `.xml` (415), whatever
`allowed_mime_types` says; then apply the bucket's `allowed_mime_types` and
`file_size_limit`, read in the database. Objects of these types already stored
(`active_types`) are migrated, but only served as attachments.
This is a deliberate gap from the source: log it at gate 1.

Alternatives not tested here: a dedicated cookie-less domain for files,
or Cellar pre-signed URLs with `response-content-disposition`.

---

## 8. Acceptance: SHA-256 proof and rights

**Three-step proof**, object by object:

| Step | Measure | Tool |
|---|---|---|
| 1. source | SHA-256 of the download; size and md5 = `metadata` of `storage.objects` (`eTag`, except multipart upload `…-N`) | `migrate-storage.py` |
| 2. Cellar | SHA-256 **re-read** after the `PUT` = step 1 | `migrate-storage.py` (manifest, PASS/FAIL) |
| 3. the app | SHA-256 served by the application, under each read identity = step 1 | `check-files.py` |

Plus a digest of the metadata, the same on both sides:
`md5(string_agg(concat_ws('|', bucket_id, name, owner_id, metadata->>'size', metadata->>'mimetype'), E'\n' order by bucket_id, name))`.

**Rights on the source side: read only.** Over HTTP: `GET
/object/authenticated/…` per object with the publishable key, `/object/public/…`,
anonymous `/object/list` (`[]`). In SQL: when a policy calls
`storage.allow_any_operation`, the simulation sees **no row** of
`storage.objects` (0 everywhere, which would look like a loss) **unless** the
operation is set:

```sql
begin read only;
set local role authenticated;
select set_config('request.jwt.claims', json_build_object('sub', '<uuid>', 'role', 'authenticated')::text, true),
       set_config('storage.operation', 'object.get_authenticated', true);   -- or object.list
select count(*) as visible, count(*) filter (where owner_id <> '<uuid>') as of_others from storage.objects;
rollback;
```

Useful matrix rows: `<bucket>_get` (`object.get_authenticated`),
`<bucket>_list` (`object.list`), `<bucket>_updatable` (the `USING` of the
UPDATE policy). No upload on the source, not even a refused one.

**The UPDATE policy is tested with an UPDATE, on the target only.** An
upload by another user on an existing name returns 409 (`(bucket_id, name)` uniqueness):
it proves nothing about the UPDATE (observed). Over HTTP: the
`replacement` section of `check-files.py`. Without a replacement route, in SQL,
in a rolled-back transaction:

```sql
begin;
select set_config('request.jwt.claim.sub', '<uuid of bob>', true),
       set_config('storage.operation', '<operation of the UPDATE policy>', true);
update storage.objects set updated_at = updated_at where bucket_id = 'avatars' and name = '<object of alice>';
rollback;   -- expected: UPDATE 0; the same under alice's uuid: UPDATE 1
```

The positive control (alice: `UPDATE 1`) is mandatory: a `WHERE` reads the
row, so the SELECT policy applies too, and a wrongly set operation
gives `UPDATE 0` to everyone, which would look like a refusal.

**Target side**: the same simulation (without `set local role`), then
`check-files.py check-files.json` (example:
`modules/supabase/scripts/check-files.example.json`): manifest re-read, headers of
§7, upload of a PNG re-read, HTML/SVG/XML refused (415), someone else's file refused,
path traversal, Cellar bucket in direct access (403).

---

## 9. `FORCE ROW LEVEL SECURITY` pitfalls tied to Storage

- **The `security definer` trigger is subject to the policies.** At Supabase,
  `handle_new_user()` runs as `postgres` (`BYPASSRLS`); on the add-on,
  as the owner under `FORCE`: its `INSERT` goes through the policy
  (`with check (auth.uid() = id)`) and sign-up fails without an identity.
  The app generates the id, **sets the identity in the sign-up transaction**,
  then inserts into `auth.users`:

```sql
begin;
select set_config('request.jwt.claim.sub', '<id of the new account>', true);
insert into auth.users (id, email, encrypted_password, …) values ('<id of the new account>', …);
commit;
```

- **No `RETURNING` under `FORCE` when the SELECT policy depends on the
  operation**: `INSERT … RETURNING` (and `ON CONFLICT DO UPDATE`) requires
  the new row to also pass a SELECT policy; with
  `storage.operation = 'object.upload'`, the source SELECT policy
  (limited to `object.get_authenticated` by `storage.allow_any_operation`)
  refuses it and the upload fails ("new row violates row-level security
  policy", observed). The app generates `id` and `version` itself and expects
  nothing back; if the row must be read again, use a separate query with
  the read operation.
- **The trigger is set after the import** (§6).
- **A purge without a DELETE policy deletes 0 rows, without error**: test accounts and
  test objects are not removed, and nothing reports it. Purge as the
  owner in a `NO FORCE` → `delete … returning` →
  `FORCE` transaction, check the row count, then delete the Cellar object;
  `check-files.py` lists the objects it created.

---

## Provenance

| Point | Opus test (metadata in the database) | Sonnet test (rights in the code) |
|---|---|---|
| Export through `/object/authenticated` with the publishable key, `/list` = `[]` | observed | observed (fact checked) |
| `config.toml` ≠ database (`allowed_mime_types`) | observed | — |
| Global Cellar names, prefix; bucket not created by provisioning | observed | observed |
| SigV4 `us-east-1`, host without scheme | — (SDK and CLI in region `default`) | observed |
| `aws` CLI: checksum variables | observed | — |
| Metadata and policies in the database, `storage.operation()`, SQL matrix 26/26 | observed | — |
| Stored XSS, headers, refusal on upload | found in review, fixed, 43/43 | `nosniff` + `sandbox` from the start |
| SQL simulation blind without `storage.operation` | worked around by setting it | observed |
| Trigger under `FORCE`, identity at sign-up | observed | observed |
| Trigger after the import | observed | — |
| Purge without a DELETE policy = 0 rows | checked (0 rows) | observed |
| GUC `claims` / `claim.sub`, `coalesce` | observed | — |
| SHA-256 source = Cellar = app | 2/2 objects | 2/2 objects |

`migrate-storage.py` and `check-files.py` bring these two acceptance runs together.
Real proof of 2026-10-25: `migrate-storage.py` 2/2 PASS from a real Supabase
project to a real Cellar (SHA-256 checked independently);
`check-files.py` 35/35 and `check-http.py` 15/15 against the production
app run locally on the real add-ons. **The app did not run on
Clever** (application creation limit); the `replacement` section and
`--buckets` are checked offline only (fake app, fake S3).
