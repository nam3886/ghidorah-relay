"""The real services every later test relies on (contracts §14.2). A missing service fails; nothing is skipped."""

import os
import shutil
import subprocess
import urllib.request

import psycopg

from relay_core.testing.pg import PREFIX, admin_dsn, disposable_database


def test_postgres_16_and_a_disposable_database_is_dropped():
    with psycopg.connect(admin_dsn()) as conn:
        assert conn.info.server_version // 10000 == 16
    with disposable_database() as dsn:
        with psycopg.connect(dsn) as conn:
            name = conn.execute("SELECT current_database()").fetchone()[0]
        assert name.startswith(PREFIX)
    with psycopg.connect(admin_dsn()) as conn:
        assert conn.execute("SELECT count(*) FROM pg_database WHERE datname = %s", (name,)).fetchone()[0] == 0


def test_minio_is_live():
    url = os.environ.get("RELAY_TEST_MINIO", "")
    assert url, "RELAY_TEST_MINIO is not set: run the tests through scripts/check.sh"
    with urllib.request.urlopen(url.rstrip("/") + "/minio/health/live", timeout=5) as r:
        assert r.status == 200


def test_ffmpeg_and_ffprobe():
    for tool in ("ffmpeg", "ffprobe"):
        assert shutil.which(tool), tool + " is not installed"
        subprocess.run([tool, "-version"], check=True, capture_output=True)

