// Forges an acceptance-check session token (HS256) with the app's SESSION_SECRET:
// no password. It is the token_cmd of check-http.json / check-files.json.
// usage: SESSION_SECRET=... node scripts/forge-token.mjs <uuid> [email]
import { SignJWT } from 'jose'
const [sub, email = ''] = process.argv.slice(2)
if (!sub || !process.env.SESSION_SECRET) { console.error('usage: SESSION_SECRET=... node forge-token.mjs <uuid> [email]'); process.exit(2) }
const t = await new SignJWT({ email }).setProtectedHeader({ alg: 'HS256' }).setSubject(sub)
  .setIssuedAt().setExpirationTime('30m').sign(new TextEncoder().encode(process.env.SESSION_SECRET))
console.log(t)
