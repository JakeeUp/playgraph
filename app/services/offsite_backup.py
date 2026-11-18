"""Encrypted off-site database backups to Amazon S3.

A backup takes a consistent snapshot of the database, gzips it, records its
SHA-256 and uploads it with server-side encryption. The restore rehearsal
downloads the newest object into a temporary directory, checks the hash and
the database, and counts a few tables. It never touches the live database.

Credentials come only from the standard AWS chain (environment variables,
a shared profile, or an instance role). Nothing here reads, stores or logs
them. Setup steps are in docs/AWS_BACKUPS.md.
"""

import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import subprocess  # nosec B404 - fixed argv lists only, never a shell
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.migrations import sqlite_backup

log = logging.getLogger(__name__)

PREFIX = "backups/"
CHUNK = 1024 * 1024
REHEARSAL_TABLES = ("users", "games", "reviews")


class BackupError(RuntimeError):
    pass


def _settings(cfg):
    if cfg is not None:
        return cfg
    from app.config import settings
    return settings


def _ready(cfg) -> bool:
    """Demo mode refuses outright. An empty bucket means backups are off."""
    if cfg.demo_mode:
        raise BackupError("Demo mode never backs up and never contacts AWS.")
    return bool(cfg.aws_backup_bucket.strip())


def _client(cfg):
    import boto3  # Imported late so the API never loads it.
    return boto3.client("s3", region_name=cfg.aws_region.strip() or None)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(engine, directory) -> tuple[Path, str]:
    """Write a consistent copy of the database into directory.

    Returns the file and its kind: "sqlite" or "pgdump".
    """
    dialect = engine.dialect.name
    if dialect == "sqlite":
        return sqlite_backup(engine, directory), "sqlite"
    if dialect == "postgresql":
        return _pg_dump(engine.url, Path(directory)), "pgdump"
    raise BackupError(f"Off-site backup supports SQLite and PostgreSQL only, not {dialect}.")


def _pg_dump(url, directory: Path) -> Path:
    executable = shutil.which("pg_dump")
    if not executable:
        raise BackupError("pg_dump was not found on PATH. Install the PostgreSQL client tools.")
    out = directory / "playgraph.pgdump"
    # The password goes through the environment, never the command line,
    # where other local users could read it from the process list.
    # URL.set() treats None as "keep", so the password is cleared with _replace.
    conninfo = url.set(drivername="postgresql")._replace(password=None).render_as_string(hide_password=False)
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    result = subprocess.run([executable, "--format=custom", "--no-owner", f"--file={out}", f"--dbname={conninfo}"],  # nosec B603 - executable from shutil.which, argv list, no shell
                            env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise BackupError(f"pg_dump failed with exit code {result.returncode}.")
    return out


def object_key(now: datetime, kind: str = "sqlite") -> str:
    now = now.astimezone(timezone.utc)
    suffix = "db" if kind == "sqlite" else "pgdump"
    return f"{PREFIX}{now:%Y/%m/%d}/playgraph-{now:%Y%m%dT%H%M%SZ}.{suffix}.gz"


def encryption_args(cfg) -> dict:
    key_id = cfg.aws_backup_kms_key_id.strip()
    if key_id:
        return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": key_id}
    return {"ServerSideEncryption": "AES256"}


def backup(engine=None, cfg=None, client=None, now: datetime | None = None) -> dict | None:
    """Upload one encrypted snapshot. Returns None when S3 is not configured."""
    cfg = _settings(cfg)
    if not _ready(cfg):
        log.info("Off-site backup skipped: AWS_BACKUP_BUCKET is not set.")
        return None
    if engine is None:
        from app.database import engine
    now = now or datetime.now(timezone.utc)
    workdir = Path(tempfile.mkdtemp(prefix="playgraph-offsite-"))
    try:
        raw, kind = snapshot(engine, workdir)
        packed = workdir / "snapshot.gz"
        with open(raw, "rb") as source, gzip.open(packed, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target, CHUNK)
        raw.unlink()
        digest = _sha256(packed)
        key = object_key(now, kind)
        # No ACL argument: the bucket stays private and owner-enforced.
        extra = {**encryption_args(cfg), "Metadata": {"sha256": digest}, "ChecksumAlgorithm": "SHA256"}
        client = client or _client(cfg)
        client.upload_file(str(packed), cfg.aws_backup_bucket.strip(), key, ExtraArgs=extra)
        size = packed.stat().st_size
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    log.info("Off-site backup uploaded: %s (%d bytes)", key, size)
    return {"key": key, "sha256": digest, "bytes": size, "encryption": extra["ServerSideEncryption"]}


def list_backups(cfg=None, client=None) -> list[dict]:
    """Every backup object, oldest first. Keys sort by their UTC timestamp."""
    cfg = _settings(cfg)
    if not _ready(cfg):
        return []
    client = client or _client(cfg)
    found = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=cfg.aws_backup_bucket.strip(), Prefix=PREFIX):
        for item in page.get("Contents", []):
            if item["Key"].endswith(".gz"):
                found.append({"key": item["Key"], "bytes": item["Size"], "modified": item["LastModified"]})
    return sorted(found, key=lambda item: item["key"])


