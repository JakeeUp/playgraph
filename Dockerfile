# PlayGraph container image. Serves both processes from one image:
#   web     -> uvicorn (the API and the /app frontend)
#   worker  -> arq (the Steam sync queue)
# The process is chosen at runtime, so both stay on identical dependencies.

FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production

WORKDIR /srv

# psycopg v3 rather than psycopg2-binary: requirements-postgres.txt already
# notes that psycopg2 falls back to compiling from source on a very new
# interpreter. v3 ships wheels and needs no pg_config at build time. The exact
# version is pinned in requirements-postgres.txt.
COPY requirements.lock requirements-postgres.txt ./
RUN pip install -r requirements.lock \
 && pip install -r requirements-postgres.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts/__init__.py scripts/seed_demo.py ./scripts/

# Never run as root. The app writes nothing to disk outside /tmp.
RUN useradd --system --uid 10001 --home /srv playgraph \
 && chown -R playgraph:playgraph /srv
USER playgraph

EXPOSE 8000

# Default to the API. fly.toml overrides this for the worker process.
CMD ["python", "-m", "app.serve"]
