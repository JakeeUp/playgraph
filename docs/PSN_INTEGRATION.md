# PlayStation integration (verified public profile)

Status (2026-10-07): client, data model (migration 0007), routes
(`app/routers/psn.py`), worker task (`sync_psn_library`) and account/library
UI are implemented, with mocked tests in `tests/test_psn_client.py`,
`tests/test_psn_link.py`, `tests/test_psn_sync.py`, `tests/test_migrations.py`
and `tests/test_psn_ui.mjs`. **Nothing here has run against live Sony
servers.** See "Implemented, and where it differs from this design" at the end.

## Product decision and how it fits PLATFORM_PLAN.md

A user types their PSN Online ID. PlayGraph shows a one-time code
(`PLAYGRAPH-XXXXXX`). The user puts it in their PSN profile "About Me" and
clicks Check. PlayGraph reads that profile with **one server-owned NPSSO
token** (the site owner's, `PSN_NPSSO`) and links the account if the code is
there. Syncs then read that account's public trophy list, plus per-title play
time when the player's privacy settings allow it.

PLATFORM_PLAN.md forbids collecting *users'* PSN session values or tokens.
This design keeps that rule: users only prove control of public profile text.
It does depend on Sony's **undocumented internal APIs** used by the
PlayStation App. The UI must say so plainly and present PSN data as "public
PSN profile data", not as a Sony-sanctioned connection. Update the
PlayStation row of the capability matrix in PLATFORM_PLAN.md when this ships.

## Config fields (app/config.py)

```python
psn_npsso: SecretStr = SecretStr("")  # server account NPSSO; empty disables PSN
```

Optional, if pacing needs tuning without a deploy:

```python
psn_request_budget: int = 200            # requests per rolling window, per process
psn_budget_window_seconds: float = 900.0 # the rolling window (15 minutes)
psn_min_request_interval: float = 0.5    # minimum gap between request starts
```

These fields exist. `settings.psn_enabled` (true when `PSN_NPSSO` is
non-empty) is the only thing the frontend sees, via `/auth/session`.
`PSNClient()` reads `settings.psn_npsso` and the three pacing settings
when they are not passed in. An empty value raises `PSNAuthError` before any
request. Add `PSN_NPSSO=` (empty) to `.env.example` if one exists; the real
value goes only in the ignored `.env`. Use a **dedicated PSN account** for the
server, not the owner's main account (PSNAWP warns heavy use can get the
authenticating account banned). Generating a new NPSSO for that account
invalidates the previous one immediately (PSNAWP README), so there must be
exactly one deployed value.

## Client API (app/services/psn.py)

| Call | Returns | Raises |
|---|---|---|
| `resolve_profile(online_id)` | `{"account_id", "online_id"}` | `ValueError` (bad ID format, no request), `PSNNotFoundError` |
| `get_profile(account_id)` / `get_about_me(account_id)` | `{"online_id", "about_me", "avatar_url"}` / text | `PSNNotFoundError`, `PSNPrivateError` |
| `check_verification(account_id, code)` | bool | as above |
| `get_trophy_titles(account_id)` | list of `{np_communication_id, np_service_name, name, platforms[], icon_url, earned{bronze,silver,gold,platinum}, defined{...}, progress, trophy_set_version, last_updated}` | `PSNPrivateError` when trophies are hidden |
| `get_played_titles(account_id)` | list of `{title_id, concept_id, concept_title_ids[], name, image_url, category, platform, play_minutes, play_count, first_played, last_played}`, or **None** when privacy hides it | `PSNNotFoundError`, auth errors |
| `get_trophy_lists_for_titles(account_id, title_ids)` | `{titleId: [npCommunicationId, ...]}` for played titles (malformed IDs skipped, 5 per request) | `PSNPrivateError` |
| `parse_duration_minutes(s)` | int minutes, or None if malformed (never 0 for garbage) | - |
| `generate_verification_code()`, `verification_matches(about_me, code)` | code / bool (case-insensitive, code must not be part of a longer token) | - |

Every method can also raise `PSNAuthError` (server NPSSO rejected: operator
must act), `PSNRateLimitedError` (429) and `PSNError` (anything else). Token
values and the NPSSO never appear in exception messages.

