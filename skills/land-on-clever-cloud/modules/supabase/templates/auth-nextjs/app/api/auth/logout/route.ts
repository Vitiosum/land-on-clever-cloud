import { NextResponse } from 'next/server'
import { COOKIE } from '@/lib/session'

// RELATIVE redirect (/login): behind the Clever router, req.nextUrl carries
// the internal host (localhost:8080), and X-Forwarded-Host can come from the
// client: never build an absolute URL from a header. The browser resolves
// /login against the origin it displays.
// APP_ORIGIN (e.g. https://app.example.com), if set: a request from another
// origin is refused (forced logout from another site).
export async function POST(req: Request) {
  const origin = req.headers.get('origin')
  if (process.env.APP_ORIGIN && origin && origin !== process.env.APP_ORIGIN)
    return NextResponse.json({ error: 'forbidden' }, { status: 403 })
  const res = new NextResponse(null, { status: 303, headers: { Location: '/login' } })
  res.cookies.delete(COOKIE)
  return res
}
