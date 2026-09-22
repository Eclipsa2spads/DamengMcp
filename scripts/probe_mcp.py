from __future__ import annotations

import argparse
import json

import httpx


def payload(response: httpx.Response) -> dict:
    response.raise_for_status()
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError("SSE response did not contain a data event")


parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8082/mcp")
parser.add_argument("--call", action="store_true")
parser.add_argument("--tool", default="dm_execute_query")
parser.add_argument("--arguments", help="JSON object passed to the selected tool")
parser.add_argument("--sql", default="SELECT 1 FROM DUAL")
args = parser.parse_args()
headers = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
with httpx.Client(timeout=40) as client:
    initialize_response = client.post(
        args.url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "dameng-mcp-probe", "version": "1.0"},
            },
        },
    )
    initialized = payload(initialize_response)
    session_id = initialize_response.headers.get("Mcp-Session-Id")
    if not session_id:
        raise RuntimeError("Server did not return Mcp-Session-Id")
    session_headers = {**headers, "Mcp-Session-Id": session_id}
    client.post(
        args.url,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    ).raise_for_status()
    tools = payload(
        client.post(
            args.url,
            headers=session_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    )
    names = [tool["name"] for tool in tools["result"]["tools"]]
    print(json.dumps({"protocol": initialized["result"]["protocolVersion"], "sessionId": session_id, "tools": names}, ensure_ascii=False, indent=2))
    if args.call:
        call_arguments = (
            json.loads(args.arguments)
            if args.arguments
            else {"sql": args.sql}
        )
        if not isinstance(call_arguments, dict):
            raise ValueError("--arguments must decode to a JSON object")
        result = payload(
            client.post(
                args.url,
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": args.tool, "arguments": call_arguments},
                },
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    terminated = client.delete(args.url, headers=session_headers)
    if terminated.status_code not in (200, 204):
        terminated.raise_for_status()
