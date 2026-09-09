# PlayGraph

Letterboxd for games, except the reviews actually mean something.

You link your Steam account and it pulls your real playtime and achievement
data. It shows you what you actually play, broken down by genre. When you
review a game, the review carries your verified hours on it, so a review from
someone with 200 hours reads differently than one from someone who played it
for 20 minutes and bounced.

That last part is the whole reason I'm building it. Game logging apps already
exist. None of them tie a review to proof you played the thing.

## Where it's at

Working:
- Sign in through Steam
- Full library sync, running in the background
- Genre breakdown by playtime
- Reviews with playtime and achievement verification frozen at creation
- Comments under reviews
- Browser-bound Steam login, short revocable API sessions and request limits
- Responsive UI at `/app`: Steam covers, library search, filters and playtime stats
- Game details with review and comment forms
- HttpOnly browser sessions, CSRF protection and sign-out
- Draggable half-star ratings, with touch and keyboard support
- Separate Software shelf and usage totals; apps stay out of gaming stats
- For You and Latest review feeds, with shareable review discussions

Not built yet:
- Recommendations
- Standalone PlayGraph sign-up, passkeys, email links and MFA (provider setup pending)
- Cross-console catalog imports and platform connections beyond Steam

## Steam first

Steam is the implemented importer today. Broader catalog coverage will be
separate from account connections. Xbox documents a website authentication flow;
other providers offer different levels of access, often limited to particular
products or approved developers. PLATFORM_PLAN.md records the current evidence
and gaps. A provider login does not guarantee a complete library or playtime API.

One thing that caught me off guard: "Sign in through Steam" is OpenID 2.0,
not OAuth. There's no client secret and no token exchange. Steam redirects
back with a pile of signed query params, and you POST those params back to
Steam to ask whether it really signed them. That verification round trip is
the only thing making this authentication instead of a suggestion. Skip it
and anyone can hand my server a made up SteamID and log in as whoever they
want.

Worth noting the login and the API access are two separate systems. Logging
in tells me who you are. The API key is mine, tied to the app, and it's what
actually fetches your data afterward.

## How the sync works

Large library syncs can take several minutes. `GetPlayerAchievements` takes
one appid per call. The current worker checks played games sequentially and
pauses between requests. Cached genres skip that work on later syncs. The
worker's estimate assumes uncached genres and can overstate the time remaining.
Connection reuse, measured concurrency and better estimates are performance
backlog items, not a fixed Steam requirement.

13 minutes doesn't fit in an HTTP request. So `POST /me/sync` drops a job on
a queue and hands back a job id immediately, and you poll
`GET /me/sync/status/{job_id}` to watch it go.

I used arq for the queue. Celery is the usual answer but it's a lot of setup
for one job type. FastAPI ships BackgroundTasks, but those run inside the API
process and disappear if it restarts, which is useless for something running
this long.

Stuff I hit building it:

- The first version logged nothing between start and finish. A 13 minute job
  that prints nothing looks identical to a hung one. It logs every 10 games
  now, plus an up front estimate so you know whether to wait.
- arq's default job timeout is 300 seconds. My sync was dying at exactly 5
  minutes, halfway through the library, and the error said TimeoutError with
  no hint about which timeout.
- arq also defaults to 10 concurrent jobs. Two syncs writing the same tables
  got me "database is locked", which sent me looking at SQLite when the
  actual problem was upstream. Set it to 1. Concurrency wasn't buying
  anything since the whole thing is rate limit bound, not CPU bound.
- Double clicking the sync button started two jobs. The job id is fixed per
  user now, so arq refuses the second one and the endpoint returns a 409.
- Biggest speedup was noticing most of a Steam library is unplayed. A game
  with 0 minutes has nothing unlocked, so that achievements call can only
  ever come back empty. Skipping those cut the sync roughly in half.

