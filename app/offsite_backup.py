"""Off-site S3 backups.

Usage: python -m app.offsite_backup backup|list|verify-latest

Setup and the restore rehearsal are described in docs/AWS_BACKUPS.md.
"""

import argparse
import logging
import sys

from app.services import offsite_backup as service


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["backup", "list", "verify-latest"])
    action = parser.parse_args(argv).action
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from app.config import settings
    try:
        if settings.demo_mode:
            raise service.BackupError("Demo mode never backs up and never contacts AWS.")
        if not settings.aws_backup_bucket.strip():
            print("Off-site backups are not configured: AWS_BACKUP_BUCKET is empty. Nothing was done.")
            return 2
        if action == "backup":
            result = service.backup(cfg=settings)
            print(f"Uploaded {result['key']} ({result['bytes']} bytes, {result['encryption']}, sha256 {result['sha256']})")
        elif action == "list":
            backups = service.list_backups(settings)
            for item in backups:
                print(f"{item['key']}  {item['bytes']} bytes  {item['modified']:%Y-%m-%d %H:%M:%S}Z")
            print(f"{len(backups)} backup(s)")
        else:
            result = service.verify_latest(settings)
            print(f"Restore rehearsal passed for {result['key']}")
            print(f"  sha256     {result['sha256']} (matches)")
            print(f"  encryption {result['encryption']}")
            print(f"  integrity  {result['integrity']}")
            for table, count in result.get("rows", {}).items():
                print(f"  {table:<10} {count} rows")
            if "entries" in result:
                print(f"  archive    {result['entries']} entries")
            print("The live database was not touched.")
    except service.BackupError as error:
        print(f"Failed: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # botocore errors carry no credential values
        print(f"Failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
