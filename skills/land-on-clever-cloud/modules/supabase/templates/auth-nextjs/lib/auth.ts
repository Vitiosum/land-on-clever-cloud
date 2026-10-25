// Accounts migrated from auth.users: the bcrypt hashes ($2a$) in encrypted_password
// are verified as they are by bcryptjs, no password to reset.
import bcrypt from 'bcryptjs'
import { randomUUID } from 'node:crypto'
import { pool, withIdentity } from './db'

// Valid dummy hash, computed at startup: an unknown e-mail costs the same
// time as a wrong password. BCRYPT_COST must be the cost of the MIGRATED hashes,
// read in the survey (auth.password_hash_cost, e.g. {"06": 3} for $2a$06$…;
// GoTrue projects are often 10, but not always — never assume it). New
// accounts (createUser) use the same cost.
const BCRYPT_COST = Number(process.env.BCRYPT_COST || 10)
const DUMMY = bcrypt.hashSync(randomUUID(), BCRYPT_COST)

// GoTrue refused banned accounts and, without auto-confirmation, unconfirmed
// accounts: so does the template. AUTH_SKIP_CONFIRMATION=true only if the
// source had mailer_autoconfirm=true (survey, /auth/v1/settings): a choice
// to log at gate 1.
const CONFIRMATION_REQUIRED = process.env.AUTH_SKIP_CONFIRMATION !== 'true'

export const normalizeEmail = (email: string) => email.trim().toLowerCase()

export async function checkPassword(email: string, password: string) {
  // Read as the owner, outside any identity: auth.users without RLS (otherwise 0 rows under FORCE).
  const r = await pool.query(
    `select id, email, encrypted_password from auth.users
      where lower(email) = $1 and deleted_at is null
        and (banned_until is null or banned_until <= now())
        and ($2::boolean = false or email_confirmed_at is not null)
      limit 1`,
    [normalizeEmail(email), CONFIRMATION_REQUIRED]
  )
  const u = r.rows[0]
  const ok = await bcrypt.compare(password, u?.encrypted_password || DUMMY)
  return ok && u ? { sub: u.id as string, email: u.email as string } : null
}

/**
 * Signup: the identity of the new account is set INSIDE the transaction,
 * otherwise the handle_new_user trigger (security definer, subject to the
 * policies under FORCE) is refused and signup fails. No RETURNING: the id is generated here.
 * E-mail normalized to lower case (like GoTrue); plan the unique index
 * `create unique index on auth.users (lower(email))` (modules/supabase/reference/data-migration.md §3).
 * Without SMTP, the account is confirmed right away (email_confirmed_at = now()): a gap to
 * log at gate 1 if the source required confirmation.
 */
export async function createUser(email: string, password: string) {
  const id = randomUUID()
  const mail = normalizeEmail(email)
  const hash = await bcrypt.hash(password, BCRYPT_COST)
  await withIdentity(id, null, async (c) => {
    await c.query(
      `insert into auth.users (id, email, encrypted_password, email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
       values ($1, $2, $3, now(), '{"provider":"email","providers":["email"]}', '{}')`,
      [id, mail, hash]
    )
    await c.query(
      `insert into auth.identities (id, user_id, provider, provider_id, identity_data)
       values ($1, $2::uuid, 'email', $4::text, jsonb_build_object('sub', $4::text, 'email', $3::text, 'email_verified', true))`,
      [randomUUID(), id, mail, id]
    )
  })
  return { sub: id, email: mail }
}
