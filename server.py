from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from core.settings import Settings

if TYPE_CHECKING:
    from core.connection import DamengDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("dameng_mcp")


def create_server(settings: Settings, database: DamengDatabase) -> FastMCP:
    from tools.query import register_query_tools
    from tools.schema import register_schema_tools

    server = FastMCP(
        "axis-dameng-mcp",
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.mcp_path,
        sse_path=settings.sse_path,
        message_path=settings.message_path,
    )
    register_query_tools(server, database, settings)
    register_schema_tools(server, database, settings)
    return server


def create_transport_app(server: FastMCP) -> Starlette:
    """Expose legacy HTTP+SSE and Streamable HTTP on one ASGI application."""
    streamable_app = server.streamable_http_app()
    sse_app = server.sse_app()
    return Starlette(
        debug=server.settings.debug,
        routes=[*streamable_app.routes, *sse_app.routes],
        lifespan=streamable_app.router.lifespan_context,
    )


def main() -> None:
    env_file = os.getenv("MCP_ENV_FILE", "").strip()
    if env_file:
        load_dotenv(Path(env_file), override=True)
    else:
        load_dotenv()
    from core.connection import DamengDatabase

    settings = Settings.from_env()
    database = DamengDatabase(settings)
    logger.info("Starting Dameng MCP server")
    database.open()
    logger.info(
        "Dameng MCP ready: streamable=http://%s:%d%s sse=http://%s:%d%s",
        settings.host,
        settings.port,
        settings.mcp_path,
        settings.host,
        settings.port,
        settings.sse_path,
    )
    try:
        server = create_server(settings, database)
        import uvicorn

        uvicorn.run(
            create_transport_app(server),
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    finally:
        logger.info("Stopping Dameng MCP server")
        database.close()


if __name__ == "__main__":
    main()