Genre tags come from a different API than everything else, the store one, and
it's rate limited harder. I only look up a game's genres once ever and store
them on the game itself, so that cost gets paid the first time anyone syncs a
game and never again, even for a different user.

## How I count genres

A game tagged Action, RPG and Indie counts its full playtime toward all
three. I'm not splitting it evenly across tags.

That means genre totals add up to more than my real playtime, which looks
broken until you think about it. Almost every game carries several tags, so
splitting would understate every single genre at once. The question the graph
answers is "how much time have you spent on games with X in them", and full
credit is the honest answer to that one.

There's a test pinning this down so nobody comes along later and "fixes" it.

## Data model

- `User` is the app account
- `LinkedAccount` is a platform identity attached to a user. It's a separate
  table so adding Xbox later is a new row, not a migration that reshapes
  users
- `Game` is one canonical game, keyed on Steam's appid, shared across users
- `PlaytimeSnapshot` is a point in time capture of your playtime and
  achievements for one game. Append only, so a re-sync adds a row instead of
  overwriting. That means I can chart playtime over time later, not just show
  a running total
- `Review` stores a rating and verified stats at write time. `Comment` belongs
  to one review and its author

The append only thing has a catch worth knowing: every query that wants
"current" playtime has to pull the newest snapshot per game, not just any
snapshot, or games get counted twice. That lives in one helper both endpoints
share.

## Running it

```bash
cp .env.example .env
```

Fill in `STEAM_API_KEY` (free at https://steamcommunity.com/dev/apikey) and a
`JWT_SECRET`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Local dev uses SQLite, so there's no database to install. You do need Redis
for the job queue. A free hosted one works fine, just paste its `rediss://`
URL into `REDIS_URL`. There's a docker-compose.yml if you'd rather run
Postgres and Redis locally, in which case also install
`requirements-postgres.txt`.

```bash
python -m pip install -r requirements.lock
dev.bat
```

`dev.bat` opens two labelled windows, one for the API and one for the sync
worker. Running them by hand got old fast and I kept Ctrl+C-ing the wrong
terminal without noticing the worker had died.

If a window gets closed badly and the API comes back with "address already in
use", `stop.bat` clears whatever is still holding port 8000.

I tried honcho first, which runs both from the Procfile in a single window.
It starts fine but its Ctrl+C handling throws InterruptedError on Windows with
Python 3.14 and can orphan the child processes, so two native windows it is.
On Mac or Linux `honcho start` works properly and is nicer.

Either way the Procfile stays, because it is also the format Railway, Render
and Heroku read to figure out what to run. Same two lines describe local dev
and deployment.

To run them separately:

```bash
python -m uvicorn app.main:app --reload    # the API
python -m arq app.worker.WorkerSettings    # the sync worker, separate terminal
```

Open **http://localhost:8000/app**. Choose **Connect Steam**, finish Steam's
sign-in, then choose **Sync Steam**. Keep the API and worker running. An existing
synced library appears after sign-in without another import.

Use the exact origin configured in `APP_BASE_URL`, normally `localhost`,
throughout sign-in. Browser sessions last 30 minutes. Connect Steam again when
one expires. No token copying, npm install or separate frontend server is needed.
This is a responsive browser app; native mobile packaging is future work.

API docs remain at http://localhost:8000/docs. `/auth/steam/login` still returns
a bearer token for developer tools. The browser uses `/auth/steam/login?ui=1`,
which sets an HttpOnly session cookie and redirects to `/app`.

If the queue gets into a weird state with stale jobs retrying:

```bash
python scripts/reset_queue.py --yes
```

## Tests

```bash
python -m pytest
node tests/test_library_ui.mjs
```

The default tests run against an in memory SQLite database, with no Steam or
hosted Redis calls. An optional localhost Redis integration test runs in CI. They cover genre aggregation, review verification and snapshot isolation,
validation, authentication, duplicate reviews, ordering, and comments.

## Reviews and comments

Use a game's internal `id` from `/me/library`, not its Steam appid.
`POST /games/{game_id}/reviews` accepts a rating from 0.5 to 5 in half star
steps, plus an optional body of up to 10,000 characters. Sign in first.
There's one review per user per game. A duplicate returns 409.

Verified minutes and achievement percentage come from your latest snapshot
for that game when you write the review. Later syncs don't change the review.
Without a snapshot, both fields are null and the review is unverified. Zero
minutes is a known value, not proof of meaningful play. Missing achievement
data or zero total achievements leaves the percentage null.

`GET /games/{game_id}/reviews` is public and sorts by verified minutes,
highest first, with unverified reviews last. Ties use newest review first.

`POST /reviews/{review_id}/comments` accepts a nonblank body of up to 5,000
characters and requires sign in. `GET /reviews/{review_id}/comments` is public
and returns oldest first. Comments form a flat thread under each review;
nested replies aren't supported yet.

Both list routes accept `limit` (1 to 100, default 20) and `offset` (default 0).
Missing games or reviews return 404. Review and comment creation return 201.

## Known rough edges

- Tables get created from the models on startup. That's fine now because I
  can throw the database away, but it needs Alembic before there's real data
- Genres are a comma separated string on the game row. Good enough for one
  user's breakdown, but if I ever want to ask cross user questions like
  average hours per genre, that wants its own table and a join
- The genre aggregation happens in Python, not SQL, for the same reason
- Review editing/deletion, backlog statuses, diary entries and social feeds remain
- Catalog browsing covers imported games, not the complete Steam catalog
- Missing Steam covers use an available stored header, then a title placeholder

## Security and next steps

Read [SECURITY.md](SECURITY.md) before deploying or upgrading the API and worker.
It lists implemented protections, test limits and the remaining launch gates.
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) defines the sprint sequence,
acceptance criteria, and release gates for continued development.