Behavior worth knowing:
- Access token and refresh token are cached in process memory (`TokenState`,
  shared by all `PSNClient` instances) and renewed 60 s before expiry. A 401
  triggers one renewal and retry. A rejected refresh token falls back to the
  NPSSO. The cache resets if the configured NPSSO changes.
- Requests run against a budget: at most 200 per rolling 15 minutes per
  process, starting at least 0.5 s apart. Inside the budget they burst; once
  it is spent, the next request waits for the oldest one to age out. PSNAWP
  self-limits to 300 per 15 minutes, and the API and the worker each keep their
  own budget and token cache, so the two together can reach 400. In practice
  the API makes only a few calls (link start and check). If several workers
  run, move the budget to Redis.
- A 429 pauses every request in the process for Sony's `Retry-After`, or 600 s
  when Sony gives none. A pause of 30 s or less is slept through and the
  request retried once. A longer one raises `PSNRateLimitedError` carrying
  `retry_after`, and the worker defers the job by that much.
- Title-to-trophy-list lookups send 5 IDs per request. psn-api documents 5,
  and a live check in October 2026 confirmed it: 5 IDs returned 200, 6 returned
  400 with code 2240513. If Sony rejects a batch with that code, the client
  halves the batch size for the rest of the process and retries the same IDs.
- Pass `client=` a shared `httpx.AsyncClient` to reuse connections within a
  sync. The NPSSO is sent as a per-request `Cookie` header, never put in the
  client's cookie jar.

## Data model proposal (one Alembic migration)

### Platform enum

Add `Platform.psn = "psn"`. Postgres needs `ALTER TYPE ... ADD VALUE`; SQLite's
`Enum` is a CHECK/VARCHAR, so check how the baseline migration created it.

### LinkedAccount

Reuse the existing table, which already enforces one account per platform per
user and one user per platform account:
- `platform = psn`, `platform_user_id = accountId` (the stable numeric ID, not
  the Online ID: Online IDs can be changed by the player).
- Add `display_handle VARCHAR NULL` for the current Online ID (refresh it on
  every sync from `get_profile`). Steam rows can leave it null or store the
  persona name.
- Add `verified_at TIMESTAMPTZ NULL` and `verification_method VARCHAR NULL`
  (`"psn_about_me"`).

### Game rows for PSN titles

`games.steam_appid` is `NOT NULL UNIQUE` today. Make it nullable (unique still
allows many NULLs on Postgres and SQLite). Do **not** add more provider
columns to `games`; follow PLATFORM_PLAN.md and add a mapping table:

```
game_external_ids
  id, game_id FK games.id, provider VARCHAR ("steam" | "psn_concept" | "psn_title" | "psn_trophy"),
  external_id VARCHAR, created_at, updated_at
  UNIQUE (provider, external_id)
```

Mapping rules:
- Primary PSN identity is the **concept ID** (`concept.id` from gamelist). One
  concept groups all regional and PS4/PS5 title IDs of a game. Store one
  `psn_concept` row per Game, and a `psn_title` row for each `titleId` seen.
- Trophy lists are keyed by `npCommunicationId` (`NPWRxxxxx_00`). Trophy
  titles do not carry a title ID or concept. Link them with
  `GET trophy/v1/users/{accountId}/titles/trophyTitles?npTitleIds=CUSA..,PPSA..`
  (the recording sends 3 title IDs per call; the maximum is unknown; returns `npTitleId` ->
  `trophyTitles[].npCommunicationId`). Then store a `psn_trophy` row on the
  same Game. A PS4 and PS5 version of one concept can have two different
  trophy lists; both map to the one Game.
- PS3 and Vita trophy lists have no gamelist entry or concept (gamelist covers
  PS4/PS5 only). Create a Game from the trophy list alone, keyed only by
  `psn_trophy`, flagged for later IGDB matching.
- Never merge a PSN game into a Steam Game by name. Same name on both stores
  becomes two Game rows until an exact external-ID match (IGDB external games)
  or an owner review links them. This is the rule PLATFORM_PLAN.md already sets.
- `classify_content(steam_appid, genres)` in the Game insert listener must
  accept `steam_appid=None`. Gamelist only returns `ps4_game` and
  `ps5_native_game` when asked; media apps are filtered out client side too.

### Source-aware snapshots

