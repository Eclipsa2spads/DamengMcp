from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from starlette.testclient import TestClient

from server import create_server, create_transport_app


class FakeType:
    name = "NUMBER"


class FakeCursor:
    description = [("VALUE", FakeType())]
    arraysize = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        assert "MCP_QUERY" in sql
        assert sql.rstrip().endswith("ROWNUM <= 101")

    def fetchmany(self, _):
        return [(1,)]


class FakeConnection:
    def cursor(self):
        return FakeCursor()


class FakeDatabase:
    def open(self):
        pass

    def close(self):
        pass

    async def run(self, operation):
        return operation(FakeConnection())


def rpc_body(response):
    response.raise_for_status()
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError("Missing SSE data event")


@contextmanager
def live_server(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Test ASGI server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Test ASGI server did not stop")


def test_streamable_http_session_and_tool_call(settings):
    server = create_server(settings, FakeDatabase())
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(
        server.streamable_http_app(), base_url="http://127.0.0.1:8082"
    ) as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
        )
        initialized = rpc_body(response)
        session_id = response.headers["Mcp-Session-Id"]
        assert initialized["result"]["protocolVersion"] == "2025-03-26"
        session_headers = {**headers, "Mcp-Session-Id": session_id}
        response = client.post(
            "/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert response.status_code == 202
        listed = rpc_body(
            client.post(
                "/mcp",
                headers=session_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
        )
        assert len(listed["result"]["tools"]) == 7
        called = rpc_body(
            client.post(
                "/mcp",
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "dm_execute_query",
                        "arguments": {"sql": "SELECT 1 FROM dual"},
                    },
                },
            )
        )
        assert called["result"]["isError"] is False
        assert called["result"]["structuredContent"]["rowCount"] == 1
        rejected = rpc_body(
            client.post(
                "/mcp",
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "dm_execute_query",
                        "arguments": {"sql": "DROP TABLE APP.T"},
                    },
                },
            )
        )
        assert rejected["result"]["isError"] is True


@pytest.mark.asyncio
async def test_legacy_sse_session_and_tool_call(settings):
    server = create_server(settings, FakeDatabase())
    app = create_transport_app(server)
    with live_server(app) as base_url:
        async with sse_client(
            f"{base_url}/sse",
            timeout=5,
            sse_read_timeout=5,
        ) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "axis-dameng-mcp"
                listed = await session.list_tools()
                assert len(listed.tools) == 7
                called = await session.call_tool(
                    "dm_execute_query",
                    {"sql": "SELECT 1 FROM dual"},
                )
                assert called.isError is False
                assert called.structuredContent["rowCount"] == 1
