from __future__ import annotations

import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

from core.settings import Settings

# Dameng folds unquoted identifiers to upper case and allows up to 128 bytes.
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
_COMMENT = re.compile(r"(--|/\*|\*/)")
_FOR_UPDATE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)
# Dynamic performance and administrative dictionary views stay off limits even
# though DM exposes them to privileged accounts. The database account is the
# final boundary, but an agent must never receive sessions, locks, or user data.
_FORBIDDEN_TABLE = re.compile(r"^(V|GV|DV)\$|^DBA_")
_DANGEROUS_NODES = (
    exp.Command,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Into,
    exp.Merge,
    exp.Transaction,
    exp.Update,
)
_FUNCTION_ALIASES = {
    "DATE_TRUNC": "TRUNC",
    "DECODE_CASE": "DECODE",
    "STR_POSITION": "INSTR",
    "STR_TO_DATE": "TO_DATE",
    "SUBSTRING": "SUBSTR",
    "TIME_TO_STR": "TO_CHAR",
}
# SQLGlot derives a few SQL grammar nodes from Func even though they are
# operators/clauses, not callable database functions. They must not be checked
# against DM_ALLOWED_FUNCTIONS.
_STRUCTURAL_FUNCTION_NODES = (exp.And, exp.Or, exp.Exists, exp.Case)


class QueryRejected(ValueError):
    pass


def validate_identifier(value: str, label: str = "identifier") -> str:
    normalized = value.strip().upper()
    if not _IDENTIFIER.fullmatch(normalized):
        raise QueryRejected(f"Invalid Dameng {label}")
    return normalized


def validate_owner(value: str | None, settings: Settings) -> str | None:
    if value is None or not value.strip():
        return None
    owner = validate_identifier(value, "owner")
    if owner in settings.denied_schemas or owner not in settings.allowed_owners:
        raise QueryRejected("Owner is not allowed")
    return owner


def validate_pattern(value: str | None, default: str = "%") -> str:
    pattern = (value or default).strip().upper()
    if len(pattern) > 128 or any(ord(char) < 32 for char in pattern):
        raise QueryRejected("Invalid search pattern")
    return pattern


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return function.name.upper()
    canonical = function.sql_name().upper()
    return _FUNCTION_ALIASES.get(canonical, canonical)


def validate_select(sql: str, settings: Settings) -> exp.Expression:
    candidate = sql.strip()
    if not candidate:
        raise QueryRejected("SQL cannot be empty")
    if len(candidate) > settings.max_sql_length:
        raise QueryRejected("SQL exceeds the configured length limit")
    if _COMMENT.search(candidate):
        raise QueryRejected("SQL comments and hints are not allowed")
    if ";" in candidate:
        raise QueryRejected("Semicolons and multiple statements are not allowed")
    if _FOR_UPDATE.search(candidate):
        raise QueryRejected("FOR UPDATE is not allowed")
    try:
        statements = parse(candidate, read="oracle")
    except ParseError as exc:
        raise QueryRejected("SQL is not valid Dameng SELECT syntax") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise QueryRejected("Only a single SELECT or query-producing WITH is allowed")
    statement = statements[0]
    if any(statement.find(node_type) is not None for node_type in _DANGEROUS_NODES):
        raise QueryRejected("DML, DDL, transaction, and command statements are not allowed")
    for table in statement.find_all(exp.Table):
        catalog = table.catalog
        db = table.db
        if catalog:
            raise QueryRejected("Database links and catalogs are not allowed")
        name = validate_identifier(table.name, "table name")
        if _FORBIDDEN_TABLE.match(name):
            raise QueryRejected("System and dynamic performance views are not allowed")
        if db:
            owner = validate_identifier(db, "schema")
            if owner in settings.denied_schemas or owner not in settings.allowed_owners:
                raise QueryRejected("Schema is not allowed")
    for function in statement.find_all(exp.Func):
        if isinstance(function, _STRUCTURAL_FUNCTION_NODES):
            continue
        # SQLGlot models each WHEN branch of a standard CASE expression as an
        # If node. Allow only that structural form; standalone IF functions
        # and user-defined functions remain subject to the allowlist.
        if isinstance(function, exp.If) and isinstance(function.parent, exp.Case):
            continue
        if isinstance(function.parent, exp.Dot):
            raise QueryRejected("Qualified, packaged, and user-defined functions are not allowed")
        name = _function_name(function)
        if name not in settings.allowed_functions:
            raise QueryRejected(f"Function {name} is not allowed")
    for column in statement.find_all(exp.Column):
        if column.name.upper() in {"NEXTVAL", "CURRVAL"}:
            raise QueryRejected("Sequence pseudocolumns are not allowed")
    return statement
