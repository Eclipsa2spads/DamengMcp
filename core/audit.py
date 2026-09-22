from __future__ import annotations

from mcp.server.fastmcp import Context


def request_details(context: Context) -> tuple[str, str]:
    request_id = context.request_id
    request = context.request_context.request
    client = getattr(request, "client", None)
    source_ip = getattr(client, "host", None) or "unknown"
    return request_id, source_ip
