from __future__ import annotations

import pytest

from core.security import QueryRejected
from tools.schema import _owner_filter, _row_limit, _table_names_filter, _types_filter


def test_table_names_filter_validates_deduplicates_and_binds():
    placeholders, params = _table_names_filter(["wb_flow", "WB_FLOW_INSTANCE", "WB_FLOW"])
    assert placeholders == "?,?"
    assert params == ["WB_FLOW", "WB_FLOW_INSTANCE"]


@pytest.mark.parametrize(
    "table_names",
    [
        [],
        ["WB_FLOW;DROP_TABLE"],
        [f"TABLE_{index}" for index in range(21)],
    ],
)
def test_table_names_filter_rejects_invalid_input(table_names):
    with pytest.raises(QueryRejected):
        _table_names_filter(table_names)


def test_owner_filter_uses_the_allowlist(settings):
    sql, params = _owner_filter("app", settings)
    assert sql == "OWNER = ?"
    assert params == ["APP"]
    sql, params = _owner_filter(None, settings)
    assert sql == "OWNER IN (?,?)"
    assert params == ["APP", "MCP_READ"]
    with pytest.raises(QueryRejected):
        _owner_filter("SYS", settings)


def test_types_filter_accepts_only_approved_types():
    sql, params = _types_filter(["table", "view", "TABLE"], frozenset({"TABLE", "VIEW"}))
    assert sql == "OBJECT_TYPE IN (?,?)"
    assert params == ["TABLE", "VIEW"]
    with pytest.raises(QueryRejected):
        _types_filter(["TABLE", "JAVA CLASS"], frozenset({"TABLE"}))


def test_row_limit_is_inlined_as_an_integer():
    assert _row_limit(200) == 201
