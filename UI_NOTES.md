# First browser UI

Local implementation, 2026-09-08. FastAPI serves `/app` and `app/static`.
No separate frontend build, schema migration or new API credentials are needed.
This is a responsive web app, not a native mobile binary.

Phase 1B update: rating input is now a pointer/touch half-star slider with native
range keyboard controls and a clear action. Software has its own shelf and usage
summary; gaming totals and preferences exclude it. For You ranks real reviews
from other users, Latest includes all recent game reviews, and review links open
a dedicated public discussion using existing comments.

Automated results on 2026-09-09: 104 Python tests pass, one real-Redis test
is skipped, and seven JavaScript data/URL/rating tests pass. Desktop browser
acceptance with synthetic data passed as recorded below; touch/mobile checks remain.

Live Phase 1B HTTP checks confirm 322 games plus 4 software apps (Blender, DSX,
ShareX and Wallpaper Engine), preserving all 326 entries. The review feed and new
assets respond successfully. It has no game reviews yet; publishing a game review
will populate Latest. For You excludes the viewer's own reviews.

## Design references

These are design judgments from the public sites, not usability-study results.

| Reference | Useful pattern | Decision for PlayGraph |
|---|---|---|
| [Letterboxd](https://letterboxd.com/about/faq/) | Poster browsing, ratings, and separate logging/reviewing actions | Browse public catalog covers, then open details and reviews. Keep verified hours beside reviews. |
| [Backloggd](https://backloggd.com/about/) | Game collection filters, reviews, lists and social discovery | Make library search and actual played/unplayed filters work first. Manual backlog statuses need their own persistence API. |
| [Steam library artwork](https://partner.steamgames.com/doc/store/assets/libraryassets) | Consistent portrait assets | A 2:3 cover grid with a compact list alternative. |

A busy social feed would compete with finding games in this first version.
The default screen puts personal library and playtime first. Explore clearly
describes the imported catalog. Snapshot dates are not diary dates; achievement
completion is not story completion. Genre totals overlap, which the chart states.
The publish form explains public review attribution and verified stats.

## Artwork and metadata

The public Steam image convention needs no extra key:
`https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg`.
It returned image responses for Dota 2, Counter-Strike 2, Elden Ring, Cyberpunk
2077, Baldur's Gate 3, Hades and Horizon Zero Dawn during research. This is an
observed path, not a guaranteed API contract.

Tiles try that portrait, an existing allowlisted header URL, then a title
placeholder. Images load lazily. The worker does not currently populate headers,
so a missing portrait normally reaches the title fallback. Artwork remains with
its respective rights holders; public access does not transfer ownership.

Steam's public appdetails endpoint can provide headers and more metadata.
Some headers use hashes, so don't build portrait URLs from header paths. A
future integration should use a server cache, fixed hosts, numeric app IDs,
timeouts and an upstream request budget. No per-tile API lookup was added.

[IGDB](https://api-docs.igdb.com/) is a candidate for richer metadata and games
outside Steam. It requires Twitch application credentials and limits requests
to four per second; its commercial partnership route requires contacting IGDB.
[RAWG](https://rawg.io/apidocs) requires a key and backlinks. Its current free
pricing table is non-commercial and conflicts with older commercial wording
on the same page. Recheck terms when integrating. Neither is configured here.

## Run and test

1. Run `dev.bat` from the project. Open `http://localhost:8000/app`. Keep the API
   and worker running and use the same configured origin throughout sign-in.
2. Browse the public catalog before login. Connect Steam; confirm it returns
   to `/app` and loads your existing library without token copying.
3. Search beyond game 200, filter genres and played/unplayed, sort, switch
   cover/list view, and load more. Counts must match the selected filters.
4. Sync once. Confirm queued/running states stop on success/failure and success
   refreshes the data. Reload while running to check polling resumes.
5. Open a game and publish a review, then a comment. Try duplicate reviews,
   blank comments, unavailable network and comments spanning several pages.
6. Sign out with a dialog open and a second tab open. Private data must clear.
   Returning through browser history must revalidate the session.
7. Check keyboard focus, Escape and 360px phone layout. Test long game/persona
   names and missing artwork. No horizontal overflow.

Earlier Phase 1A evidence: 90 Python tests passed, one real-Redis test was skipped,
and five JavaScript data/URL tests passed. The current results supersede those counts.

The real local API and worker started successfully. `/app`, its scripts/styles,
and the catalog returned HTTP 200; the existing catalog contains 326 games.

## Phase 1B desktop acceptance, 2026-09-09

Ran the actual FastAPI app and assets on loopback port 8002 with an isolated SQLite
database, dummy credentials and an in-memory Redis double. Two synthetic accounts,
322 game records, four software records, more than 20 reviews for one game and
21 comments exercised pagination. Steam and ARQ execution were not tested.
The fixture server and sign-in helper live only in D:\Code\playgraph-verify;
they must never be copied into the product or used with real data.

Observed in the browser:

- Software shelf shows Blender, DSX, ShareX and Wallpaper Engine; its 2,000 usage
  hours and 60 achievements stay separate from the library's 2 hours and 2 achievements.
- Mouse drag reaches 0.5 and 5 stars. ArrowRight, End, Clear and required-rating
  validation work. A 4.5-star review publishes with the expected verified stats.
- The user's review remains visible after closing/reopening despite ranking below
  the first 20 public results. The composer is absent for an existing review.
- HTML-like review text stays literal. No script element or handler is created.
- For You excludes the signed-in author's reviews; Latest includes them. Library
  match reasons display. Genre matching has API coverage.
- Posting a feed comment increments its count. Open discussion and Copy link work;
  a second tab opens the same review with its comment. Loading comment page two
  gives 21 comments, and posting after pagination succeeds.
- Show more expands 48 items to 96; search finds game 322. Missing covers show titles.
- Played shows only Hades; unplayed plus Adventure yields 161 games. Adding the
  game-322 search yields one result. Title/achievement sorts and list/grid modes work.
- Sign-out clears private summaries and open dialogs in both tabs. Escape closes
  dialogs and the focused star slider has a visible outline. Browser error/warning
  logs were empty at the end of these flows.

Fixes from review and verification:

- Added a session-scoped own-review lookup so UI ownership never depends on public
  pagination. It cannot select a different author's review via a query parameter.
- Personal feed pages retain their initial library snapshot cutoff as well as
  their review cutoff. Imports during pagination no longer skip or duplicate posts.
- Oversized resource IDs and feed anchors return validation errors before reaching
  the database. Regression tests also cover the 500-review candidate boundary.
- Corrected software count/search/usage labels and raised phone navigation text to 12px.

Still open: real Steam login/logout/sync, hosted Redis integration, real-device touch
and mobile layout. The browser viewport
override remained at 1280 by 720, so no phone-layout pass is claimed. PHASES.md keeps
these gates open. Public release requires the privacy and operating gates in
DEVELOPMENT_PLAN.md; accounts and MFA remain Phase 2 work.
## September 16 review/profile increment

The detail dialog now uses a poster column and a main column with Community reviews,
Your review and Details tabs. Only metadata actually present in the catalog is shown.
Game links resolve through a public metadata endpoint. Review edit/delete controls
are scoped to the signed-in user's own review; changes refresh a visible review feed.
Unverified posts use a neutral label rather than the green verified-data treatment.

Observed in an isolated browser fixture using synthetic users: saved review/rating
prefill, keyboard rating change and save, literal rendering of HTML-like text,
unchanged verified hours and achievements, edit cancellation, explicit delete warning
and cancellation, comments retained after edits, tab keyboard controls, clipboard
game link and direct-link loading, and separate own-review panels for two users.
At a 390px browser viewport the dialog's scroll width equaled its client width.
This is not physical-phone or real Steam acceptance. Destructive deletion and rollback
were checked by isolated API tests; no real review or comment was changed.
