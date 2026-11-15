"""What the app pulls, downloads, keeps and reads: only the recent past, and never more than the disk can hold.

- INGEST_DAYS (default 7): messages older than this are not pulled, their attachments are not downloaded and
  Claude doesn't read them. 0 = no limit (the old behaviour).
- MEDIA_MAX_MB (default 25): larger attachments are skipped (listed on Sources), never downloaded.
- MIN_FREE_MB (default 1024): downloads stop while less than this is free on the data volume, so the database
  always has room to write.
"""
import os, shutil, time
import db


def _int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


DAYS = _int("INGEST_DAYS", 7)
MEDIA_MAX_MB = _int("MEDIA_MAX_MB", 25)
MIN_FREE_MB = _int("MIN_FREE_MB", 1024)


def cutoff(now=None):
    """Oldest message timestamp the app works on (0 = everything)."""
    return int((now or time.time()) - DAYS * 86400) if DAYS > 0 else 0


def disk():
    """{total_mb, used_mb, free_mb} of the data volume."""
    os.makedirs(db.DATA_DIR, exist_ok=True)
    u = shutil.disk_usage(db.DATA_DIR)
    return {"total_mb": u.total // 2**20, "used_mb": u.used // 2**20, "free_mb": u.free // 2**20}


def low_disk():
    return disk()["free_mb"] < MIN_FREE_MB


def describe():
    return (f"last {DAYS} day(s)" if DAYS > 0 else "all history") + \
        f", attachments up to {MEDIA_MAX_MB} MB, downloads pause below {MIN_FREE_MB} MB free"
