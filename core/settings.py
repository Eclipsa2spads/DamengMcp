from __future__ import annotations

import os
from dataclasses import dataclass, field


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _integer(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _csv(name: str, default: str) -> frozenset[str]:
    return frozenset(
        item.strip().upper() for item in os.getenv(name, default).split(",") if item.strip()
    )


# Dameng DM8 implements the Oracle analytic and conversion functions used by
# Text2SQL workloads. Keep the list explicit: anything absent is rejected.
DEFAULT_FUNCTIONS = (
    "ABS,ADD_MONTHS,AVG,CAST,CEIL,COALESCE,CONCAT,COUNT,DECODE,EXTRACT,FLOOR,"
    "GREATEST,INSTR,LAG,LAST_DAY,LEAD,LEAST,LENGTH,LISTAGG,LOWER,LTRIM,MAX,MIN,"
    "MOD,MONTHS_BETWEEN,NULLIF,NVL,NVL2,RANK,REGEXP_INSTR,REGEXP_REPLACE,"
    "REGEXP_SUBSTR,REPLACE,ROUND,ROW_NUMBER,RTRIM,STDDEV,SUBSTR,SUM,TO_CHAR,"
    "TO_DATE,TO_NUMBER,TRANSLATE,TRIM,TRUNC,UPPER,VARIANCE,CURRENT_TIMESTAMP"
)
DEFAULT_DENIED_SCHEMAS = "SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG"
ENCODINGS = ("UTF8", "GBK", "GB18030")


@dataclass(frozen=True)
class Settings:
    dm_host: str
    dm_port: int
    dm_user: str
    dm_password: str = field(repr=False)
    encoding: str
    allowed_owners: frozenset[str]
    denied_schemas: frozenset[str]
    allowed_functions: frozenset[str]
    pool_min: int
    pool_max: int
    pool_wait_timeout_ms: int
    query_timeout_seconds: int
    max_rows: int
    max_sql_length: int
    max_cell_chars: int
    max_result_bytes: int
    required_database_version_prefix: str
    host: str
    port: int
    mcp_path: str
    sse_path: str
    message_path: str

    @classmethod
    def from_env(cls) -> "Settings":
        user = _required("DM_USER").upper()
        allowed = _csv("DM_ALLOWED_OWNERS", user)
        denied = _csv("DM_DENIED_SCHEMAS", DEFAULT_DENIED_SCHEMAS)
        if not allowed:
            raise ValueError("DM_ALLOWED_OWNERS cannot be empty")
        overlap = allowed & denied
        if overlap:
            raise ValueError(f"Allowed owners also appear in denied schemas: {sorted(overlap)}")
        encoding = os.getenv("DM_ENCODING", "UTF8").strip().upper() or "UTF8"
        if encoding not in ENCODINGS:
            raise ValueError(f"DM_ENCODING must be one of {', '.join(ENCODINGS)}")
        pool_min = _integer("DM_POOL_MIN", 1)
        pool_max = _integer("DM_POOL_MAX", 3)
        if pool_max < pool_min:
            raise ValueError("DM_POOL_MAX must be greater than or equal to DM_POOL_MIN")
        path = os.getenv("MCP_PATH", "/mcp").strip() or "/mcp"
        if not path.startswith("/"):
            path = "/" + path
        sse_path = os.getenv("MCP_SSE_PATH", "/sse").strip() or "/sse"
        if not sse_path.startswith("/"):
            sse_path = "/" + sse_path
        message_path = os.getenv("MCP_MESSAGE_PATH", "/messages/").strip() or "/messages/"
        if not message_path.startswith("/"):
            message_path = "/" + message_path
        if not message_path.endswith("/"):
            message_path += "/"
        if len({path, sse_path, message_path}) != 3:
            raise ValueError("MCP transport paths must be different")
        return cls(
            dm_host=_required("DM_HOST"),
            dm_port=_integer("DM_PORT", 5236),
            dm_user=user,
            dm_password=_required("DM_PASSWORD"),
            encoding=encoding,
            allowed_owners=allowed,
            denied_schemas=denied,
            allowed_functions=_csv("DM_ALLOWED_FUNCTIONS", DEFAULT_FUNCTIONS),
            pool_min=pool_min,
            pool_max=pool_max,
            pool_wait_timeout_ms=_integer("DM_POOL_WAIT_TIMEOUT_MS", 5000),
            query_timeout_seconds=_integer("DM_QUERY_TIMEOUT_SECONDS", 30),
            max_rows=_integer("DM_MAX_ROWS", 200),
            max_sql_length=_integer("DM_MAX_SQL_LENGTH", 10000),
            max_cell_chars=_integer("DM_MAX_CELL_CHARS", 10000),
            max_result_bytes=_integer("DM_MAX_RESULT_BYTES", 1048576),
            required_database_version_prefix=os.getenv(
                "DM_REQUIRED_VERSION_PREFIX", "V8"
            ).strip(),
            host=os.getenv("MCP_HOST", "0.0.0.0").strip(),
            port=_integer("MCP_PORT", 8082),
            mcp_path=path,
            sse_path=sse_path,
            message_path=message_path,
        )
