# Catalog coverage and platform integrations

Research status: 2026-09-08. **Only Steam is currently implemented.**
Catalog coverage, identity linking and imported play data are separate capabilities.
A login button does not prove that a provider exposes libraries, hours or achievements.

## Broad game coverage comes first

Use a backend IGDB integration for cross-console titles, platforms, releases,
descriptions and artwork. It needs a Twitch application Client ID/Secret and
OAuth token; no credentials are configured yet. Cache token and metadata
responses, respect its four-requests-per-second limit, and confirm commercial
terms before launch. Coverage is broad, not a promise of every release ever made.
Source: [IGDB documentation](https://api-docs.igdb.com/).

Keep Game.id as the internal canonical title ID so existing reviews survive.
An Alembic migration should make steam_appid optional and add an external-ID
mapping with unique (provider, external_id), classification provenance and
refresh timestamps. Use exact external-ID matches before title matching.
Keep uncertain matches for review rather than merging editions by name alone.

Model supported hardware separately from account providers. A PC game can have
Steam and Epic releases; Xbox hardware and a Microsoft account are different
dimensions. Store canonical games, release/platform variants and store IDs
separately. Group the UI by Games/Software, platform, account source and genre.

Snapshots need source linked_account_id and release/platform identity before
multiple platform imports are combined. Preserve imported achievement IDs with
their source. Do not sum overlapping hour counts or replace Steam data with an
Xbox snapshot just because the title matches. Manual ownership, hours and
completion records must be labeled self-reported and never receive imported
verification badges.

Current IGDB external-game fields use external_game_source and platform;
several older category fields are deprecated. Verify the current schema when
implementing. Source: [IGDB external games](https://api-docs.igdb.com/#external-game).

## Connection capability matrix

| Provider | What official sources establish | PlayGraph status and remaining proof |
|---|---|---|
| Steam | OpenID identity and Web API access to available library/playtime/achievements | Implemented with the app's server key. Privacy settings and game support still limit data. |
| Xbox | Website OAuth/token flow and current-user achievement services are documented | Evaluate next. App registration, scopes and end-to-end access are needed. Full library and universal playtime coverage are not established by sign-in alone; title history is incomplete. |
| Battle.net | Consumer OAuth and selected game profile/achievement APIs | Feasible for supported games. No verified whole-launcher library or universal playtime import; do not promise one. |
| Epic Games | Epic Account Services and product/sandbox APIs for commerce and achievements | Evaluate with a registered product. These docs do not establish access to every owned Epic title, all achievements or lifetime hours. |
| PlayStation | Official partner/developer programs exist | Public self-service third-party consumer library/trophy/hour access is unverified. Confirm approval and supported scope before implementation. |
| Nintendo | Official developer registration and platform development programs exist | Public consumer library/achievement/hour export access is unverified. Do not equate developer access with permission to read arbitrary player accounts. |

Primary sources: [Steam authentication](https://partner.steamgames.com/doc/features/auth#website),
[Steam player statistics](https://partner.steamgames.com/doc/webapi/ISteamUserStats),
[Xbox website authentication](https://learn.microsoft.com/en-us/gaming/gdk/docs/services/fundamentals/s2s-auth-calls/service-authentication/live-website-authentication),
[Battle.net OAuth](https://develop.battle.net/documentation/guides/using-oauth),
[Battle.net profile APIs](https://develop.battle.net/documentation/guides/profile-apis),
[Epic Account Services](https://dev.epicgames.com/docs/epic-account-services),
[PlayStation Partners](https://partners.playstation.net/),
[Nintendo Developer Portal](https://developer.nintendo.com/).

Do not collect platform passwords, private browser cookies, PSN session values
or launcher refresh tokens copied by users. Unofficial scraping can break,
violate provider rules and create a much larger credential-theft risk. Lack of
documented access should lead to an honest unavailable/manual mode, not a hidden
scraper presented as a supported connection.

## Concrete implementation order

1. Finish the local rating/software/feed acceptance checks.
2. Add migrations and PlayGraph identity/MFA with explicit platform-link ownership.
3. Add cached IGDB search/import, canonical release mapping and source-aware snapshots.
4. Test Xbox website authentication and one supported Battle.net game as bounded
   integration proofs. Record exactly which library/achievement/hour fields work.
5. Add supported adapters behind capability flags. Show missing, private and
   unavailable data explicitly. Never turn missing data into zero achievements.
6. Seek appropriate Epic, PlayStation and Nintendo access before offering sync.
7. Test unlinking, revoked consent, expired tokens, provider outages and cross-user
   access. Encrypt any retained provider token with a separate managed encryption
   key; limit scopes, redact logs, rotate keys and delete tokens on unlinking.

## Current software classification

Steam store pages explicitly identify [DSX](https://store.steampowered.com/app/1812620/DSX/),
[ShareX](https://store.steampowered.com/app/400040/ShareX/) and
[Wallpaper Engine](https://store.steampowered.com/app/431960/Wallpaper_Engine/) as software.
Wallpaper Engine's observed appdetails response still says type=game. The local
classifier therefore uses explicit app IDs plus software-only genre tokens,
shared between SQL filtering and API serialization. Existing records are retained.

This is a heuristic, not definitive provider metadata. Add persisted classification
and refresh provenance later. Valve's documented IStoreService/GetAppList has
separate include_games/include_software filters and supports pagination and
incremental updates; validate membership and budgets before a background import.
Source: [IStoreService](https://partner.steamgames.com/doc/webapi/IStoreService).