Add to `playtime_snapshots`:
- `linked_account_id INTEGER NULL FK linked_accounts.id` (backfill Steam rows
  from the user's Steam link, then make it NOT NULL in a later migration).
- `source VARCHAR NOT NULL DEFAULT 'steam'` (`steam` | `psn`). Denormalized
  so queries need no join.
- `playtime_minutes` becomes **nullable**. PSN trophy-only games, and every
  game when gamelist is private, have unknown play time. NULL means unknown,
  0 means verified zero. The reviews router already treats a missing snapshot
  as unverified; extend that to NULL minutes.
- Trophy breakdown: `trophies_bronze`, `trophies_silver`, `trophies_gold`,
  `trophies_platinum` (earned) and the matching `_total` columns, all
  nullable, plus `trophy_progress` (Sony's own percentage). Keep filling
  `achievements_unlocked/total` with the summed counts so existing views work.

Readers must group by `source` (or linked account). Never sum Steam and PSN
minutes for one Game; show them as separate lines. The library and the review
"verified" badge pick the snapshot for the source the review was written
against.

## Verification flow (routes, mirroring app/routers/accounts.py)

All routes require `get_current_user` (cookie session). That dependency
already enforces the Origin header and `x-csrf-token` on POST/DELETE, and the
per-user write rate limit. Add a router `app/routers/psn.py`, prefix
`/auth/psn`.

1. `POST /auth/psn/link` body `{"online_id": "..."}`
   - 404 if `settings.psn_npsso` is empty (feature off).
   - 409 if the user already has a PSN link.
   - `rate_limit(request, "psn-link", str(user.id), 5, 600)`.
   - `validate_online_id`, then `resolve_profile` (map `PSNNotFoundError` to
     404 "No PSN account with that Online ID"; `PSNAuthError` to 503 "PSN
     sign-in is temporarily unavailable" and log for the operator).
   - 409 if that accountId is already linked to anyone (the unique constraint
     backs this up at insert).
   - Generate a code and store
     `state_key("psn-link", request.state.session_id)` ->
     `{"user_id", "account_id", "online_id", "code"}` with `ex=900`. Binding to
     the session ID means another browser or a stolen code alone cannot finish.
   - Return `{"code", "online_id", "expires_in": 900}`.
2. `GET /auth/psn` (implemented path) returns the link state (`linked`,
   current Online ID, `verified_at`, `last_synced_at`, last sync outcome) and
   the pending code for this session, or `"pending": null`.
3. `POST /auth/psn/link/check`
   - `rate_limit(request, "psn-check", str(user.id), 10, 600)`; each check is
     two Sony requests on the owner's account, so keep this tight.
   - Load the pending state for this session; 409 if missing or for another
     user.
   - `check_verification(account_id, code)`. False -> 409 "Code not found in
     your About Me yet. Save it on PSN, wait a minute, and try again."
     `PSNPrivateError` -> 409 explaining the profile must be visible.
   - After the awaited Sony call, re-check the session as `complete_link`
     does, then consume the state with `GETDEL` so only one request can link.
   - Insert the `LinkedAccount`; on `IntegrityError` roll back and 409.
   - Enqueue `sync_psn_library(user_id)`. Tell the user they can now remove
     the code from their About Me.
4. `DELETE /auth/psn/link` cancels the pending state.
5. Unlinking (`DELETE /auth/psn`) is **not implemented**. ACCOUNT_PLAN.md
   forbids unlink/replacement until snapshots carry their linked account (now
   true) **and** retention rules are defined (still open: delete PSN
   snapshots, or keep them detached?). Until the owner decides, a player can
   stop future imports by hiding trophies and play time in PSN privacy
   settings; the next sync then imports nothing new and keeps past rows.
6. Sync: `POST /me/psn/sync` and `GET /me/psn/sync/status/{job_id}` mirror the
   Steam pair in routers/library.py (job id `sync-psn-user-<id>`, rate limit 3
   per hour, 409 while a job is queued or recent).

## Worker task

`sync_psn_library(ctx, user_id)` in `app/worker.py`, registered next to
`sync_steam_library`, same locking and status reporting:

1. Load the PSN LinkedAccount; skip if absent.
2. `async with PSNClient(client=shared_httpx_client) as psn:`
3. `get_profile` to refresh `display_handle` (the player may rename).
4. `get_trophy_titles` (private -> record "trophies hidden" status, no
   snapshots, do not delete old ones).
5. `get_played_titles` (None -> play time unknown; still write trophy data).
6. Upsert Games via `game_external_ids`, linking trophy lists to concepts with
   the `titles/trophyTitles?npTitleIds=` lookup only for titles not mapped yet
   (cache the mapping; it does not change).
7. Append one snapshot per Game with `source="psn"`.
8. Error handling: `PSNAuthError` -> fail the job with an operator-facing
   message and stop all PSN jobs until the NPSSO is fixed (a Redis flag is
   enough); `PSNRateLimitedError` -> `Retry(defer=600)`; `PSNNotFoundError`
   on a linked account -> mark the link broken and ask the user to relink.
   Implemented: the flag (`app/psn_status.py`) stores a salted fingerprint of
   the rejected NPSSO for 24 hours, and the API checks it too, so a new
   `PSN_NPSSO` lifts the stop immediately. "Broken" is recorded in the user's
   Redis sync status (`problem: account_not_found`), not a column; relinking
   needs the unimplemented unlink, so the account page only reports it.

Expected cost per sync: 1 profile + ceil(trophy lists / 800) +
ceil(played / 200) + ceil(unmapped played / 5) mapping lookups. Mappings are
saved, so repeat syncs only look up new games. Measured on the first live sync
(492 played games, 479 trophy lists): about 105 requests, which took 5 min 17 s
at the old fixed 3 s pace. Under the budget, the same first sync is about
105 × 0.5 s ≈ 55 s plus Sony's response time, and a repeat sync is a few
seconds. A first sync over 200 requests (roughly 950+ played games) waits at
the window edge for the remainder. Run PSN syncs on demand plus at most daily.

## Risks

- **Terms of service.** These are private Sony APIs used with the PlayStation
  App's client credentials. Automated use is very likely outside Sony's terms.
  Sony can block it or sanction the server account at any time. Confirm the
  owner accepts this before any public deployment; PHASES.md release gates
  apply.
- **Breakage.** Endpoints, hosts and the client ID have changed before (the
  legacy `us-prof.np.community.playstation.net` host is still what both
  libraries use for Online ID lookup). Keep everything in `psn.py`, show PSN
  as unavailable on failure, and never present stale data as fresh.
- **Operator token expiry.** The refresh token lasts about 2 months (PSNAWP),
  and the NPSSO about as long (psn-api docs). Then every PSN call raises
  `PSNAuthError` until the owner signs in and sets a new `PSN_NPSSO`. Add a
  health signal: log `refresh_expires_at` and warn when under 7 days. The
  token cache is in memory, so each process restart spends one NPSSO
  exchange.
- **Privacy.** We only read data the player's settings already show to an
  unrelated PSN account. Trophy visibility and gamelist visibility are
  separate; handle each being hidden. Never store or show other people's
  profiles beyond the linked user. Unlinking waits on the retention decision
  (see route 5); until then hiding data in PSN privacy settings stops imports.
- **Verification spoofing.** The code proves control of the profile at check
  time only. Codes are random, session-bound, single-use and expire in 15
  minutes. Sony may cache profile text briefly; the UI should say "wait a
  minute and retry" rather than fail hard. (Assumption: not measured.)
- **Rate limits.** Sony publishes none. PSNAWP's 300 per 15 minutes is a
  self-imposed courtesy limit, not a known Sony quota. 429 handling exists but
  real thresholds are unknown.

## Endpoint reference (researched 2026-10-07)

Sources: psn-api source at
[achievements-app/psn-api](https://github.com/achievements-app/psn-api/tree/main/src)
(last commit 2026-08-15) and docs at
[psn-api.achievements.app](https://psn-api.achievements.app/); PSNAWP source at
[isFakeAccount/psnawp](https://github.com/isFakeAccount/psnawp/tree/master/src/psnawp_api)
(last commit 2026-10-05). Response examples come from PSNAWP's recorded
integration cassettes
([tests/integration_tests/.../cassettes](https://github.com/isFakeAccount/psnawp/tree/master/tests/integration_tests/integration_test_psnawp_api/cassettes)),
which include data from early 2026.

### 1. NPSSO -> authorization code

`GET https://ca.account.sony.com/api/authz/v3/oauth/authorize`
Query: `access_type=offline`, `client_id=09515159-7237-4370-9b40-3806e67c0891`,
`redirect_uri=com.scee.psxandroid.scecompcall://redirect`,
`response_type=code`, `scope=psn:mobile.v2.core psn:clientapp`.
Header: `Cookie: npsso=<NPSSO>`. Do not follow redirects.
Success: 302, `Location: com.scee.psxandroid.scecompcall://redirect/?code=v3.XXXX&cid=...`.
Failure: Location query has `error=...&error_code=4165` for an expired or
wrong NPSSO.
([psn-api exchangeNpssoForAccessCode.ts](https://github.com/achievements-app/psn-api/blob/main/src/authenticate/exchangeNpssoForAccessCode.ts),
[PSNAWP authenticator.py](https://github.com/isFakeAccount/psnawp/blob/master/src/psnawp_api/core/authenticator.py)).
PSNAWP sends many extra query params (`device_profile=mobile`, `smcid`, etc.);
psn-api's minimal set is what we send.
The NPSSO itself comes from `https://ca.account.sony.com/api/v1/ssocookie`
while signed in at playstation.com.

### 2. Code -> tokens, and refresh

`POST https://ca.account.sony.com/api/authz/v3/oauth/token`
Headers: `Authorization: Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A=`
(the PS App client, public in both libraries),
`Content-Type: application/x-www-form-urlencoded`.
Body (code): `code=<code>&redirect_uri=<above>&grant_type=authorization_code&token_format=jwt`
(PSNAWP also sends `scope`).
Body (refresh): `refresh_token=<rt>&grant_type=refresh_token&token_format=jwt&scope=psn:mobile.v2.core psn:clientapp`.
Response: `{access_token, token_type, expires_in, id_token, refresh_token,
refresh_token_expires_in, scope}`. Lifetimes: access about 1 hour, refresh
about 2 months (PSNAWP docstring and README). Commonly reported values are
3599 s and 5183999 s; we use whatever `expires_in` says.
([exchangeAccessCodeForAuthTokens.ts](https://github.com/achievements-app/psn-api/blob/main/src/authenticate/exchangeAccessCodeForAuthTokens.ts),
[exchangeRefreshTokenForAuthTokens.ts](https://github.com/achievements-app/psn-api/blob/main/src/authenticate/exchangeRefreshTokenForAuthTokens.ts)).

All API calls below use `Authorization: Bearer <access_token>`.

### 3. Online ID -> accountId

`GET https://us-prof.np.community.playstation.net/userProfile/v1/users/{onlineId}/profile2?fields=accountId,onlineId,currentOnlineId`
-> `{"profile": {"onlineId": "VaultTec_Trading", "accountId": "8520698476712646544"}}`.
Unknown ID -> 404 `{"error": {"code": 2105356, "message": "User not found (user: '...')"}}`.
This is what PSNAWP's `User.from_online_id` uses today (cassette
`test_user__get_profile`, `test_user__user_not_found`). psn-api suggests
`POST https://m.np.playstation.com/api/search/v1/universalSearch` with
`{"searchTerm": "...", "domainRequests": [{"domain": "SocialAllAccounts"}]}`
as an alternative; it is fuzzy search, so an exact `profile2` lookup is safer
for verification.

### 4. Profile / About Me by accountId

`GET https://m.np.playstation.com/api/userProfile/v1/internal/users/{accountId}/profiles`
-> `{"onlineId", "aboutMe", "avatars": [{"size": "s|m|l|xl", "url"}], "languages", "isPlus", "isOfficiallyVerified", "isMe"}`.
Recorded for a non-friend account with `isMe: false` and a populated
`aboutMe`. Non-existent accountId -> 400
`{"error": {"referenceId", "code": 2281473, "message": "Bad Request (path: accountId)"}}`
(treated as not found).
([getProfileFromAccountId.ts](https://github.com/achievements-app/psn-api/blob/main/src/user/getProfileFromAccountId.ts),
cassettes `test_user__get_profile`, `test_user__user_acct_id_not_found`).
The legacy `profile2` endpoint can also return `aboutMe` via `fields`.

### 5. Trophy titles

`GET https://m.np.playstation.com/api/trophy/v1/users/{accountId}/trophyTitles?limit=800&offset=0`
-> `{"trophyTitles": [...], "totalItemCount": 2070, "nextOffset": 50, "previousOffset": 49}`.
`nextOffset` is absent on the last page. Max `limit` is 800 (psn-api docs).
Item: `npServiceName` (`trophy` = PS3/PS4/Vita, `trophy2` = PS5),
`npCommunicationId`, `trophySetVersion`, `trophyTitleName`,
`trophyTitleDetail` (legacy only), `trophyTitleIconUrl`,
`trophyTitlePlatform` (comma-separated, e.g. `PS4,PSVITA`), `hasTrophyGroups`,
`trophyGroupCount`, `definedTrophies` / `earnedTrophies`
`{bronze, silver, gold, platinum}`, `progress` (0-100), `hiddenFlag`,
`lastUpdatedDateTime`. Ordered by most recent trophy.
Private -> 403 `{"error": {"referenceId", "code": 2240526, "message": "Not permitted by access control"}}`.
([getUserTitles.ts](https://github.com/achievements-app/psn-api/blob/main/src/trophy/user/getUserTitles.ts),
[trophy-title.model.ts](https://github.com/achievements-app/psn-api/blob/main/src/models/trophy-title.model.ts),
cassettes `test_user__trophy_titles_pagination_test`, `test_user__trophy_titles_forbidden`).

Title ID -> trophy list mapping:
`GET https://m.np.playstation.com/api/trophy/v1/users/{accountId}/titles/trophyTitles?npTitleIds=PPSA01506_00,CUSA12057_00`
-> `{"titles": [{"npTitleId": "CUSA12057_00", "trophyTitles": [{npCommunicationId, trophyTitleName, earnedTrophies, definedTrophies, progress, ...}]}]}`.
No pagination; only returns titles the account has played (cassette
`test_user__trophy_titles_for_title`).

### 6. Played games with play time

`GET https://m.np.playstation.com/api/gamelist/v2/users/{accountId}/titles?categories=ps4_game,ps5_native_game&limit=200&offset=0`
-> `{"titles": [...], "totalItemCount", "nextOffset", "previousOffset"}`.
Item: `titleId` (`PPSA04873_00`), `name`, `localizedName`, `imageUrl`,
`category` (`ps4_game`, `ps5_native_game`, and without the filter also
`ps5_native_media_app`, `ps5_web_based_media_app`, `pspc_game`, `unknown`),
`service` (`none(purchased)`, `ps_plus`, ...), `playCount`,
`concept {id, titleIds[], name, genres, media}`, `firstPlayedDateTime`,
`lastPlayedDateTime`, `playDuration` (`PT226H55M18S`; hours never roll into
days, seen down to `PT8M`). PS4 and PS5 only.
**Works for other users:** PSNAWP's cassette `test_user__title_stats_with_limit`
reads another account's full list. psn-api documents it "may fail if user
privacy settings prohibit listing".
([getUserPlayedGames.ts](https://github.com/achievements-app/psn-api/blob/main/src/user/getUserPlayedGames.ts),
[user-played-games-response.model.ts](https://github.com/achievements-app/psn-api/blob/main/src/models/user-played-games-response.model.ts),
[PSNAWP title_stats.py](https://github.com/isFakeAccount/psnawp/blob/master/src/psnawp_api/models/title_stats.py)).

### Errors and limits

- Error body shape on m.np.playstation.com:
  `{"error": {"referenceId": "<uuid>", "code": <int>, "message": "<text>"}}`.
- 401: bad or expired bearer. 403 + 2240526 (trophies) / 2281486 (friends):
  privacy. 404 + 2105356: unknown Online ID. 400 + 2281473: unknown accountId.
- PSNAWP maps 400/401/403/404/405/429/5xx to distinct exceptions
  ([request_builder.py](https://github.com/isFakeAccount/psnawp/blob/master/src/psnawp_api/core/request_builder.py))
  and self-limits to 1 request per 3 s (300 per 15 min), warning of possible
  account bans
  ([README](https://github.com/isFakeAccount/psnawp/blob/master/README.md)).

### Verified vs assumed

Verified from source code or recorded responses: every URL, the auth
parameters and Basic header, token response fields, pagination fields,
trophy and gamelist item shapes, `playDuration` format, gamelist working for
another account, error body shape, codes 2240526 / 2281473 / 2105356 and the
4165 NPSSO error.

Assumed or unverified:
- The exact error a **private gamelist** returns. No recording exists; we
  assume 403 like trophies. `get_played_titles` returns None on any 403.
- Whether a private-profile account's `/profiles` (About Me) is readable. If
  it is 403, linking requires the user to make the profile visible.
- That the `categories` query filter is honored (documented by psn-api, not
  used by PSNAWP). We filter locally as well.
- That `limit=200` is the gamelist maximum.
- Exact token lifetimes (we trust `expires_in`) and whether Sony caches
  About Me text.
- Sony's real rate limits.

## Implemented, and where it differs from this design

Implemented 2026-10-07. Verified only by automated tests with a fake PSN
client and fake Redis; see PHASES.md for what still needs live acceptance.

Schema (migration `0007_psn_accounts_and_sources`):
- `Platform.psn`. SQLite stores the enum as `VARCHAR(5)` with no CHECK
  constraint (checked on the owner's database schema, read only), so nothing
  changes there. **Deviation:** on PostgreSQL the `platform` type is rebuilt
  (`CREATE TYPE platform_v2 ... ; ALTER COLUMN ... USING ...; DROP; RENAME`)
  instead of `ALTER TYPE ... ADD VALUE`, because an added value cannot be used
  before commit and `app/copy_to_postgres.py` migrates and inserts in one
  transaction. The rendered PostgreSQL SQL was inspected; it has not been run
  against a PostgreSQL server.
- `linked_accounts.display_handle`, `verified_at`, `verification_method`.
- `games.steam_appid` nullable (still unique); `game_external_ids` as designed.
- `playtime_snapshots.source` (backfilled `'steam'`), `linked_account_id`
  (backfilled from the user's Steam link; stays nullable for now), nullable
  `playtime_minutes`, the eight trophy count columns and `trophy_progress`.
  The ORM column lost its Python-side `default=0`, which would have turned an
  explicit None (unknown) into 0.
- **Addition:** `reviews.verified_source` (`'steam'`/`'psn'`, existing verified
  reviews backfilled `'steam'`) so a review never presents trophies as Steam
  achievements.

Readers:
- `/me/library` returns one entry per (game, source) with `source`, nullable
  `playtime_minutes` and a `trophies` breakdown. The newest-snapshot ranking
  partitions by game and source, so one source never hides the other.
- `/me/genres` and the Steam sync's `_previous_snapshots` read Steam rows only.
  The For You feed treats NULL minutes as no preference weight. News "most
  played" skips games without a Steam app ID.
- Review verification: the newest snapshot from ONE source, Steam first, then
  PSN. PSN games are separate Game rows, so in practice a PSN game's review is
  verified from its PSN snapshot: hours when shared (NULL stays unverified),
  and the trophy percentage (earned over defined, all grades) as the
  percentage, labelled "PlayStation" and "trophies" in the UI.
- Frontend: Steam entries stay in `state.library`, so every Steam total is
  unchanged. PSN entries get their own shelf behind the existing PS5 chip
  (renamed "PlayStation" only for accounts with PSN games), their own summary
  (games, shared hours, trophies by grade) and their own lines on the game page.

Worker deviations and assumptions:
- When trophies are private, played-title snapshots are still written (the
  design said "no snapshots"); hours are useful and nothing old is deleted.
- Title-to-trophy-list lookups run only while some of the account's trophy
  lists are unmapped, only for played titles whose game has no list yet, five
  title IDs per request (Sony's maximum is unknown; the recording used three).
- PS4 and PS5 play time under one concept is added together (both are PSN
  time); `trophy_progress` is left NULL when a game has more than one list.
- A trophy list first imported as a trophy-only game (play time hidden at the
  time) stays its own Game if the concept later becomes visible. Owner review
  or IGDB matching can merge them later; nothing merges by name.
- Game art: Sony image URLs are stored in `games.header_image_url` but not
  displayed. The app only builds image URLs from Steam app IDs and its CSP
  allows only Steam hosts, so PSN games show the title placeholder cover.

