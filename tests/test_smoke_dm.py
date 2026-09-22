"""End-to-end checks against a live Dameng database.

They are skipped unless DM_SMOKE=1 is set, and they use the connection settings
from .env. Create the demo objects first with:

    py -3.11 scripts/setup_test_schema.py
    DM_SMOKE=1 .venv/Scripts/python.exe -m pytest -q -m smoke
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from starlette.testclient import TestClient

from core.connection import DamengDatabase
from core.settings import Settings
from server import create_server

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not os.getenv("DM_SMOKE"),
        reason="set DM_SMOKE=1 to run the live Dameng smoke tests",
    ),
]

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
OWNER = os.getenv("DM_SMOKE_OWNER", "TESTUSER").upper()
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def rpc_body(response):
    response.raise_for_status()
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError("Missing SSE data event")


def call_tool(client, session_headers, name, arguments):
    body = rpc_body(
        client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
    )
    return body["result"]


def structured(result):
    assert result["isError"] is False, result.get("content")
    return result["structuredContent"]


@pytest.fixture(scope="module")
def client():
    settings = Settings.from_env()
    database = DamengDatabase(settings)
    database.open()
    server = create_server(settings, database)
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8082") as http:
        response = http.post(
            "/mcp",
            headers=HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "1"},
                },
            },
        )
        session_headers = {**HEADERS, "Mcp-Session-Id": response.headers["Mcp-Session-Id"]}
        http.post(
            "/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        yield http, session_headers
    database.close()


def test_metadata_tools_see_the_demo_schema(client):
    http, session_headers = client
    tables = structured(
        call_tool(http, session_headers, "dm_list_tables", {"owner": OWNER, "pattern": "DM%"})
    )
    assert {row[1] for row in tables["rows"]} == {"DM_CUSTOMER", "DM_ORDER", "DM_ORDER_ITEM"}

    described = structured(
        call_tool(
            http, session_headers, "dm_describe_table", {"owner": OWNER, "table_name": "dm_customer"}
        )
    )
    columns = {row[0]: row for row in described["rows"]}
    assert list(columns) == ["CUSTOMER_ID", "CUSTOMER_NAME", "CITY", "CREDIT_LIMIT", "CREATED_AT"]
    assert columns["CUSTOMER_ID"][-1] == "Y"
    assert columns["CITY"][-1] == "N"

    comments = structured(
        call_tool(
            http, session_headers, "dm_get_comments", {"owner": OWNER, "table_name": "DM_CUSTOMER"}
        )
    )
    comment_map = {row[1]: (row[3], row[4]) for row in comments["rows"]}
    assert comment_map["CUSTOMER_ID"][0] == "客户主表：记录客户基础信息"
    assert comment_map["CUSTOMER_ID"][1] == "客户编号，主键"

    relationships = structured(
        call_tool(
            http,
            session_headers,
            "dm_get_relationships",
            {"owner": OWNER, "table_names": ["DM_ORDER", "DM_ORDER_ITEM"]},
        )
    )
    assert {(row[1], row[4]) for row in relationships["rows"]} == {
        ("DM_ORDER", "DM_CUSTOMER"),
        ("DM_ORDER_ITEM", "DM_ORDER"),
    }

    objects = structured(
        call_tool(http, session_headers, "dm_search_objects", {"pattern": "DM_%", "owner": OWNER})
    )
    assert {row[1] for row in objects["rows"]} >= {"DM_CUSTOMER", "DM_ORDER", "DM_ORDER_ITEM"}


def test_query_tool_returns_joined_business_rows(client):
    http, session_headers = client
    result = structured(
        call_tool(
            http,
            session_headers,
            "dm_execute_query",
            {
                "sql": (
                    "SELECT C.CUSTOMER_NAME AS 客户, COUNT(*) AS 订单数, SUM(O.AMOUNT) AS 金额合计 "
                    f"FROM {OWNER}.DM_ORDER O JOIN {OWNER}.DM_CUSTOMER C "
                    "ON C.CUSTOMER_ID = O.CUSTOMER_ID "
                    "GROUP BY C.CUSTOMER_NAME ORDER BY 2 DESC"
                )
            },
        )
    )
    assert result["rowCount"] == 2
    assert result["truncated"] is False
    assert {row[0] for row in result["rows"]} == {"上海长江实业", "北京恒星科技"}
    assert [column["type"] for column in result["columns"]] == ["STRING", "BIGINT", "DECIMAL"]


def test_query_tool_truncates_and_rejects(client):
    http, session_headers = client
    truncated = structured(
        call_tool(
            http,
            session_headers,
            "dm_execute_query",
            {"sql": f"SELECT CUSTOMER_NAME FROM {OWNER}.DM_CUSTOMER ORDER BY CUSTOMER_ID", "max_rows": 2},
        )
    )
    assert truncated["rowCount"] == 2
    assert truncated["truncated"] is True

    rejected = call_tool(
        http, session_headers, "dm_execute_query", {"sql": "SELECT * FROM V$SESSIONS"}
    )
    assert rejected["isError"] is True
    blocked_owner = call_tool(
        http, session_headers, "dm_execute_query", {"sql": "SELECT * FROM SYS.SYSOBJECTS"}
    )
    assert blocked_owner["isError"] is True


def test_live_connection_refuses_writes():
    database = DamengDatabase(Settings.from_env())
    database.open()
    try:
        assert database.status()["ready"] is True
        assert "V8" in str(database.status()["databaseVersion"])
        with pytest.raises(Exception) as error:
            with database.read_only_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("CREATE TABLE MCP_SMOKE_WRITE (ID INT)")
        assert "只读" in str(error.value) or "read" in str(error.value).lower()
    finally:
        database.close()
