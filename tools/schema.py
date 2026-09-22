from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from core.audit import request_details
from core.connection import DamengDatabase
from core.results import build_result
from core.security import (
    QueryRejected,
    validate_identifier,
    validate_owner,
    validate_pattern,
)
from core.settings import Settings

logger = logging.getLogger(__name__)
# Object types observed in DM8 ALL_OBJECTS that a Text2SQL agent may safely search.
SEARCHABLE_TYPES = frozenset(
    {
        "TABLE",
        "VIEW",
        "MATERIALIZED VIEW",
        "SEQUENCE",
        "SYNONYM",
        "PROCEDURE",
        "FUNCTION",
        "PACKAGE",
    }
)
PROCEDURE_TYPES = frozenset({"PROCEDURE", "FUNCTION", "PACKAGE"})


def _placeholders(count: int) -> str:
    """dmPython uses qmark binding, so parameters are positional."""
    return ",".join(["?"] * count)


def _row_limit(limit: int) -> int:
    """Row limits are clamped integers, so they are inlined instead of bound."""
    return int(limit) + 1


def _owner_filter(owner: str | None, settings: Settings) -> tuple[str, list[Any]]:
    resolved = validate_owner(owner, settings)
    if resolved:
        return "OWNER = ?", [resolved]
    owners = sorted(settings.allowed_owners)
    return f"OWNER IN ({_placeholders(len(owners))})", list(owners)


def _types_filter(
    requested: list[str] | None, allowed: frozenset[str]
) -> tuple[str, list[Any]]:
    values = sorted({item.strip().upper() for item in (requested or allowed)})
    if not values or any(value not in allowed for value in values):
        raise QueryRejected("One or more object types are not allowed")
    return f"OBJECT_TYPE IN ({_placeholders(len(values))})", list(values)


def _table_names_filter(table_names: list[str]) -> tuple[str, list[Any]]:
    if not table_names:
        raise QueryRejected("At least one table name is required")
    if len(table_names) > 20:
        raise QueryRejected("At most 20 table names may be inspected at once")
    names = sorted({validate_identifier(item, "table name") for item in table_names})
    return _placeholders(len(names)), list(names)


