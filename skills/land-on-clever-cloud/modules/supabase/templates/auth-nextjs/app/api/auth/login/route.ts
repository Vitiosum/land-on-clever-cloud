import { NextResponse } from 'next/server'
import { checkPassword, normalizeEmail } from '@/lib/auth'
import { allow, readJsonBody, clientIp } from '@/lib/ratelimit'
import { COOKIE, cookieOptions, signSession } from '@/lib/session'

// POST {email, password} -> 200 + session cookie, 401 (same response whether
// the e-mail exists or not, whether the account is banned or unconfirmed), 429
// beyond 10 attempts per quarter hour (per e-mail and per address).
export async function POST(req: Request) {
  const b = await readJsonBody(req)
  if (!b || typeof b.email !== 'string' || typeof b.password !== 'string' || b.password.length > 200)
    return NextResponse.json({ error: 'invalid credentials' }, { status: 401 })
  const quarter = 15 * 60 * 1000
  if (!allow('login:mail:' + normalizeEmail(b.email), 10, quarter) || !allow('login:ip:' + clientIp(req), 30, quarter))
    return NextResponse.json({ error: 'too many attempts' }, { status: 429 })
  const s = await checkPassword(b.email, b.password)
  if (!s) return NextResponse.json({ error: 'invalid credentials' }, { status: 401 })
  const res = NextResponse.json({ ok: true, id: s.sub })
  res.cookies.set(COOKIE, await signSession(s), cookieOptions)
  return res
}
