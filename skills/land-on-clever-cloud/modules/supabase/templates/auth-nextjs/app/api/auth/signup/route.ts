import { NextResponse } from 'next/server'
import { createUser } from '@/lib/auth'
import { allow, readJsonBody, clientIp } from '@/lib/ratelimit'
import { COOKIE, cookieOptions, signSession } from '@/lib/session'

// Signup CLOSED by default: SIGNUP_ENABLED=true opens it, only if the source
// had it open (disable_signup=false in /auth/v1/settings).
// POST {email, password} -> 200 + cookie, 400 invalid, 403 closed, 409 exists,
// 429 beyond 5 signups per hour and per address.
export async function POST(req: Request) {
  if (process.env.SIGNUP_ENABLED !== 'true') return NextResponse.json({ error: 'signup disabled' }, { status: 403 })
  if (!allow('signup:ip:' + clientIp(req), 5, 60 * 60 * 1000))
    return NextResponse.json({ error: 'too many attempts' }, { status: 429 })
  const b = await readJsonBody(req)
  if (!b || typeof b.email !== 'string' || !/^[^@\s]{1,64}@[^@\s]{1,190}$/.test(b.email.trim())
      || typeof b.password !== 'string' || b.password.length < 8 || b.password.length > 200)
    return NextResponse.json({ error: 'invalid' }, { status: 400 })
  try {
    const s = await createUser(b.email, b.password)
    const res = NextResponse.json({ ok: true, id: s.sub })
    res.cookies.set(COOKIE, await signSession(s), cookieOptions)
    return res
  } catch (e: unknown) {
    if ((e as { code?: string }).code === '23505') return NextResponse.json({ error: 'exists' }, { status: 409 })
    throw e
  }
}
