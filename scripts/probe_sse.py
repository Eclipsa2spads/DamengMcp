from __future__ import annotations

import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.sse import sse_client


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8082/sse")
    parser.add_argument("--call", action="store_true")
    parser.add_argument("--tool", default="dm_execute_query")
    parser.add_argument("--arguments", help="JSON object passed to the selected tool")
    parser.add_argument("--sql", default="SELECT 1 FROM DUAL")
    args = parser.parse_args()

    async with sse_client(args.url, timeout=10, sse_read_timeout=40) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            result: dict[str, object] = {
                "protocol": initialized.protocolVersion,
                "server": initialized.serverInfo.name,
                "tools": [tool.name for tool in listed.tools],
            }
            if args.call:
                call_arguments = (
                    json.loads(args.arguments)
                    if args.arguments
                    else {"sql": args.sql}
                )
                if not isinstance(call_arguments, dict):
                    raise ValueError("--arguments must decode to a JSON object")
                called = await session.call_tool(
                    args.tool,
                    call_arguments,
                )
                result["call"] = called.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
