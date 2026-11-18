import gzip
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app import offsite_backup as cli
from app.config import settings
from app.services import offsite_backup as service

BUCKET = "playgraph-test-backups"
ACCESS_KEY = "AKIATESTONLYNOTREAL01"
SECRET_KEY = "test-only-secret-value-never-logged-0123"
WHEN = datetime(2026, 11, 14, 4, 17, 9, tzinfo=timezone.utc)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", ACCESS_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SECRET_KEY)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def config(**changes):
    values = {"demo_mode": False, "aws_backup_bucket": BUCKET, "aws_region": "us-east-1", "aws_backup_kms_key_id": ""}
    return settings.model_copy(update={**values, **changes})


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "live.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE games (id INTEGER PRIMARY KEY);
            CREATE TABLE reviews (id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1), (2);
            INSERT INTO games VALUES (1), (2), (3);
            INSERT INTO reviews VALUES (1);
        """)
    engine = create_engine(f"sqlite:///{path}")
    yield engine, path
    engine.dispose()


@pytest.fixture
def no_aws(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("AWS must not be contacted")
    monkeypatch.setattr(boto3, "client", refuse)


def test_upload_is_encrypted_private_and_checksummed(aws, database):
    engine, _ = database
    result = service.backup(engine, config(), now=WHEN)

    assert result["key"] == "backups/2026/11/14/playgraph-20261114T041709Z.db.gz"
    head = aws.head_object(Bucket=BUCKET, Key=result["key"], ChecksumMode="ENABLED")
    assert head["ServerSideEncryption"] == "AES256"
    assert head["Metadata"]["sha256"] == result["sha256"]
    assert "ChecksumSHA256" in head
    body = aws.get_object(Bucket=BUCKET, Key=result["key"])["Body"].read()
    assert hashlib.sha256(body).hexdigest() == result["sha256"]
    assert gzip.decompress(body).startswith(b"SQLite format 3")
    grants = aws.get_object_acl(Bucket=BUCKET, Key=result["key"])["Grants"]
    assert all(grant["Grantee"].get("Type") == "CanonicalUser" for grant in grants)


def test_kms_key_is_used_when_set(aws, database):
    kms = boto3.client("kms", region_name="us-east-1")
    key_id = kms.create_key()["KeyMetadata"]["KeyId"]
    result = service.backup(database[0], config(aws_backup_kms_key_id=key_id), now=WHEN)

    head = aws.head_object(Bucket=BUCKET, Key=result["key"])
    assert head["ServerSideEncryption"] == "aws:kms"
    assert key_id in head["SSEKMSKeyId"]


def test_no_acl_and_temp_files_removed(aws, database, monkeypatch, tmp_path):
    workdirs, calls = [], []
    real_mkdtemp = service.tempfile.mkdtemp
    monkeypatch.setattr(service.tempfile, "mkdtemp", lambda **kw: workdirs.append(real_mkdtemp(dir=tmp_path, **kw)) or workdirs[-1])

    class Spy:
        def upload_file(self, *args, **kwargs):
            calls.append(kwargs["ExtraArgs"])
            return aws.upload_file(*args, **kwargs)

    service.backup(database[0], config(), client=Spy(), now=WHEN)
    assert "ACL" not in calls[0]
    assert calls[0]["ChecksumAlgorithm"] == "SHA256"
    assert workdirs and not any((tmp_path / name).exists() for name in workdirs)


def test_list_and_verify_latest_rehearsal(aws, database, capsys):
    engine, live = database
    before = live.read_bytes()
    service.backup(engine, config(), now=WHEN)
    service.backup(engine, config(), now=WHEN.replace(hour=5))

    keys = [item["key"] for item in service.list_backups(config())]
    assert keys == ["backups/2026/11/14/playgraph-20261114T041709Z.db.gz",
                    "backups/2026/11/14/playgraph-20261114T051709Z.db.gz"]
    result = service.verify_latest(config())
    assert result["key"] == keys[-1]
    assert result["integrity"] == "ok"
    assert result["rows"] == {"users": 2, "games": 3, "reviews": 1}
    assert live.read_bytes() == before


def test_verify_latest_detects_altered_object(aws, database):
    first = service.backup(database[0], config(), now=WHEN)
    tampered = gzip.compress(b"not the database")
    aws.put_object(Bucket=BUCKET, Key=first["key"], Body=tampered, Metadata={"sha256": first["sha256"]})
    with pytest.raises(service.BackupError, match="SHA-256 mismatch"):
        service.verify_latest(config())


def test_verify_latest_detects_corrupt_database(aws):
    junk = gzip.compress(b"SQLite format 3\x00" + b"\x00" * 4000)
    aws.put_object(Bucket=BUCKET, Key="backups/2026/11/14/playgraph-20261114T041709Z.db.gz", Body=junk,
                   Metadata={"sha256": hashlib.sha256(junk).hexdigest()})
    with pytest.raises(service.BackupError):
        service.verify_latest(config())


@pytest.mark.parametrize("missing", [("users",), ("games",), ("reviews",), service.REHEARSAL_TABLES])
def test_verify_latest_refuses_missing_core_tables(aws, database, missing):
    engine, live = database
    with sqlite3.connect(live) as connection:
        for table in missing:
            connection.execute(f'DROP TABLE "{table}"')
    service.backup(engine, config(), now=WHEN)

    with pytest.raises(service.BackupError, match="Missing required tables"):
        service.verify_latest(config())


def test_verify_latest_accepts_empty_core_tables(aws, database):
    engine, live = database
    with sqlite3.connect(live) as connection:
        for table in service.REHEARSAL_TABLES:
            connection.execute(f'DELETE FROM "{table}"')
    service.backup(engine, config(), now=WHEN)

    result = service.verify_latest(config())
    assert result["integrity"] == "ok"
    assert result["rows"] == {"users": 0, "games": 0, "reviews": 0}


def test_verify_latest_with_empty_bucket(aws):
    with pytest.raises(service.BackupError, match="No backups"):
        service.verify_latest(config())


def test_demo_mode_refuses_without_contacting_aws(no_aws, database):
    demo = config(demo_mode=True)
    with pytest.raises(service.BackupError, match="Demo mode"):
        service.backup(database[0], demo)
    with pytest.raises(service.BackupError, match="Demo mode"):
        service.list_backups(demo)
    with pytest.raises(service.BackupError, match="Demo mode"):
        service.verify_latest(demo)
    assert not demo.aws_backups_enabled


def test_unconfigured_is_a_no_op(no_aws, database):
    off = config(aws_backup_bucket="")
    assert service.backup(database[0], off) is None
    assert service.list_backups(off) == []
    assert not off.aws_backups_enabled


def test_worker_cron_is_only_trending_when_backups_are_unconfigured():
    from app.worker import WorkerSettings
    assert not settings.aws_backups_enabled
    assert [job.coroutine.__name__ for job in WorkerSettings.cron_jobs] == ["refresh_trending", "prune_notifications"]


def test_other_databases_are_refused():
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))
    with pytest.raises(service.BackupError, match="SQLite and PostgreSQL only"):
        service.snapshot(engine, ".")


def test_pg_dump_password_stays_off_the_command_line(monkeypatch, tmp_path):
    seen = {}

    def run(argv, env, **kwargs):
        seen.update(argv=argv, env=env)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(service.subprocess, "run", run)
    url = make_url("postgresql+psycopg://app:hunter2-secret@db.internal:5432/playgraph?sslmode=require")
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"), url=url)
    path, kind = service.snapshot(engine, tmp_path)

    assert kind == "pgdump" and path.parent == tmp_path
    assert "--format=custom" in seen["argv"]
    assert "hunter2-secret" not in " ".join(seen["argv"])
    assert "--dbname=postgresql://app@db.internal:5432/playgraph?sslmode=require" in seen["argv"]
    assert seen["env"]["PGPASSWORD"] == "hunter2-secret"
    assert service.object_key(WHEN, kind).endswith("playgraph-20261114T041709Z.pgdump.gz")


def test_no_secrets_in_logs_or_output(aws, database, monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG, logger="app")
    monkeypatch.setattr("app.config.settings", config())
    monkeypatch.setattr("app.database.engine", database[0])
    for action in ("backup", "list", "verify-latest"):
        assert cli.main([action]) == 0
    printed = capsys.readouterr()
    text = caplog.text + printed.out + printed.err
    assert "Restore rehearsal passed" in printed.out
    assert ACCESS_KEY not in text and SECRET_KEY not in text


def test_cli_unconfigured_and_demo(no_aws, monkeypatch, capsys):
    monkeypatch.setattr("app.config.settings", config(aws_backup_bucket=""))
    assert cli.main(["backup"]) == 2
    monkeypatch.setattr("app.config.settings", config(demo_mode=True))
    assert cli.main(["backup"]) == 1
    assert "Demo mode" in capsys.readouterr().err
