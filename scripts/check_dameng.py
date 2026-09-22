from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.connection import DamengDatabase
from core.settings import Settings

parser = argparse.ArgumentParser(description="Validate Dameng connectivity and read-only access.")
parser.add_argument(
    "--env-file",
    type=Path,
    default=Path(__file__).resolve().parents[1] / ".env",
)
args = parser.parse_args()
load_dotenv(args.env_file, override=True)
settings = Settings.from_env()
database = DamengDatabase(settings)

PROBE_TABLE = "MCP_READ_ONLY_CHECK"


def verify_read_only() -> None:
    """A write attempted through the read-only connection must be rejected."""
    try:
        with database.read_only_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE TABLE {PROBE_TABLE} (ID INT)")
    except Exception as exc:  # noqa: BLE001 - rejection is the expected outcome
        print(f"[OK] write rejected by the database: {exc}")
        return
    print(f"[FAIL] the database accepted a write; dropping {PROBE_TABLE} again")
    import dmPython

    connection = dmPython.connect(
        user=settings.dm_user,
        password=settings.dm_password,
        server=settings.dm_host,
        port=settings.dm_port,
        autoCommit=True,
        local_code=dmPython.PG_UTF8,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(f"DROP TABLE {PROBE_TABLE}")
        cursor.close()
    finally:
        connection.close()
    raise SystemExit(1)


try:
    database.open()
    print(json.dumps(database.status(), ensure_ascii=False, indent=2))
    verify_read_only()
finally:
    database.close()
