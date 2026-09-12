# PlayGraph

Letterboxd for games, except the reviews actually mean something.

You link your Steam account and PlayGraph pulls your real playtime and
achievement data. When you review a game, the review carries your verified
hours on it, so a review from someone with 200 hours reads differently than
one from someone who played it for 20 minutes and bounced.

That's the whole reason I'm building it. Game logging apps already exist.
None of them tie a review to proof you played the thing.

## Stack

FastAPI, SQLAlchemy 2.x, Alembic, and arq on Redis for background jobs.
SQLite for local development, Postgres for deployment. The frontend is plain
ES modules served by FastAPI, so there's no build step.

## What works

- Sign in through Steam (OpenID 2.0, verified server side)
- Full library sync as a background job, with progress polling
- Genre breakdown by playtime, with software kept out of gaming stats
- Reviews carrying playtime and achievements frozen at the moment of writing
- Comments and review feeds
- Responsive UI at `/app`: covers, search, filters, drag half-star ratings

## What isn't built

- Recommendations
- PlayGraph's own sign-up, passkeys and MFA (provider setup is pending)
- Any platform beyond Steam

## Running it locally

You need Python 3.11 or newer and a Redis instance.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Fill in `.env`. You need a free Steam Web API key from
https://steamcommunity.com/dev/apikey, and a signing secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Then apply migrations and start both processes:

```bash
python -m app.migrations upgrade
uvicorn app.main:app --reload      # API on http://localhost:8000
arq app.worker.WorkerSettings      # sync worker, separate terminal
```

On Windows, `dev.bat` starts both and `stop.bat` stops them.
The app is at http://localhost:8000/app.

## Tests

```bash
python -m pytest -q
```

The suite covers Steam OpenID verification, session and CSRF handling, rate
limits, the queue codec, migrations, and the review and feed endpoints.

## Notes

A few things worth knowing if you're reading the code:

- Signing in through Steam is OpenID 2.0, not OAuth. There's no client secret
  and no token exchange. Steam redirects back with signed query params and you
  POST them back to Steam to ask whether it really signed them. Skip that round
  trip and anyone can hand the server a made up SteamID.
- A large library sync takes several minutes, because achievements are one API
  call per game. That can't sit inside an HTTP request, so `POST /me/sync`
  queues a job and returns an id you poll.
- Playtime is stored as append-only snapshots rather than a single mutable
  number, so a review can freeze the hours it was written with.

More detail lives in [SECURITY.md](SECURITY.md) for the security review,
[MIGRATIONS.md](MIGRATIONS.md) for database upgrades and recovery, and
[PLATFORM_PLAN.md](PLATFORM_PLAN.md) for why only Steam is wired up so far.

## Status

Personal project, actively being built. Not deployed publicly yet.
