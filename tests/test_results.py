from __future__ import annotations

import datetime as dt
import decimal

import dmPython

from core.results import build_result


class TypeCode:
    name = "OTHER"


def test_serializes_dameng_values():
    description = [
        ("D", dmPython.DATE),
        ("N", dmPython.DECIMAL),
        ("I", dmPython.NUMBER),
        ("B", dmPython.BLOB),
    ]
    result = build_result(
        description,
        [(dt.date(2026, 9, 2), decimal.Decimal("12.50"), 7, b"abc")],
        False,
        4,
        100,
        1024,
    )
    assert result["rows"] == [["2026-09-02", "12.50", 7, "<BLOB 3 bytes>"]]
    assert [column["type"] for column in result["columns"]] == [
        "DATE",
        "DECIMAL",
        "NUMBER",
        "BLOB",
    ]
    assert result["rowCount"] == 1
    assert result["truncated"] is False


def test_falls_back_to_the_reported_type_name():
    result = build_result([("VALUE", TypeCode())], [(1,)], False, 1, 100, 1024)
    assert result["columns"] == [{"name": "VALUE", "type": "OTHER"}]


def test_truncates_to_result_byte_limit():
    result = build_result(
        [("VALUE", dmPython.STRING)],
        [("x" * 80,), ("y" * 80,)],
        False,
        1,
        100,
        180,
    )
    assert result["truncated"] is True
    assert result["rowCount"] < 2
