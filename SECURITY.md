# Security review and operating requirements

Reviewed 2026-09-08. This is a code review and automated test pass, not a
penetration-test certification. Public launch still has requirements below.

## What PlayGraph actually stores

The app stores one operator-owned Steam Web API key, a token-signing secret,
database/Redis credentials, Steam IDs, imported play stats, reviews, and
comments. It does not collect Steam passwords or each user's personal API key.
Steam handles authentication. OpenID identifies a user; it doesn't grant access
to private Steam data. Keep that distinction in the onboarding text.

Sources: [Steam authentication](https://partner.steamgames.com/doc/features/auth)
and [Web API key handling](https://partner.steamgames.com/doc/webapi_overview/auth).

## Findings and fixes

| Finding in the reviewed code | Protection implemented | Remaining boundary |
|---|---|---|
| Sync status was public and accepted arbitrary job IDs | Authentication plus exact owner/job matching before accessing Redis | Add the same ownership tests to every future private resource |
| Login callbacks lacked browser binding and return URL checks | Random expiring state, separate HttpOnly browser cookie, exact return URL and Steam identity namespace checks | Real Steam browser sign-in still needs a manual smoke test |
| Assertion freshness and local replay protection were missing | Required signed fields, nonce age checks, atomic one-time state consumption and nonce storage | Keep server clocks synchronized |
| Tokens lasted 30 days with no logout | 30-minute signed tokens, required issuer/audience/time/session claims, Redis session allowlist and logout revocation | No refresh tokens or session-management screen yet |
| Unlimited API requests and repeated sync attempts | Shared Redis counters: 120 requests/IP/minute, 10 login requests/IP/minute, 30 writes/user/minute, 3 sync attempts/user/hour | Edge DDoS limits and a global upstream request budget are still needed |
| Oversized request bodies could reach parsers | 64 KiB actual body limit, including chunked bodies, and 10-second body deadline | Edge must also cap connections, header sizes, timeouts and body sizes |
| Steam key appeared in URLs | Official x-webapi-key header, secret-masked settings, query-free Uvicorn access logs, hidden SQL parameter values | Don't enable HTTP header/body tracing with live credentials |
| Redis job data used pickle | Matching JSON codecs in API and worker, separate JSON queue and job ID namespace | Redis remains a trusted service and needs restricted network/credential access |
| Dependencies allowed old JWT packages | Switched to PyJWT, removed unused multipart parser, added tested dependency snapshot and CI scans | Snapshot has version pins, not artifact hashes; review and audit updates |
| Production had no separate HTTP posture | HTTPS/Redis TLS checks, exact Host allowlist, HSTS, no-store, nosniff, frame denial, API CSP, production docs disabled | Deployment proxy and TLS must be configured correctly |

The public review/comment reads are intentional. Library and genre data stay
behind `/me` authentication. A review publishes its stored verified stats.
There is no private-review feature yet. Imported Steam hours establish a
recorded value, not expertise or proof that every minute was active play.

## Research behind the review

A common API failure is checking that someone is logged in without checking
whether they own the requested object. That directly matched the old sync
status endpoint. See [OWASP object-level authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/).

OpenID requires return URL checks, provider/identity validation, nonce checks,
and verification of signed fields. Browser-bound state also prevents someone
from transplanting their login into another user's browser. PlayGraph pins
Steam instead of doing discovery against arbitrary caller-supplied hosts.
See [OpenID assertion verification](https://openid.net/specs/openid-authentication-2_0.html#verification).

Bearer tokens remain useful to anyone who steals them until they expire or
are revoked. An active server-side session record provides a logout boundary.
See [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
and [REST security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).

Letterboxd disclosed a staff-account compromise in March 2024. Its notice says
support tools exposed some members' email addresses, private lists/watchlists,
and deleted content. That motivates narrow staff permissions and audit logs,
not just better customer login. PlayGraph has no staff console today.
See [Letterboxd's notice](https://letterboxd.com/about/security-notices/).

Request limits protect application resources but cannot stop a distributed
network flood on their own. See [OWASP resource consumption](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/).

The old python-jose dependency pulled in ecdsa, which has an unpatched timing
advisory. PlayGraph's HS256 use did not invoke the affected signing operation.
Switching libraries removed that unnecessary dependency rather than suppressing
the finding. See [the advisory](https://github.com/advisories/GHSA-wj6h-64fc-37mp).

## Session behavior

Start login at `/auth/steam/login` in the same browser that receives the
callback. State expires after ten minutes. Starting a second login replaces
the browser binding, so finish the latest attempt. A failed verification may
require restarting login. No automatic retry of an already consumed assertion.

The API currently returns a bearer token as JSON for the developer workflow.
Send it in `Authorization: Bearer ...`. `POST /auth/logout` revokes that session.
Old tokens without the new claims and Redis record stop working. Logging out
does not cancel a sync already in progress. Redis loss expires sessions and
requires signing in again. Security-store failures return 503, not access.

The browser starts at `/app` and uses `/auth/steam/login?ui=1`. Login mode is
bound in Redis and the signed return URL. Success sets an HttpOnly, SameSite=Lax
session cookie and redirects to `/app`. HTTPS uses Secure and the `__Host-`
prefix. Local HTTP cookies are development-only. No bearer token is placed
in page content, URLs, localStorage or sessionStorage.

`GET /auth/session` returns identity, expiry and a session-bound HMAC CSRF token.
The UI keeps that CSRF token in memory. Cookie writes require the exact Origin
and matching X-CSRF-Token. Explicit invalid Authorization cannot fall back to
a cookie. Bearer API clients retain their existing behavior.
See [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).

Logout revokes Redis state and clears the cookie. The UI clears private records,
dialogs and timers and aborts pending requests when a session ends. Other tabs
revalidate through a credential-free BroadcastChannel signal and on focus.
Browser history restoration reloads the page. No service worker caches data.

Reviews, comments, game names, and Steam persona names are untrusted text.
The API returns JSON. The UI builds DOM nodes and renders those values as text,
never raw HTML. Its CSP permits local scripts/styles and specific Steam image
hosts without inline-script permission. Stored artwork URLs also pass an HTTPS
host allowlist. Image requests contact Steam's CDN directly and reveal the image
being requested and the visitor's IP to that host.
If Markdown is added, sanitize the rendered result with a maintained allowlist.
See [OWASP XSS prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html).

## Upgrade from the earlier development build

1. Finish any running sync, then stop both the API and worker.
2. Use a project virtual environment and install `requirements.lock`.
3. Keep the existing database. No schema changes are required in this update.
4. Keep APP_BASE_URL consistent with the browser origin, normally
   `http://localhost:8000` locally. A JWT_SECRET shorter than 32 bytes now
   stops startup. Generate a random secret with `secrets.token_hex(32)` if needed.
5. Restart both processes. The worker and API now use
   `playgraph:queue:json-v1` with `sync-json-user-{id}` jobs. Old queued pickle
   jobs aren't migrated or executed by the new worker. Re-request needed syncs.
6. Sign in again. Previous tokens aren't accepted. Swagger no longer persists
   a token across page reloads.

Don't leave the old worker running alongside the new API. The legacy queue
has intentionally not been deleted. `scripts/reset_queue.py --yes` now removes
only this app's new sync job namespace, after the worker is stopped. It leaves
security state and unrelated arq applications alone.

## Public deployment gates

- Serve HTTPS only. Set ENVIRONMENT=production and the exact public origin.
  Terminate TLS at a maintained proxy; trust forwarded headers only from that
  proxy's actual addresses, never `*`. The origin must not be publicly reachable
  around the proxy. Rate limiting uses the ASGI client IP, not raw forwarded
  headers. Verify real client attribution with two clients before launch.
- Use TLS and authenticated Redis with a dedicated account/instance, network
  restrictions, and least-privilege credentials. Redis must not be internet-open
  without access restrictions. Validate required EVAL and GETDEL support.
  Monitor memory and prefer noeviction for security state with TTLs.
- Keep database credentials scoped to PlayGraph, use encrypted transport where
  remote, protect storage/backups, and rehearse restore. Add Alembic migrations
  before schema-changing features. create_all is not a migration system.
- Inject secrets from the hosting secret store. No real secrets in images,
  source, browser bundles, screenshots, logs, or CI variables for pull requests.
  Use separate dev/prod keys. Gitignore is not a secret scanner. Enable GitHub
  secret scanning and push protection. A tracked .env path wasn't found in
  current files or its available git history; this was not an exhaustive
  historical secret scan.
- Require MFA on GitHub, hosting, Redis and any future staff identities. Grant
  support staff only the actions they need. Log privileged reads and writes,
  restrict exports, and require reauthentication for destructive operations.
- Add privacy choices, user data export/deletion, report/block flows, moderation
  roles and an abuse response process before opening a social community.
- Add edge request/connection limits, an upstream daily-call budget, alerting
  for errors and suspicious rates, and a way to disable syncs during an incident.
  IP limits can affect people behind shared networks and need observed tuning.
- Configure proxy/application error reporting to redact Authorization, Cookie,
  x-webapi-key, X-Clerk-Token, callback query strings, request bodies and connection URLs.
  The app logs login/logout IDs and rejection/rate-limit events without tokens;
  collection, alerting, access controls and retention are hosting work.
- Review the CI result before merge and require it in branch protection.
  The workflow and Dependabot config only take effect after they are committed
  and pushed. GitHub account/repository security settings were not changed here.
- Run a real Steam login, logout and library sync smoke test on an HTTPS staging
  deployment, then an independent security review before public launch.

Sources: [OWASP secrets management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html),
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use),
and [arq serializer requirements](https://arq-docs.helpmanual.io/#custom-job-serializers).

## Verification and limits

Local tests exercise hostile callbacks, replay, session expiry/revocation,
claim/signature checks, cross-account denial, secret placement, queue codec
rejection, request limits, and existing review/genre behavior. Test databases
are in-memory and test configuration overrides inherited production settings.
Real Steam and hosted Redis were not contacted by tests.

Browser changes add negative tests for login-mode tampering, missing or foreign
CSRF/Origin values, cross-session CSRF, cookie revocation, Authorization precedence,
catalog bounds and CSP. Results: 90 Python tests passed, one optional real-Redis
test skipped, and five JavaScript data tests passed. These are not browser
layout or real Steam end-to-end acceptance evidence.

Phase 1B adds public review-feed reads and authenticated personal ranking.
Responses expose existing public review/game fields and comment counts, not raw
personal libraries or Steam account IDs. Ranking input comes from the current
session's game snapshots. SQL input is bound, pagination is capped and the
personalized candidate set is capped at 500. Software is excluded before ranking.
Feed/dialog state clears on session changes, and user text is still rendered
through textContent. Existing comment/review writes retain CSRF and ownership checks.
Current results are 98 passing Python tests, one skipped Redis test, and seven
passing JavaScript tests. Live browser interaction checks remain open.

Clerk account creation and Steam beta conversion are implemented for local
testing. Conversion checks recent signed factor ages, including a second factor
when enrolled, and performs an uncached provider-status check. Missing proof is
rejected. Existing identities are never merged, and a converted account cannot
authenticate through Steam alone. Provider factor/recovery acceptance remains
open; implementation and synthetic tests are not a security certification.
ACCOUNT_PLAN.md defines the managed identity boundary and linking threats. A
platform data link must never bypass the PlayGraph account's second factor.
No PSN/Nintendo/Epic browser-session cookies or platform passwords are collected.

A separate Redis integration test runs only with TEST_REDIS_URL set to a local
server. CI provisions an isolated Redis service for atomic Lua/GETDEL and an
arq JSON worker round trip. It is skipped on this Windows machine, which lacks
a local Redis server. The GitHub workflow has not run yet.

The earlier 2026-09-07 audit of the 35-package dependency snapshot returned no
known advisories. That earlier Bandit run reported no actionable findings;
its hardcoded-password check is
locally suppressed only for the public protocol label "bearer". Those tools
do not prove the absence of vulnerabilities or validate hosting controls.

## If a credential leak is suspected

Stop the affected capability, revoke/rotate the exposed credential at its
provider, replace the hosting secret, and restart affected processes. Rotating
JWT_SECRET invalidates all signed sessions. Check access logs and scope the
exposure before restoring service. Preserve evidence securely and follow the
incident response and user-notification process. Do not post exposed values
in a public issue or commit them as proof.
