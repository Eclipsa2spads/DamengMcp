from __future__ import annotations

import pytest

from core.connection import DamengDatabase
from server import create_server


@pytest.mark.asyncio
async def test_exposes_the_read_only_query_and_metadata_tools(settings):
    database = DamengDatabase(settings)
    server = create_server(settings, database)
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "dm_execute_query",
        "dm_list_tables",
        "dm_describe_table",
        "dm_search_objects",
        "dm_list_procedures",
        "dm_get_comments",
        "dm_get_relationships",
    }
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name["dm_get_comments"].inputSchema["required"]) == {
        "owner",
        "table_name",
    }
    assert set(by_name["dm_get_relationships"].inputSchema["required"]) == {
        "owner",
        "table_names",
    }
    database.executor.shutdown(wait=True, cancel_futures=True)
