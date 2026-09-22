"""Probe a Dameng DM8 instance for the capabilities this MCP server depends on.

Run it with connection settings in the environment or an env file:

    py -3.11 scripts/probe_dictionary.py --env-file .env
    DM_HOST=127.0.0.1 DM_PORT=5236 DM_USER=SYSDBA DM_PASSWORD=... \
        py -3.11 scripts/probe_dictionary.py --write-probe --timeout-probe

It only reads data. The optional --write-probe creates a temporary table inside a
read-only transaction to prove that DM rejects writes there (and drops it again if
the guard turns out to be ineffective). The optional --timeout-probe runs a CPU
bound query to measure the connection_timeout behaviour.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import dmPython
from dotenv import load_dotenv

DICTIONARY_VIEWS = (
    "ALL_TABLES",
    "ALL_TAB_COLUMNS",
    "ALL_OBJECTS",
    "ALL_CONSTRAINTS",
    "ALL_CONS_COLUMNS",
    "ALL_TAB_COMMENTS",
    "ALL_COL_COMMENTS",
)

ENCODINGS = {"GBK": dmPython.PG_GBK, "GB18030": dmPython.PG_GB18030, "UTF8": dmPython.PG_UTF8}

parser = argparse.ArgumentParser(description="Probe Dameng DM8 capabilities for the MCP server.")
parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
parser.add_argument("--write-probe", action="store_true")
parser.add_argument("--timeout-probe", action="store_true")
args = parser.parse_args()

load_dotenv(args.env_file, override=True)
host = os.getenv("DM_HOST", "127.0.0.1").strip()
port = int(os.getenv("DM_PORT", "5236"))
user = os.getenv("DM_USER", "").strip()
password = os.getenv("DM_PASSWORD", "").strip()
encoding = os.getenv("DM_ENCODING", "UTF8").strip().upper()
if not user or not password:
    raise SystemExit("DM_USER and DM_PASSWORD must be set")

connect_kwargs = {
    "user": user,
    "password": password,
    "server": host,
    "port": port,
    "autoCommit": False,
    "local_code": ENCODINGS.get(encoding, dmPython.PG_UTF8),
}


def report(label: str, value: object) -> None:
    print(f"[INFO] {label}: {value}")


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f"[{'OK' if condition else 'FAIL'}] {label}{' ' + str(detail) if detail != '' else ''}")


def run(cursor, sql: str, params=None):
    """Execute a statement and return rows only when it produces a result set."""
    cursor.execute(sql, params) if params is not None else cursor.execute(sql)
    return cursor.fetchall() if cursor.description else []


def probe_environment(cursor) -> None:
    print("\n== 1. Version, parameters and users ==")
    for row in run(cursor, "SELECT BANNER FROM V$VERSION"):
        report("V$VERSION.BANNER", row[0])
    try:
        report("ID_CODE", run(cursor, "SELECT ID_CODE")[0][0])
    except Exception as exc:  # noqa: BLE001 - diagnostic output only
        report("ID_CODE", f"unavailable: {exc}")
    try:
        for row in run(
            cursor,
            "SELECT PARA_NAME, PARA_VALUE FROM V$DM_INI WHERE PARA_NAME IN "
            "('COMPATIBLE_MODE','CASE_SENSITIVE','CHARSET')",
        ):
            report(f"V$DM_INI.{row[0]}", row[1])
    except Exception as exc:  # noqa: BLE001
        report("V$DM_INI", f"unavailable: {exc}")
    try:
        report("SF_GET_UNICODE_FLAG()", run(cursor, "SELECT SF_GET_UNICODE_FLAG() FROM DUAL")[0][0])
    except Exception as exc:  # noqa: BLE001
        report("SF_GET_UNICODE_FLAG()", f"unavailable: {exc}")
    try:
        report("CASE_SENSITIVE()", run(cursor, "SELECT CASE_SENSITIVE() FROM DUAL")[0][0])
    except Exception as exc:  # noqa: BLE001
        report("CASE_SENSITIVE()", f"unavailable: {exc}")
    try:
        users = [row[0] for row in run(cursor, "SELECT USERNAME FROM DBA_USERS ORDER BY USERNAME")]
        report("DBA_USERS", ",".join(users))
    except Exception as exc:  # noqa: BLE001
        report("DBA_USERS", f"unavailable: {exc}")
    try:
        report("DUAL", run(cursor, "SELECT 1 FROM DUAL")[0][0])
    except Exception as exc:  # noqa: BLE001
        report("DUAL", f"unavailable: {exc}")


def probe_dictionary(cursor) -> None:
    print("\n== 2. Dictionary view shapes ==")
    for view in DICTIONARY_VIEWS:
        try:
            cursor.execute(f"SELECT * FROM {view} WHERE ROWNUM = 1")
            columns = [str(column[0]).upper() for column in (cursor.description or [])]
            report(view, f"{len(columns)} columns")
            print("       " + ",".join(columns))
        except Exception as exc:  # noqa: BLE001
            check(view, False, f"unavailable: {exc}")
    print("\n== 3. ALL_OBJECTS object types ==")
    try:
        for row in run(cursor, "SELECT OBJECT_TYPE, COUNT(*) FROM ALL_OBJECTS GROUP BY OBJECT_TYPE ORDER BY 1"):
            report("OBJECT_TYPE", f"{row[0]} x{row[1]}")
    except Exception as exc:  # noqa: BLE001
        check("OBJECT_TYPE", False, str(exc))
    print("\n== 4. Required columns ==")
    required = {
        "ALL_TABLES": ("OWNER", "TABLE_NAME", "NUM_ROWS", "STATUS"),
        "ALL_TAB_COLUMNS": (
            "OWNER",
            "TABLE_NAME",
            "COLUMN_NAME",
            "DATA_TYPE",
            "DATA_LENGTH",
            "DATA_PRECISION",
            "DATA_SCALE",
            "NULLABLE",
            "DATA_DEFAULT",
            "COLUMN_ID",
        ),
        "ALL_CONS_COLUMNS": ("OWNER", "CONSTRAINT_NAME", "TABLE_NAME", "COLUMN_NAME", "POSITION"),
        "ALL_CONSTRAINTS": (
            "OWNER",
            "CONSTRAINT_NAME",
            "CONSTRAINT_TYPE",
            "TABLE_NAME",
            "R_OWNER",
            "R_CONSTRAINT_NAME",
            "DELETE_RULE",
            "STATUS",
        ),
        "ALL_TAB_COMMENTS": ("OWNER", "TABLE_NAME", "COMMENTS"),
        "ALL_COL_COMMENTS": ("OWNER", "TABLE_NAME", "COLUMN_NAME", "COMMENTS"),
        "ALL_OBJECTS": ("OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS", "LAST_DDL_TIME"),
    }
    for view, columns in required.items():
        try:
            cursor.execute(
                "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = 'SYS' "
                "AND TABLE_NAME = ?",
                (view,),
            )
            present = {str(row[0]).upper() for row in cursor.fetchall()}
            missing = [name for name in columns if name not in present]
            check(view, not missing, "missing: " + ",".join(missing) if missing else "all columns present")
        except Exception as exc:  # noqa: BLE001
            check(view, False, f"introspection failed: {exc}")


def probe_behaviour(cursor) -> None:
    print("\n== 5. Read-only transaction and enforcement ==")
    # DM only accepts SET TRANSACTION READ ONLY as the first statement of a
    # transaction, so this check needs its own fresh connection.
    probe = dmPython.connect(**connect_kwargs)
    probe_cursor = probe.cursor()
    try:
        try:
            run(probe_cursor, "SET TRANSACTION READ ONLY")
            check("SET TRANSACTION READ ONLY", True)
        except Exception as exc:  # noqa: BLE001
            check("SET TRANSACTION READ ONLY", False, str(exc))
        if args.write_probe:
            try:
                run(probe_cursor, "CREATE TABLE MCP_READ_ONLY_PROBE (ID INT)")
                check("write inside read-only transaction", False, "the write was accepted")
            except Exception as exc:  # noqa: BLE001
                report("write inside read-only transaction", f"rejected as expected: {exc}")
        else:
            report("write probe", "skipped (pass --write-probe to test)")
        probe.rollback()
        try:
            run(probe_cursor, "SELECT 1 FROM DUAL")
            try:
                run(probe_cursor, "SET TRANSACTION READ ONLY")
                report("SET READ ONLY inside a running transaction", "accepted by this build")
            except Exception as exc:  # noqa: BLE001
                report("SET READ ONLY inside a running transaction", f"rejected as expected: {exc}")
        finally:
            probe.rollback()
    finally:
        probe_cursor.close()
        probe.close()
    try:
        created = bool(run(cursor, "SELECT 1 FROM ALL_TABLES WHERE TABLE_NAME='MCP_READ_ONLY_PROBE'"))
    except Exception:  # noqa: BLE001
        created = False
    if created:
        run(cursor, "DROP TABLE MCP_READ_ONLY_PROBE")
        report("cleanup", "MCP_READ_ONLY_PROBE dropped")
    print("\n== 6. Pagination and parameter styles ==")
    wrapped = "SELECT * FROM (SELECT 1 AS ID FROM DUAL) MCP_QUERY WHERE ROWNUM <= 101"
    try:
        check("ROWNUM wrapper", bool(run(cursor, wrapped)))
    except Exception as exc:  # noqa: BLE001
        check("ROWNUM wrapper", False, str(exc))
    try:
        check("ROWNUM with bind", bool(run(cursor, "SELECT 1 FROM DUAL WHERE ROWNUM <= ?", (1,))))
    except Exception as exc:  # noqa: BLE001
        check("ROWNUM with bind", False, str(exc))
    try:
        check("FETCH FIRST", bool(run(cursor, "SELECT 1 AS ID FROM DUAL FETCH FIRST 1 ROWS ONLY")))
    except Exception as exc:  # noqa: BLE001
        check("FETCH FIRST", False, str(exc))
    # DM types a bare parameter projection as text ('7' rather than 7); in
    # predicates the value is converted according to the column type.
    for label, sql, params in (
        ("positional ?", "SELECT ? FROM DUAL", (7,)),
        ("named :1", "SELECT :1 FROM DUAL", (7,)),
        ("named :value dict", "SELECT :value FROM DUAL", {"value": 7}),
    ):
        try:
            value = run(cursor, sql, params)[0][0]
            check(label, str(value) == "7", f"returned {value!r}")
        except Exception as exc:  # noqa: BLE001
            check(label, False, str(exc))
    try:
        check(
            "? in a predicate",
            run(cursor, "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = ?", ("SYS",))[0][0] > 0,
        )
    except Exception as exc:  # noqa: BLE001
        check("? in a predicate", False, str(exc))
    print("\n== 7. Text round-trip ==")
    try:
        text = run(cursor, "SELECT ? FROM DUAL", ("中文测试",))[0][0]
        check("UTF-8 literal round-trip", text == "中文测试", repr(text))
    except Exception as exc:  # noqa: BLE001
        check("UTF-8 literal round-trip", False, str(exc))
    try:
        description = cursor.description
        report("cursor.description sample", [(column[0], column[1]) for column in description])
    except Exception as exc:  # noqa: BLE001
        report("cursor.description", f"unavailable: {exc}")


def probe_timeout() -> None:
    print("\n== 8. connection_timeout behaviour ==")
    # COUNT over a generated series is optimised away; SUM forces the work.
    slow_sql = "SELECT SUM(L) FROM (SELECT LEVEL L FROM DUAL CONNECT BY LEVEL <= 40000000)"
    try:
        connection = dmPython.connect(**connect_kwargs)
    except Exception as exc:  # noqa: BLE001
        check("connect", False, str(exc))
        return
    try:
        with connection.cursor() as cursor:
            started = time.perf_counter()
            try:
                run(cursor, slow_sql)
                report("slow query without timeout", f"{time.perf_counter() - started:.2f}s")
            except Exception as exc:  # noqa: BLE001
                report("slow query without timeout", f"{time.perf_counter() - started:.2f}s, {exc}")
    finally:
        connection.close()
    try:
        connection = dmPython.connect(**{**connect_kwargs, "connection_timeout": 2})
    except Exception as exc:  # noqa: BLE001
        check("connect with connection_timeout=2", False, str(exc))
        return
    try:
        with connection.cursor() as cursor:
            started = time.perf_counter()
            try:
                run(cursor, slow_sql)
                report(
                    "slow query with connection_timeout=2",
                    f"{time.perf_counter() - started:.2f}s, not aborted",
                )
            except Exception as exc:  # noqa: BLE001
                report(
                    "slow query with connection_timeout=2",
                    f"{time.perf_counter() - started:.2f}s, aborted: {exc}",
                )
    finally:
        connection.close()


def main() -> None:
    print(f"[INFO] target: {host}:{port} user={user} encoding={encoding}")
    connection = dmPython.connect(**connect_kwargs)
    try:
        connection.autoCommit = False
        cursor = connection.cursor()
        try:
            probe_environment(cursor)
            probe_dictionary(cursor)
            probe_behaviour(cursor)
        finally:
            cursor.close()
            connection.rollback()
    finally:
        connection.close()
    if args.timeout_probe:
        probe_timeout()


if __name__ == "__main__":
    sys.exit(main())
