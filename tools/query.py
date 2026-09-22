from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from core.audit import request_details
from core.connection import DamengDatabase
from core.results import build_result
from core.security import validate_select
from core.settings import Settings

logger = logging.getLogger(__name__)


def register_query_tools(mcp: FastMCP, database: DamengDatabase, settings: Settings) -> None:
    @mcp.tool(
        name="dm_execute_query",
        annotations={
            "title": "Execute a read-only Dameng query",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def dm_execute_query(ctx: Context, sql: str, max_rows: int = 100) -> dict[str, Any]:
        """Execute one Dameng SELECT or query-producing WITH statement.

        SQL comments, semicolons, writes, DDL, FOR UPDATE, unapproved schemas,
        system views, and unapproved functions are rejected before execution.
        """
        request_id, source_ip = request_details(ctx)
        started = time.perf_counter()
        validate_select(sql, settings)
        requested_rows = max(1, min(int(max_rows), settings.max_rows))
        # The row limit is clamped above and inlined as an integer: DM rejects
        # nothing here, and this keeps the ROWNUM predicate free of binds.
        wrapped_sql = (
            "SELECT * FROM (" + sql.strip() + ") MCP_QUERY "
            f"WHERE ROWNUM <= {requested_rows + 1}"
        )

        def operation(connection: Any) -> dict[str, Any]:
            with connection.cursor() as cursor:
                cursor.arraysize = min(requested_rows + 1, 100)
                cursor.execute(wrapped_sql)
                rows = cursor.fetchmany(requested_rows + 1)
                elapsed = int((time.perf_counter() - started) * 1000)
                return build_result(
                    cursor.description or [],
                    rows[:requested_rows],
                    len(rows) > requested_rows,
                    elapsed,
                    settings.max_cell_chars,
                    settings.max_result_bytes,
                )

        try:
            result = await database.run(operation)
            logger.info(
                "request_id=%s source_ip=%s tool=dm_execute_query elapsed_ms=%d rows=%d",
                request_id,
                source_ip,
                result["elapsedMs"],
                result["rowCount"],
            )
            return result
        except Exception:
            logger.exception(
                "request_id=%s source_ip=%s tool=dm_execute_query failed sql_chars=%d",
                request_id,
                source_ip,
                len(sql),
            )
            raise
