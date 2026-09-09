# PlayGraph phases

Updated 2026-09-09. **Current phase: 1B, desktop acceptance passed; final device and Steam checks open.**
The first UI is running. Drag ratings, software separation and the review feed
are implemented in this increment. Account-provider setup is the next dependency.
Keep development local until the privacy and release gates are complete.

| Phase | Outcome | Current state | Exit gate |
|---|---|---|---|
| 0. Backend foundation | Steam login, queued sync, library, genres, verified reviews and comments | Implemented; live queue and login acceptance still open | Repeatable startup and a successful real sync, review and logout |
| 1A. Usable local app | Responsive UI, covers, library, stats, Steam login, reviews and comments | Implemented; owner screenshot shows signed-in imported data | Finish baseline acceptance checks |
| 1B. Better ratings and discovery | Drag half-stars, Software shelf, For You/latest feeds, shareable review discussions | **Current: desktop browser checks passed, Verify** | Finish real-phone touch/layout and fresh Steam login/sync acceptance |
| 2. Account and data foundations | Alembic, PlayGraph identity, password/email-link/passkey choices, MFA, secure Steam linking, contribution editing | Planned; identity provider not configured | Existing IDs/data survive; account linking and MFA cannot be bypassed |
| 3. Cross-console catalog and connections | Canonical titles, IGDB metadata, platform/source mappings; supported provider adapters | Research recorded; credentials/access not configured | Cross-console games browse without account linking; imports preserve source and distinguish verified/manual data |
| 4. Personal tracking | Want to play, playing, completed, paused, dropped; diary, favorites and lists | Planned | Manual organization survives resyncs |
| 5. Privacy and account control | Visibility defaults, export, deletion and retention policy | Planned | Verify boundaries with two accounts and test deletion |
| 6. Social participation and moderation | Profiles, follows, richer feeds, blocking, reporting and moderator tools | Basic review feed exists; remaining controls planned | Privacy/blocking rules hold in feeds and direct URLs |
| 7. Hosted beta | HTTPS, secrets, backups, monitoring, load tests and independent security review | Planned | Real staging acceptance and restore rehearsal before invites |

## Phase 1 test checklist

- [x] Existing backend tests remain green.
- [x] Browser authentication has cookie, CSRF, Origin and revocation tests.
- [x] Catalog pagination, static assets and CSP have HTTP tests.
- [x] Library filters cover more than 200 games; missing artwork is handled.
- [x] Current automated results: 104 Python tests passed, one Redis test skipped;
  seven JavaScript data tests passed.
- [x] API and worker started locally. `/app`, assets and catalog return HTTP 200;
  the existing catalog contains 326 games.
- [x] Owner screenshot shows the signed-in game dialog and imported Steam hours.
- [ ] Repeat Steam login/logout acceptance after this update.
- [ ] Confirm the original 326 catalog entries are preserved across Games and Software.
- [x] Live HTTP check confirms 322 games plus 4 software apps, 326 total;
  the new assets and public feed respond successfully.
- [x] Synthetic browser library shows DSX, ShareX, Wallpaper Engine and Blender
  separately: 2,000 software hours and 60 achievements versus 2 game hours and
  2 game achievements. Backend tests cover the same separation.
- [x] Browser mouse drag from half a star to five and back, arrow keys, End,
  Clear, and rejection of a missing rating all pass. Published 4.5 stars.
- [ ] Verify touch dragging and layout on a real phone.
- [ ] Publish a real game review, find it under Latest reviews, open its discussion,
  post a comment and open the copied review link in a second tab.
- [x] Synthetic review appears in Latest, its discussion accepts a comment,
  the count updates, and its copied link opens the same discussion in a second tab.
- [x] For You excludes own posts and shows library reasons in the browser;
  backend tests cover genre reasons, a 500-review limit and stable pages across syncs.
- [x] Search finds synthetic game 322, Show more goes from 48 to 96, and
  details open. Missing artwork has a title fallback.
- [x] Browser filters combine played/unplayed, genre and search; title/achievement
  sorts and compact-list/cover-grid switching preserve the selected results.
- [ ] Request a sync; confirm the worker completes it and the UI refreshes.
- [ ] Publish a review and a comment; confirm attribution and verified stats.
- [x] Synthetic browser sign-out clears private summaries and open dialogs
  in both tabs. A second synthetic account does not see the first account's feed posts as its own.
- [x] Escape closes dialogs; rating keyboard focus is visible. Reopening a game
  shows the user's review even when it falls below the first 20 public results.

Browser evidence uses an isolated test database, dummy credentials and a Redis
double. No reviews/comments were posted to the owner's real account. The browser
viewport override did not change its size, so mobile acceptance remains open.
UI_NOTES.md records the detailed results and fixes.

No phase is complete just because its code exists. Mark acceptance checks only
after they are observed. Real Steam and hosted Redis are not used by unit tests.

## Sprint cadence

Use two-week sprints with one goal and roughly 25% capacity reserved for defects.
Track tickets as Backlog, Ready, In progress, Review, Verify and Done.
Keep one feature ticket in progress. End each sprint with a demo and a short
record of passed checks, defects and carried-forward work.

Next sprint: close Phase 1B acceptance, then begin migrations and managed account
integration. ACCOUNT_PLAN.md specifies the provider setup and security gates.
PLATFORM_PLAN.md separates catalog coverage from platform account/data access.
Sync speed remains explicit backlog work.
Resolve blocking defects before expanding the existing social feed.

DEVELOPMENT_PLAN.md contains ticket IDs and detailed release gates. UI_NOTES.md
contains the local test steps and design/asset decisions. ROADMAP.md and HANDOFF.md
remain local notes ignored by Git; this phase checklist is repository documentation.
