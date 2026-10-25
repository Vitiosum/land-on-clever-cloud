// Cookie session: HS256 JWT signed with SESSION_SECRET (jose). No sessions
// table: Supabase sessions cannot be migrated (users log in again on first
// visit, to be announced).
// SESSION_SECRET is REQUIRED (at least 32 bytes): without it, the server
// refuses to start (instrumentation.ts) and every signature throws an error.
//   clever env set SESSION_SECRET "$(openssl rand -hex 32)" --alias <app> >/dev/null
// (the value is on the clever command line for the duration of the call:
// clever-tools 5.2.0 has no standard input for values.)
import { SignJWT, jwtVerify } from 'jose'
import { cookies } from 'next/headers'

export const COOKIE = 'session'

export function checkSecret(): Uint8Array {
  const s = process.env.SESSION_SECRET || ''
  if (Buffer.byteLength(s) < 32) throw new Error('SESSION_SECRET missing or too short (32 bytes minimum)')
  return new TextEncoder().encode(s)
}

export type Session = { sub: string; email: string }

export async function signSession(s: Session): Promise<string> {
  return new SignJWT({ email: s.email })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(s.sub)
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(checkSecret())
}

export async function verifySession(token: string | undefined | null): Promise<Session | null> {
  if (!token) return null
  try {
    const { payload } = await jwtVerify(token, checkSecret(), { algorithms: ['HS256'] })
    return payload.sub ? { sub: payload.sub, email: String(payload.email ?? '') } : null
  } catch {
    return null // tampered or expired token: treated as anonymous
  }
}

export async function getSession(): Promise<Session | null> {
  return verifySession((await cookies()).get(COOKIE)?.value)
}

export const cookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === 'production',
  sameSite: 'lax' as const,
  path: '/',
  maxAge: 3600,
}
