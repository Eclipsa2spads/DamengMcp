from __future__ import annotations

import datetime as dt
import decimal
import json
from typing import Any, Sequence

# dmPython reports column types as numeric DB-API type codes. Map the codes the
# server can actually return so agents see readable types instead of integers.
TYPE_NAMES: dict[Any, str] = {}


def _register_type_names() -> None:
    try:
        import dmPython
    except ImportError:  # pragma: no cover - only relevant when the driver is absent
        return
    for name in dir(dmPython):
        if not name.isupper() or name.startswith(("PG_", "DSQL_", "DEBUG_", "SVR_")):
            continue
        value = getattr(dmPython, name)
        if hasattr(value, "__name__") or isinstance(value, type):
            TYPE_NAMES.setdefault(value, name)


_register_type_names()


def _type_name(column: Sequence[Any]) -> str:
    code = column[1]
    if code in TYPE_NAMES:
        return TYPE_NAMES[code]
    if isinstance(code, str):
        return code
    return str(getattr(code, "name", code))


def _value(value: Any, max_chars: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<BLOB {len(value)} bytes>"
    if hasattr(value, "size") and hasattr(value, "type"):
        try:
            size = int(value.size())
            type_name = str(getattr(value, "type", "")).upper()
            if "BLOB" in type_name:
                return f"<BLOB {size} bytes>"
        except Exception:
            pass
    if hasattr(value, "read"):
        try:
            data = value.read(max_chars + 1)
            if isinstance(data, bytes):
                try:
                    size = int(value.size())
                except Exception:
                    size = len(data)
                return f"<BLOB {size} bytes>"
            text = str(data)
            return text[:max_chars] + ("..." if len(text) > max_chars else "")
        except Exception:
            return "<LOB unavailable>"
    text = str(value)
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def build_result(
    description: Sequence[Any],
    raw_rows: Sequence[Sequence[Any]],
    truncated: bool,
    elapsed_ms: int,
    max_cell_chars: int,
    max_result_bytes: int,
) -> dict[str, Any]:
    columns = [{"name": str(column[0]), "type": _type_name(column)} for column in description]
    rows = [[_value(value, max_cell_chars) for value in row] for row in raw_rows]
    result: dict[str, Any] = {
        "columns": columns,
        "rows": rows,
        "rowCount": len(rows),
        "truncated": bool(truncated),
        "elapsedMs": elapsed_ms,
    }
    while rows:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) <= max_result_bytes:
            break
        rows.pop()
        result["rowCount"] = len(rows)
        result["truncated"] = True
    encoded_size = len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if encoded_size > max_result_bytes:
        raise ValueError("Column metadata alone exceeds the configured result size limit")
    return result
