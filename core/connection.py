from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

import dmPython
from dbutils.pooled_db import PooledDB

from core.settings import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _encoding_code(name: str) -> int:
    return {
        "UTF8": dmPython.PG_UTF8,
        "GBK": dmPython.PG_GBK,
        "GB18030": getattr(dmPython, "PG_GB18030", dmPython.PG_GBK),
    }[name]


class DamengDatabase:
    """Synchronous dmPython pool exposed safely to the async MCP server."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool: PooledDB | None = None
        self.executor = ThreadPoolExecutor(
            max_workers=settings.pool_max,
            thread_name_prefix="dameng-mcp",
        )
        self.database_version: str | None = None
        self.case_sensitive: bool | None = None
        self._read_only_warned = False
        self._slots: asyncio.Semaphore | None = None

    def _connect_kwargs(self) -> dict[str, Any]:
        return {
            "user": self.settings.dm_user,
            "password": self.settings.dm_password,
            "server": self.settings.dm_host,
            "port": self.settings.dm_port,
            "autoCommit": False,
            "local_code": _encoding_code(self.settings.encoding),
            "connection_timeout": self.settings.query_timeout_seconds,
            "app_name": "axis-dameng-mcp",
            # DM rejects any write on a connection opened in read-only access
            # mode, independently of the transaction state.
            "access_mode": dmPython.DSQL_MODE_READ_ONLY,
        }

    def open(self) -> None:
        if self.pool is not None:
            return
        try:
            # dmPython has no built-in pool; DBUtils wraps the DB-API driver.
            # The application caps in-flight work with a semaphore, because the
            # pool itself would block a worker thread indefinitely when empty.
            self.pool = PooledDB(
                creator=dmPython,
                mincached=self.settings.pool_min,
                maxcached=self.settings.pool_max,
                maxconnections=self.settings.pool_max,
                blocking=True,
                ping=1,
                **self._connect_kwargs(),
            )
            logger.info("Dameng connection pool created")
            self._preflight()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _read_version(cursor: Any) -> str | None:
        try:
            cursor.execute("SELECT BANNER FROM V$VERSION")
            banners = [str(row[0]).strip() for row in cursor.fetchall() if row and row[0]]
        except Exception as exc:  # noqa: BLE001 - reported through the preflight result
            logger.warning("V$VERSION is not readable: %s", exc)
            banners = []
        if banners:
            for banner in banners:
                if banner.upper().startswith("DM DATABASE SERVER"):
                    return banner
            return banners[0]
        try:
            cursor.execute("SELECT ID_CODE")
            row = cursor.fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ID_CODE is not readable: %s", exc)
            return None
        return str(row[0]).strip() if row and row[0] else None

    def _preflight(self) -> None:
        if self.pool is None:
            raise RuntimeError("Dameng pool is not open")
        connection = self.pool.connection()
        try:
            connection.autoCommit = False
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("Dameng preflight SELECT returned an unexpected result")
                self.database_version = self._read_version(cursor)
                try:
                    cursor.execute("SELECT CASE_SENSITIVE()")
                    row = cursor.fetchone()
                    self.case_sensitive = bool(row[0]) if row else None
                except Exception:  # noqa: BLE001 - diagnostics must not block startup
                    logger.warning("Unable to read CASE_SENSITIVE()", exc_info=True)
        finally:
            connection.close()
        required = self.settings.required_database_version_prefix
        if not self.database_version:
            raise RuntimeError(
                "Unable to determine the Dameng version; grant SELECT on V$VERSION "
                "or set DM_REQUIRED_VERSION_PREFIX to an empty value"
            )
        if required and required.upper() not in self.database_version.upper():
            raise RuntimeError(
                f"Unsupported Dameng version: {self.database_version}; expected {required}"
            )
        logger.info(
            "Dameng preflight passed; database=%s case_sensitive=%s",
            self.database_version,
            self.case_sensitive,
        )

    @contextmanager
    def read_only_connection(self) -> Iterator[Any]:
        if self.pool is None:
            raise RuntimeError("Dameng pool is not open")
        connection = self.pool.connection()
        try:
            connection.autoCommit = False
            # DM only accepts SET TRANSACTION READ ONLY as the first statement
            # of a transaction, so clear any transaction left over on this
            # pooled connection first. The access mode set at connect time
            # already enforces read-only access if this statement is refused.
            connection.rollback()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            except Exception:  # noqa: BLE001 - the connect-time guard still holds
                if not self._read_only_warned:
                    self._read_only_warned = True
                    logger.warning(
                        "SET TRANSACTION READ ONLY was refused; relying on the "
                        "connection access mode to block writes",
                        exc_info=True,
                    )
            yield connection
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()

    async def _acquire_slot(self) -> None:
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.settings.pool_max)
        try:
            await asyncio.wait_for(
                self._slots.acquire(), self.settings.pool_wait_timeout_ms / 1000
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                "No Dameng connection became available within the pool wait timeout"
            ) from exc

    async def run(self, operation: Callable[[Any], T]) -> T:
        loop = asyncio.get_running_loop()
        await self._acquire_slot()
        try:

            def execute() -> T:
                with self.read_only_connection() as connection:
                    return operation(connection)

            return await loop.run_in_executor(self.executor, execute)
        finally:
            if self._slots is not None:
                self._slots.release()

    def status(self) -> dict[str, object]:
        return {
            "ready": self.pool is not None,
            "databaseVersion": self.database_version,
            "caseSensitive": self.case_sensitive,
            "host": self.settings.dm_host,
            "port": self.settings.dm_port,
            "user": self.settings.dm_user,
            "encoding": self.settings.encoding,
        }

    def close(self) -> None:
        pool, self.pool = self.pool, None
        if pool is not None:
            try:
                pool.close()
            except Exception:  # noqa: BLE001 - shutdown must not mask the original error
                logger.warning("Failed to close the Dameng pool cleanly", exc_info=True)
            logger.info("Dameng connection pool closed")
        self.executor.shutdown(wait=True, cancel_futures=True)
