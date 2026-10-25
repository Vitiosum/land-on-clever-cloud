// In-memory rate limiting, per instance: GoTrue limited attempts, so the
// template must too (brute force, and bcrypt costs CPU on a nano instance).
// Per instance only: with N instances, the real cap is N times higher; for a
// global cap, use a Redis counter.
const windows = new Map<string, { start: number; n: number }>()

export function allow(key: string, max: number, windowMs: number): boolean {
  const t = Date.now()
  const w = windows.get(key)
  if (!w || t - w.start > windowMs) {
    windows.set(key, { start: t, n: 1 })
    if (windows.size > 10000) for (const [k, v] of windows) if (t - v.start > windowMs) windows.delete(k)
    return true
  }
  w.n += 1
  return w.n <= max
}

// Behind the Clever router, the client address is the first element of
// X-Forwarded-For (set by the router). Outside this router, the header is
// supplied by the client: do not trust it.
export function clientIp(req: Request): string {
  return (req.headers.get('x-forwarded-for') || '').split(',')[0].trim() || 'unknown'
}

// Bounded JSON body (10 KB): a huge body must not tie up the instance.
export async function readJsonBody(req: Request): Promise<Record<string, unknown> | null> {
  const n = Number(req.headers.get('content-length') || '0')
  if (n > 10_000) return null
  const text = await req.text().catch(() => '')
  if (text.length > 10_000) return null
  try {
    const v = JSON.parse(text)
    return v && typeof v === 'object' ? v : null
  } catch {
    return null
  }
}
