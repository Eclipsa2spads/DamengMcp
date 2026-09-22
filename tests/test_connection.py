from __future__ import annotations

import pytest

from core.connection import DamengDatabase


class Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append(sql)
        self.current = sql

    def fetchone(self):
        if self.current == "SELECT 1 FROM DUAL":
            return (1,)
        if self.current == "SELECT CASE_SENSITIVE()":
            return (1,)
        return ("DM Database Server 64 V8",)

    def fetchall(self):
        return [("DM Database Server 64 V8",)]


class Connection:
    def __init__(self):
        self.autoCommit = True
        self.rolled_back = 0
        self.closed = False
        self.statements = []

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class Pool:
    def __init__(self):
        self.connections = []
        self.closed = False

    def connection(self):
        connection = Connection()
        self.connections.append(connection)
        return connection

    def close(self):
        self.closed = True


def test_pool_preflight_read_only_transaction_and_cleanup(monkeypatch, settings):
    calls = []
    pool = Pool()
    monkeypatch.setattr(
        "core.connection.PooledDB",
        lambda **kwargs: calls.append(kwargs) or pool,
    )
    database = DamengDatabase(settings)
    database.open()
    assert len(calls) == 1
    assert calls[0]["mincached"] == settings.pool_min
    assert calls[0]["maxcached"] == settings.pool_max
    assert calls[0]["maxconnections"] == settings.pool_max
    assert calls[0]["blocking"] is True
    assert calls[0]["autoCommit"] is False
    assert calls[0]["access_mode"] == 1  # dmPython.DSQL_MODE_READ_ONLY
    assert calls[0]["server"] == settings.dm_host
    assert calls[0]["connection_timeout"] == settings.query_timeout_seconds
    assert database.status()["ready"] is True
    assert database.status()["databaseVersion"] == "DM Database Server 64 V8"
    assert database.status()["caseSensitive"] is True
    with pytest.raises(RuntimeError):
        with database.read_only_connection() as connection:
            # A leftover transaction must be cleared before DM accepts the
            # read-only transaction statement.
            assert connection.rolled_back >= 1
            assert connection.statements == ["SET TRANSACTION READ ONLY"]
            assert connection.autoCommit is False
            raise RuntimeError("simulated query timeout")
    assert pool.connections[-1].rolled_back >= 2
    assert pool.connections[-1].closed is True
    database.close()
    assert pool.closed is True


def test_preflight_rejects_an_unexpected_database_version(monkeypatch, settings):
    pool = Pool()
    monkeypatch.setattr("core.connection.PooledDB", lambda **kwargs: pool)
    monkeypatch.setattr(Cursor, "fetchall", lambda self: [("DM Database Server 64 V7",)])
    database = DamengDatabase(settings)
    with pytest.raises(RuntimeError):
        database.open()


def test_pool_wait_timeout_is_bounded(monkeypatch, settings):
    monkeypatch.setattr("core.connection.PooledDB", lambda **kwargs: Pool())
    database = DamengDatabase(settings)
    database.open()
    try:
        assert database.settings.pool_wait_timeout_ms == settings.pool_wait_timeout_ms
    finally:
        database.close()
