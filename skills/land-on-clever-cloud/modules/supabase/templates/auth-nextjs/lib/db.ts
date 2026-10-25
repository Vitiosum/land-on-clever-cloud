// Option 1 authentication template (Next.js App Router) — verified on 2026-10-25
// on a real migration (accounts and hashes migrated, encrypted PostgreSQL add-on).
// Dependencies: pg, bcryptjs, jose (+ @types/pg in dev).
import { Pool, type PoolClient } from 'pg'

// Native variable of the linked add-on, read directly (never copied).
const url = process.env.DATABASE_URL || process.env.POSTGRESQL_ADDON_URI

// pool x instances <= connections of the plan (xxs_tny / xxs_sml: 45), margin included.
export const pool = new Pool({ connectionString: url, max: Number(process.env.PG_POOL_MAX || 10) })

/**
 * Identity set PER TRANSACTION (never per session: the pool would reuse it).
 * auth.uid() reads request.jwt.claim.sub; storage.operation() reads storage.operation.
 * Without an identity: no rows (FORCE ROW LEVEL SECURITY), never an error.
 */
export async function withIdentity<T>(
  sub: string | null,
  operation: string | null,
  fn: (c: PoolClient) => Promise<T>
): Promise<T> {
  const c = await pool.connect()
  try {
    await c.query('begin')
    await c.query(
      "select set_config('request.jwt.claim.sub', $1, true), set_config('storage.operation', $2, true)",
      [sub ?? '', operation ?? '']
    )
    const r = await fn(c)
    await c.query('commit')
    return r
  } catch (e) {
    await c.query('rollback').catch(() => {})
    throw e
  } finally {
    c.release()
  }
}