def _counts(path: Path) -> dict:
    # Read-only URI connection to the temporary copy, never the live file.
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise BackupError(f"Integrity check failed: {check}")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = set(REHEARSAL_TABLES) - tables
        if missing:
            raise BackupError(f"Missing required tables: {', '.join(sorted(missing))}.")
        counts = {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # nosec B608 - name comes from a fixed allowlist of table names
                  for name in REHEARSAL_TABLES}
    return {"integrity": check, "rows": counts}


def _pg_restore_list(path: Path) -> dict:
    executable = shutil.which("pg_restore")
    if not executable:
        raise BackupError("pg_restore was not found on PATH; the archive hash matched but its contents were not read.")
    result = subprocess.run([executable, "--list", str(path)], capture_output=True, text=True)  # nosec B603 - executable from shutil.which, argv list, no shell
    if result.returncode != 0:
        raise BackupError("pg_restore could not read the archive.")
    entries = [line for line in result.stdout.splitlines() if line and not line.startswith(";")]
    return {"integrity": "archive readable", "entries": len(entries)}


def verify_latest(cfg=None, client=None) -> dict:
    """Restore rehearsal for the newest backup, entirely inside a temp directory."""
    cfg = _settings(cfg)
    if not _ready(cfg):
        raise BackupError("AWS_BACKUP_BUCKET is not set; there is nothing to verify.")
    client = client or _client(cfg)
    backups = list_backups(cfg, client)
    if not backups:
        raise BackupError("No backups found in the bucket.")
    key = backups[-1]["key"]
    workdir = Path(tempfile.mkdtemp(prefix="playgraph-rehearsal-"))
    try:
        packed = workdir / "download.gz"
        response = client.get_object(Bucket=cfg.aws_backup_bucket.strip(), Key=key, ChecksumMode="ENABLED")
        digest = hashlib.sha256()
        with open(packed, "wb") as target:
            for chunk in response["Body"].iter_chunks(CHUNK):
                digest.update(chunk)
                target.write(chunk)
        expected = response.get("Metadata", {}).get("sha256", "")
        if not expected or digest.hexdigest() != expected:
            raise BackupError(f"SHA-256 mismatch for {key}; the backup is corrupt or was altered.")
        restored = workdir / ("restored.pgdump" if key.endswith(".pgdump.gz") else "restored.db")
        try:
            with gzip.open(packed, "rb") as source, open(restored, "wb") as target:
                shutil.copyfileobj(source, target, CHUNK)
            detail = _pg_restore_list(restored) if restored.suffix == ".pgdump" else _counts(restored)
        except (OSError, EOFError, sqlite3.DatabaseError) as error:
            raise BackupError(f"Could not read {key}: {type(error).__name__}") from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return {"key": key, "sha256": expected, "bytes": backups[-1]["bytes"],
            "encryption": response.get("ServerSideEncryption", "none"), **detail}
