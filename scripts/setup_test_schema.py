"""Create or drop the small Dameng demo schema used to validate the MCP metadata tools.

    py -3.11 scripts/setup_test_schema.py            # create tables, comments and rows
    py -3.11 scripts/setup_test_schema.py --drop     # remove everything again

It writes only inside DM_DEMO_OWNER (default TESTUSER) and is safe to re-run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import dmPython
from dotenv import load_dotenv

TABLES = ("DM_ORDER_ITEM", "DM_ORDER", "DM_CUSTOMER")

parser = argparse.ArgumentParser(description="Create or drop the Dameng demo schema.")
parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
parser.add_argument("--drop", action="store_true")
args = parser.parse_args()

load_dotenv(args.env_file, override=True)
owner = os.getenv("DM_DEMO_OWNER", "TESTUSER").strip().upper()
connection = dmPython.connect(
    user=os.environ["DM_USER"],
    password=os.environ["DM_PASSWORD"],
    server=os.environ["DM_HOST"],
    port=int(os.getenv("DM_PORT", "5236")),
    autoCommit=False,
    local_code=dmPython.PG_UTF8,
)

CREATE_STATEMENTS = (
    f"""
    CREATE TABLE {owner}.DM_CUSTOMER (
        CUSTOMER_ID INT NOT NULL,
        CUSTOMER_NAME VARCHAR(100) NOT NULL,
        CITY VARCHAR(50),
        CREDIT_LIMIT DECIMAL(12,2),
        CREATED_AT TIMESTAMP,
        CONSTRAINT PK_DM_CUSTOMER PRIMARY KEY (CUSTOMER_ID)
    )
    """,
    f"""
    CREATE TABLE {owner}.DM_ORDER (
        ORDER_ID BIGINT NOT NULL,
        CUSTOMER_ID INT NOT NULL,
        ORDER_DATE DATE,
        STATUS VARCHAR(20),
        AMOUNT DECIMAL(12,2),
        CONSTRAINT PK_DM_ORDER PRIMARY KEY (ORDER_ID),
        CONSTRAINT FK_DM_ORDER_CUSTOMER FOREIGN KEY (CUSTOMER_ID)
            REFERENCES {owner}.DM_CUSTOMER (CUSTOMER_ID)
    )
    """,
    f"""
    CREATE TABLE {owner}.DM_ORDER_ITEM (
        ITEM_ID INT NOT NULL,
        ORDER_ID BIGINT NOT NULL,
        PRODUCT_NAME VARCHAR(100),
        QUANTITY INT,
        UNIT_PRICE DECIMAL(12,2),
        CONSTRAINT PK_DM_ORDER_ITEM PRIMARY KEY (ITEM_ID),
        CONSTRAINT FK_DM_ITEM_ORDER FOREIGN KEY (ORDER_ID)
            REFERENCES {owner}.DM_ORDER (ORDER_ID)
    )
    """,
)

COMMENTS = (
    (f"COMMENT ON TABLE {owner}.DM_CUSTOMER IS '客户主表：记录客户基础信息'"),
    (f"COMMENT ON COLUMN {owner}.DM_CUSTOMER.CUSTOMER_ID IS '客户编号，主键'"),
    (f"COMMENT ON COLUMN {owner}.DM_CUSTOMER.CUSTOMER_NAME IS '客户名称'"),
    (f"COMMENT ON COLUMN {owner}.DM_CUSTOMER.CITY IS '所在城市'"),
    (f"COMMENT ON COLUMN {owner}.DM_CUSTOMER.CREDIT_LIMIT IS '授信额度（元）'"),
    (f"COMMENT ON COLUMN {owner}.DM_CUSTOMER.CREATED_AT IS '建档时间'"),
    (f"COMMENT ON TABLE {owner}.DM_ORDER IS '订单主表：一行一个订单'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER.ORDER_ID IS '订单编号，主键'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER.CUSTOMER_ID IS '客户编号，外键关联 DM_CUSTOMER'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER.ORDER_DATE IS '下单日期'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER.STATUS IS '订单状态：新建/已发货/已完成'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER.AMOUNT IS '订单金额（元）'"),
    (f"COMMENT ON TABLE {owner}.DM_ORDER_ITEM IS '订单明细表'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER_ITEM.ITEM_ID IS '明细编号，主键'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER_ITEM.ORDER_ID IS '订单编号，外键关联 DM_ORDER'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER_ITEM.PRODUCT_NAME IS '商品名称'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER_ITEM.QUANTITY IS '数量'"),
    (f"COMMENT ON COLUMN {owner}.DM_ORDER_ITEM.UNIT_PRICE IS '单价（元）'"),
)

SAMPLE_ROWS = (
    f"INSERT INTO {owner}.DM_CUSTOMER VALUES (1001, '上海长江实业', '上海', 500000.00, "
    "TIMESTAMP '2025-01-06 09:30:00')",
    f"INSERT INTO {owner}.DM_CUSTOMER VALUES (1002, '北京恒星科技', '北京', 1200000.50, "
    "TIMESTAMP '2025-02-11 14:05:00')",
    f"INSERT INTO {owner}.DM_CUSTOMER VALUES (1003, '广州明远贸易', '广州', 80000.00, "
    "TIMESTAMP '2025-03-02 10:00:00')",
    f"INSERT INTO {owner}.DM_ORDER VALUES (5001, 1001, DATE '2026-01-12', '已完成', 128000.00)",
    f"INSERT INTO {owner}.DM_ORDER VALUES (5002, 1001, DATE '2026-02-03', '已发货', 56000.00)",
    f"INSERT INTO {owner}.DM_ORDER VALUES (5003, 1002, DATE '2026-02-18', '新建', 320000.00)",
    f"INSERT INTO {owner}.DM_ORDER_ITEM VALUES (9001, 5001, '工业轴承', 40, 2600.00)",
    f"INSERT INTO {owner}.DM_ORDER_ITEM VALUES (9002, 5001, '伺服电机', 5, 4800.00)",
    f"INSERT INTO {owner}.DM_ORDER_ITEM VALUES (9003, 5002, '液压油缸', 20, 2800.00)",
    f"INSERT INTO {owner}.DM_ORDER_ITEM VALUES (9004, 5003, '数控刀具', 100, 3200.00)",
)


def execute(sql: str) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def create() -> None:
    for table in TABLES:
        try:
            execute(f"DROP TABLE {owner}.{table} CASCADE CONSTRAINTS")
            print(f"[INFO] dropped existing {owner}.{table}")
        except Exception:  # noqa: BLE001 - absent tables are expected on a fresh run
            connection.rollback()
    for statement in CREATE_STATEMENTS:
        execute(statement)
        print(f"[OK]   created table in {owner}")
    connection.commit()
    for statement in COMMENTS:
        execute(statement)
    for statement in SAMPLE_ROWS:
        execute(statement)
    connection.commit()
    print(f"[OK]   comments and sample rows applied in {owner}")


def drop() -> None:
    for table in TABLES:
        try:
            execute(f"DROP TABLE {owner}.{table} CASCADE CONSTRAINTS")
            print(f"[OK]   dropped {owner}.{table}")
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            print(f"[INFO] {owner}.{table}: {exc}")
    connection.commit()


try:
    if args.drop:
        drop()
    else:
        create()
finally:
    connection.close()
sys.exit(0)
