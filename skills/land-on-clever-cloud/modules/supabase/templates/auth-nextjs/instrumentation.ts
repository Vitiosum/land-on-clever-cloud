// Checks the configuration when the Next.js server STARTS (register() is
// called once, before any request): without a valid SESSION_SECRET, the
// instance refuses to start and the Clever deployment fails visibly, instead
// of a "successful" login that never yields a session.
export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    const { checkSecret } = await import('./lib/session')
    checkSecret()
  }
}