def register_schema_tools(mcp: FastMCP, database: DamengDatabase, settings: Settings) -> None:
    async def run_metadata(
        ctx: Context,
        tool_name: str,
        sql: str,
        params: list[Any],
        row_limit: int = 200,
    ) -> dict[str, Any]:
        request_id, source_ip = request_details(ctx)
        started = time.perf_counter()
        limit = min(row_limit, settings.max_rows)

        def operation(connection: Any) -> dict[str, Any]:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchmany(limit + 1)
                return build_result(
                    cursor.description or [],
                    rows[:limit],
                    len(rows) > limit,
                    int((time.perf_counter() - started) * 1000),
                    settings.max_cell_chars,
                    settings.max_result_bytes,
                )

        result = await database.run(operation)
        logger.info(
            "request_id=%s source_ip=%s tool=%s elapsed_ms=%d rows=%d",
            request_id,
            source_ip,
            tool_name,
            result["elapsedMs"],
            result["rowCount"],
        )
        return result

    @mcp.tool(name="dm_list_tables", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def dm_list_tables(
        ctx: Context, owner: str | None = None, pattern: str | None = None
    ) -> dict[str, Any]:
        """List accessible tables from ALL_TABLES within the configured owner allowlist."""
        owner_sql, params = _owner_filter(owner, settings)
        params.append(validate_pattern(pattern))
        sql = (
            "SELECT OWNER, TABLE_NAME, NUM_ROWS, STATUS FROM ("
            "SELECT OWNER, TABLE_NAME, NUM_ROWS, STATUS FROM ALL_TABLES "
            f"WHERE {owner_sql} AND TABLE_NAME LIKE ? ESCAPE '\\' "
            "ORDER BY OWNER, TABLE_NAME) "
            f"WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(ctx, "dm_list_tables", sql, params)

    @mcp.tool(name="dm_describe_table", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def dm_describe_table(
        ctx: Context, owner: str, table_name: str
    ) -> dict[str, Any]:
        """Describe accessible table columns, types, nullability, defaults, and primary-key membership."""
        resolved_owner = validate_owner(owner, settings)
        if not resolved_owner:
            raise QueryRejected("owner is required")
        name = validate_identifier(table_name, "table name")
        sql = (
            "SELECT * FROM ("
            "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, "
            "NULLABLE, DATA_DEFAULT, COLUMN_ID, "
            "CASE WHEN EXISTS (SELECT 1 FROM ALL_CONSTRAINTS AC "
            "JOIN ALL_CONS_COLUMNS ACC ON ACC.OWNER=AC.OWNER "
            "AND ACC.CONSTRAINT_NAME=AC.CONSTRAINT_NAME "
            "WHERE AC.OWNER=C.OWNER AND AC.TABLE_NAME=C.TABLE_NAME "
            "AND AC.CONSTRAINT_TYPE='P' AND ACC.COLUMN_NAME=C.COLUMN_NAME) "
            "THEN 'Y' ELSE 'N' END AS PRIMARY_KEY "
            "FROM ALL_TAB_COLUMNS C WHERE OWNER=? "
            "AND TABLE_NAME=? ORDER BY COLUMN_ID"
            f") WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(
            ctx,
            "dm_describe_table",
            sql,
            [resolved_owner, name],
        )

    @mcp.tool(name="dm_get_comments", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def dm_get_comments(
        ctx: Context, owner: str, table_name: str
    ) -> dict[str, Any]:
        """Get table and column comments for one allowed Dameng table or view.

        Use this after object discovery to understand business meaning without
        reading rows from the business table.
        """
        resolved_owner = validate_owner(owner, settings)
        if not resolved_owner:
            raise QueryRejected("owner is required")
        name = validate_identifier(table_name, "table name")
        sql = (
            "SELECT * FROM ("
            "SELECT C.COLUMN_ID, C.COLUMN_NAME, C.DATA_TYPE, "
            "TC.COMMENTS AS TABLE_COMMENT, CC.COMMENTS AS COLUMN_COMMENT "
            "FROM ALL_TAB_COLUMNS C "
            "LEFT JOIN ALL_TAB_COMMENTS TC ON TC.OWNER=C.OWNER "
            "AND TC.TABLE_NAME=C.TABLE_NAME "
            "LEFT JOIN ALL_COL_COMMENTS CC ON CC.OWNER=C.OWNER "
            "AND CC.TABLE_NAME=C.TABLE_NAME AND CC.COLUMN_NAME=C.COLUMN_NAME "
            "WHERE C.OWNER=? AND C.TABLE_NAME=? "
            "ORDER BY C.COLUMN_ID"
            f") WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(
            ctx,
            "dm_get_comments",
            sql,
            [resolved_owner, name],
        )

    @mcp.tool(
        name="dm_get_relationships",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def dm_get_relationships(
        ctx: Context, owner: str, table_names: list[str]
    ) -> dict[str, Any]:
        """Get foreign-key joins involving up to 20 allowed tables.

        Returns child and parent tables, matching columns, constraint name,
        delete rule, and status. It reads only Dameng ALL_* metadata views.
        """
        resolved_owner = validate_owner(owner, settings)
        if not resolved_owner:
            raise QueryRejected("owner is required")
        placeholders, names = _table_names_filter(table_names)
        sql = (
            "SELECT * FROM ("
            "SELECT FK.OWNER AS CHILD_OWNER, FK.TABLE_NAME AS CHILD_TABLE, "
            "FCC.COLUMN_NAME AS CHILD_COLUMN, PK.OWNER AS PARENT_OWNER, "
            "PK.TABLE_NAME AS PARENT_TABLE, PCC.COLUMN_NAME AS PARENT_COLUMN, "
            "FK.CONSTRAINT_NAME, FK.DELETE_RULE, FK.STATUS "
            "FROM ALL_CONSTRAINTS FK "
            "JOIN ALL_CONS_COLUMNS FCC ON FCC.OWNER=FK.OWNER "
            "AND FCC.CONSTRAINT_NAME=FK.CONSTRAINT_NAME "
            "JOIN ALL_CONSTRAINTS PK ON PK.OWNER=FK.R_OWNER "
            "AND PK.CONSTRAINT_NAME=FK.R_CONSTRAINT_NAME "
            "JOIN ALL_CONS_COLUMNS PCC ON PCC.OWNER=PK.OWNER "
            "AND PCC.CONSTRAINT_NAME=PK.CONSTRAINT_NAME "
            "AND PCC.POSITION=FCC.POSITION "
            "WHERE FK.CONSTRAINT_TYPE='R' AND FK.OWNER=? "
            "AND PK.OWNER=? "
            f"AND (FK.TABLE_NAME IN ({placeholders}) "
            f"OR PK.TABLE_NAME IN ({placeholders})) "
            "ORDER BY FK.TABLE_NAME, FK.CONSTRAINT_NAME, FCC.POSITION"
            f") WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(
            ctx,
            "dm_get_relationships",
            sql,
            [resolved_owner, resolved_owner, *names, *names],
        )

    @mcp.tool(name="dm_search_objects", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def dm_search_objects(
        ctx: Context,
        pattern: str,
        owner: str | None = None,
        object_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search accessible ALL_OBJECTS entries by name and approved object types."""
        owner_sql, params = _owner_filter(owner, settings)
        types_sql, type_params = _types_filter(object_types, SEARCHABLE_TYPES)
        params.extend(type_params)
        params.append(validate_pattern(pattern))
        sql = (
            "SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL_TIME FROM ("
            "SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL_TIME "
            f"FROM ALL_OBJECTS WHERE {owner_sql} AND {types_sql} "
            "AND OBJECT_NAME LIKE ? ESCAPE '\\' "
            "ORDER BY OWNER, OBJECT_TYPE, OBJECT_NAME) "
            f"WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(ctx, "dm_search_objects", sql, params)

    @mcp.tool(name="dm_list_procedures", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def dm_list_procedures(
        ctx: Context, owner: str | None = None, pattern: str | None = None
    ) -> dict[str, Any]:
        """List accessible procedures, functions, and packages from ALL_OBJECTS."""
        owner_sql, params = _owner_filter(owner, settings)
        types_sql, type_params = _types_filter(None, PROCEDURE_TYPES)
        params.extend(type_params)
        params.append(validate_pattern(pattern))
        sql = (
            "SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL_TIME FROM ("
            "SELECT OWNER, OBJECT_NAME, OBJECT_TYPE, STATUS, LAST_DDL_TIME "
            f"FROM ALL_OBJECTS WHERE {owner_sql} AND {types_sql} "
            "AND OBJECT_NAME LIKE ? ESCAPE '\\' "
            "ORDER BY OWNER, OBJECT_TYPE, OBJECT_NAME) "
            f"WHERE ROWNUM <= {_row_limit(min(200, settings.max_rows))}"
        )
        return await run_metadata(ctx, "dm_list_procedures", sql, params)
