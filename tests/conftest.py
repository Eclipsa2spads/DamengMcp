from __future__ import annotations

import pytest

from core.settings import DEFAULT_FUNCTIONS, Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        dm_host="db.example",
        dm_port=5236,
        dm_user="MCP_READ",
        dm_password="secret",
        encoding="UTF8",
        allowed_owners=frozenset({"MCP_READ", "APP"}),
        denied_schemas=frozenset({"SYS", "SYSDBA"}),
        allowed_functions=frozenset(
            value.strip() for value in (DEFAULT_FUNCTIONS + ",CURRENT_TIMESTAMP").split(",")
        ),
        pool_min=1,
        pool_max=3,
        pool_wait_timeout_ms=5000,
        query_timeout_seconds=30,
        max_rows=200,
        max_sql_length=10000,
        max_cell_chars=10000,
        max_result_bytes=1048576,
        required_database_version_prefix="V8",
        host="127.0.0.1",
        port=8082,
        mcp_path="/mcp",
        sse_path="/sse",
        message_path="/messages/",
    )