Steam handles the user's password. PlayGraph uses its own server-side Steam
API key to import available data. It does not ask users for personal API keys.
Tokens now expire after 30 minutes and POST /auth/logout revokes the current
session. Start login in the same browser that receives the callback. Old tokens
stop working. Swagger no longer saves bearer tokens across reloads.

Cookie-authenticated browser writes require both a session-bound CSRF token and
the configured Origin. Authentication credentials stay out of JavaScript storage.
Reviews and comments are public; the form explains which verified stats are shared.

[PHASES.md](PHASES.md) shows our current phase and completion gates.
[ACCOUNT_PLAN.md](ACCOUNT_PLAN.md) covers PlayGraph accounts, MFA and provider setup.
[PLATFORM_PLAN.md](PLATFORM_PLAN.md) distinguishes game catalog coverage from
account linking and verified import support.
[UI_NOTES.md](UI_NOTES.md) records the design sources, artwork choice and local
acceptance steps. Real Steam browser acceptance is still required for this update.

`GET /games` now defaults to games. Use `kind=software` for apps or `kind=all`
for the complete imported catalog. `/me/library` preserves all records by default
and accepts the same filter. `/me/genres` defaults to games. Software is classified
using explicit known app IDs and conservative software genre tokens; Steam's
appdetails type alone is not reliable. This needs a versioned metadata source later.

`GET /feed` returns recent public game reviews. `/me/feed` requires a session
and ranks the latest 500 other-player reviews by library/genre overlap and recency.
Both paginate with a `before` anchor. `/reviews/{id}` exposes a public discussion
entry; `/app#review={id}` opens it in the UI. The feed uses real comments and
does not include software reviews, fabricated popularity or a follow graph.

Set ENVIRONMENT=production and an HTTPS APP_BASE_URL for deployment. Redis must
use TLS and authentication in production. The API accepts only the configured
host. Configure trusted proxy addresses explicitly at the ASGI server.

The JSON queue upgrade requires restarting both API and worker after active
syncs finish. Old queued jobs aren't automatically migrated. Re-request syncs
using the new API. No database migration is required by this security update.
