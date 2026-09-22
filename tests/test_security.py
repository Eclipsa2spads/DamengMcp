from __future__ import annotations

import pytest

from core.security import QueryRejected, validate_owner, validate_select


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM dual",
        "SELECT COUNT(*) FROM APP.ORDERS",
        "WITH recent AS (SELECT ID FROM APP.ORDERS) SELECT ID FROM recent",
        "SELECT NVL(NAME, 'N/A') FROM APP.CUSTOMERS",
        "SELECT SYSDATE, TO_CHAR(SYSDATE, 'YYYY-MM-DD') FROM dual",
        "SELECT TO_DATE('2026-09-02', 'YYYY-MM-DD') FROM dual",
        "SELECT SUBSTR(NAME, 1, 3), INSTR(NAME, 'A') FROM APP.CUSTOMERS",
        "SELECT DECODE(STATUS, 'A', 1, 0), TRUNC(SYSDATE) FROM APP.ORDERS",
        "SELECT CASE WHEN STATUS = 'A' THEN 1 ELSE 0 END FROM APP.ORDERS",
        "SELECT COUNT(*) FROM APP.CUSTOMERS WHERE CLIENT_TYPE = '1' AND BRANCH_ID <> 'QHZG'",
        "SELECT * FROM APP.CUSTOMERS WHERE STATUS = 'A' OR STATUS = 'B'",
        "SELECT * FROM APP.CUSTOMERS WHERE NOT STATUS = 'D'",
        "SELECT * FROM APP.CUSTOMERS C WHERE EXISTS (SELECT 1 FROM APP.ORDERS O WHERE O.CUSTOMER_ID = C.ID)",
        "SELECT 100 / NULLIF(TOTAL, 0) FROM APP.ORDERS",
        "SELECT ROW_NUMBER() OVER (ORDER BY ID) FROM APP.ORDERS",
        "SELECT LISTAGG(PRODUCT_NAME, ',') WITHIN GROUP (ORDER BY ITEM_ID) FROM APP.ITEMS",
        "SELECT ADD_MONTHS(ORDER_DATE, 1), MONTHS_BETWEEN(SYSDATE, ORDER_DATE) FROM APP.ORDERS",
    ],
)
def test_accepts_read_queries(sql, settings):
    assert validate_select(sql, settings) is not None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO APP.T VALUES (1)",
        "UPDATE APP.T SET X = 1",
        "DELETE FROM APP.T",
        "DROP TABLE APP.T",
        "TRUNCATE TABLE APP.T",
        "CREATE TABLE APP.T (ID INT)",
        "SELECT * INTO APP.T_COPY FROM APP.T",
        "BEGIN NULL; END;",
        "SELECT * FROM APP.T; SELECT * FROM APP.U",
        "SELECT /*+ FULL(T) */ * FROM APP.T",
        "SELECT * FROM APP.T -- comment",
        "SELECT * FROM APP.T FOR UPDATE",
        "SELECT DBMS_RANDOM.VALUE FROM dual",
        "SELECT UTL_HTTP.REQUEST('http://example') FROM dual",
        "SELECT * FROM SYS.USER$",
        "SELECT * FROM APP.T@REMOTE_DB",
        "SELECT APP.SEQ.NEXTVAL FROM dual",
        "SELECT APP.SEQ.CURRVAL FROM dual",
        "SELECT APP.ABS(1) FROM dual",
        "SELECT APP.COUNT(1) FROM dual",
        "SELECT IF(STATUS = 'A', 1, 0) FROM APP.ORDERS",
        "SELECT * FROM V$SESSIONS",
        "SELECT * FROM V$DM_INI",
        "SELECT * FROM GV$SESSION",
        "SELECT * FROM SYS.DBA_USERS",
        "SELECT * FROM DBA_TABLES",
        "SELECT * FROM APP.T WHERE ROWNUM <= 1 AND DBMS_LOB.GETLENGTH(X) > 0",
    ],
)
def test_rejects_unsafe_queries(sql, settings):
    with pytest.raises(QueryRejected):
        validate_select(sql, settings)


def test_rejects_long_sql(settings):
    with pytest.raises(QueryRejected):
        validate_select("SELECT '" + ("x" * 10001) + "' FROM dual", settings)


def test_owner_allowlist(settings):
    assert validate_owner("app", settings) == "APP"
    with pytest.raises(QueryRejected):
        validate_owner("SYS", settings)
    with pytest.raises(QueryRejected):
        validate_owner("NOT_ALLOWED", settings)
