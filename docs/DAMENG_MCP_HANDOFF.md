# 达梦 MCP 交接说明（Text2SQL）

本文档面向平台与智能体侧：接口契约、给智能体的生成规则、只读账号授权清单，
以及 2026-09-22 在达梦实库上的核实记录。

## 1. 链路

```
智能体
 ├─ dm_search_objects / dm_list_tables        → 发现对象
 ├─ dm_describe_table / dm_get_comments       → 理解结构与业务含义
 ├─ dm_get_relationships                      → 获取外键关联
 └─ dm_execute_query                          → 执行生成的只读 SQL
```

本服务不额外部署 Text2SQL 模型：自然语言转 SQL 由智能体完成，达梦 MCP 提供真实元数据
与最终安全执行边界。

## 2. 接口契约

工具与入参：

| 工具 | 必填 | 可选 |
| --- | --- | --- |
| `dm_execute_query` | `sql` | `max_rows`（默认 100，上限 `DM_MAX_ROWS`） |
| `dm_list_tables` | — | `owner`、`pattern` |
| `dm_describe_table` | `owner`、`table_name` | — |
| `dm_search_objects` | `pattern` | `owner`、`object_types` |
| `dm_list_procedures` | — | `owner`、`pattern` |
| `dm_get_comments` | `owner`、`table_name` | — |
| `dm_get_relationships` | `owner`、`table_names`（最多 20 张） | — |

查询与元数据工具统一返回：

```json
{
  "columns": [{"name": "CUSTOMER_NAME", "type": "STRING"}],
  "rows": [["上海长江实业"]],
  "rowCount": 1,
  "truncated": false,
  "elapsedMs": 137
}
```

约定：

- `columns[].type` 为 dmPython 类型名（`STRING`/`NUMBER`/`BIGINT`/`DECIMAL`/`DATE`/`TIMESTAMP`/`CLOB`/`BLOB` 等）。
- 数值列可能以 `DECIMAL` 返回；整数值会被规范化成 JSON 数字。
- `BLOB` 以 `<BLOB n bytes>` 占位，不回传二进制内容；单单元格超长会截断并加 `...`。
- 超过行数或结果字节上限时 `truncated=true`，`rowCount` 为实际返回行数。
- 拒绝执行时返回 MCP `isError=true`，message 以 `QueryRejected` 语义说明原因。

## 3. 给智能体的生成规则

允许：

- 单条 `SELECT`，或最终产生查询的 `WITH ... SELECT`（禁止分号结尾）。
- 只引用 `DM_ALLOWED_OWNERS` 中的模式，建议始终写全 `OWNER.TABLE` 限定名。
- 函数白名单（可配置 `DM_ALLOWED_FUNCTIONS`）：`NVL/DECODE/SUBSTR/INSTR/TO_CHAR/TO_DATE/
  TO_NUMBER/TRUNC/ROUND/SUM/COUNT/AVG/MIN/MAX/LISTAGG/ROW_NUMBER/RANK/ADD_MONTHS/
  MONTHS_BETWEEN/CASE/CAST` 等。

禁止（连接数据库前即拒绝）：

- 注释（`--`、`/* */`，含 Hint）、分号、多语句、`FOR UPDATE`。
- 任何写操作与事务语句：`INSERT/UPDATE/DELETE/MERGE/TRUNCATE/CREATE/DROP/ALTER/
  COMMIT/ROLLBACK/BEGIN ... END`。
- 系统视图与字典：`V$*`、`GV$*`、`DV$*`、`DBA_*`，以及非白名单模式（`SYS` 等）。
- 数据库链接（`table@link`）、序列伪列（`NEXTVAL`/`CURRVAL`）、
  非白名单函数与 `PACKAGE.FUNCTION` 形式调用（如 `DBMS_*`、`UTL_*`）。

方言要点：

- 不需要也不要在 SQL 里写分页：服务端会包一层 `ROWNUM` 上限；`FETCH FIRST`/`LIMIT` 也支持，
  但请依赖服务端分页。
- 时间字面量建议 `DATE '2026-01-12'`、`TIMESTAMP '2026-01-12 09:30:00'`，或 `TO_DATE(...)`。
- 标识符不区分大小写地按大写处理，请使用大写对象名。

## 4. 只读账号授权清单

生产环境必须使用专用只读账号。以下 SQL 于 2026-09-23 在实机逐条验证过：

```sql
-- 1) 建号即可连接，达梦不需要 GRANT CREATE SESSION（实测）
CREATE USER MCP_READ IDENTIFIED BY "<强密码>";

-- 2) 逐表 / 逐视图授予 SELECT，不要图省事用 SELECT ANY TABLE
GRANT SELECT ON APP_SCHEMA.<表或视图> TO MCP_READ;
```

表或视图多的时候，先生成语句、核对无误再执行：

```sql
SELECT 'GRANT SELECT ON ' || OWNER || '.' || TABLE_NAME || ' TO MCP_READ;'
  FROM ALL_TABLES WHERE OWNER = 'APP_SCHEMA';
SELECT 'GRANT SELECT ON ' || OWNER || '.' || VIEW_NAME || ' TO MCP_READ;'
  FROM ALL_VIEWS WHERE OWNER = 'APP_SCHEMA';
```

需要避开的三个坑（均为实测结论）：

- **不需要额外字典权限**：新用户默认就能读 `V$VERSION`、`ID_CODE`、`CASE_SENSITIVE()`
  以及全部 `ALL_*` 字典视图，无需 `SELECT ANY DICTIONARY`，也无需单独授 `V$VERSION`
- **达梦没有模式级授权**：`GRANT SELECT ON SCHEMA <模式>` 报 `-2201 无效的数据库对象`，
  `GRANT SELECT ON <模式>.*` 报 `-2007 语法分析出错`
- **`GRANT SELECT ANY TABLE` 不推荐**：它会把 `V$SESSIONS` 这类动态性能视图一并放开，
  数据库侧边界形同虚设（应用层仍会拦截，但少了一层防护）

`.env` 中相应配置：

```
DM_USER=MCP_READ
DM_ALLOWED_OWNERS=MCP_READ,APP_SCHEMA
DM_DENIED_SCHEMAS=SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG
```

注意：`DM_ALLOWED_OWNERS` 与 `DM_DENIED_SCHEMAS` 不得有交集，服务启动时会校验。

完整的建号步骤与部署流程见
[`LINUX_NATIVE_DEPLOYMENT.md`](LINUX_NATIVE_DEPLOYMENT.md) 第 1 节。

## 5. 实机核实记录（2026-09-22）

核实环境：DM8，`DM Database Server 64 V8`，DB Version `0x7000d`，
ID_CODE `03134284336-20250117-257733-20132`。

| 项目 | 结果 |
| --- | --- |
| `COMPATIBLE_MODE` | `0`（默认兼容模式；Oracle 风格字典视图与 `ROWNUM` 均可用） |
| 字符集 | `SF_GET_UNICODE_FLAG()=1`（UTF-8）；中文注释与中文字面量往返正常 |
| 大小写 | `CASE_SENSITIVE()=1`；`max_identifier_length=128`（服务端标识符上限已放宽到 128） |
| 内置用户 | `SYS, SYSAUDITOR, SYSDBA, SYSSSO`（默认黑名单已覆盖） |
| 版本视图 | `V$VERSION.BANNER` 多行，首行为 `DM Database Server 64 V8`；`SELECT ID_CODE` 可用 |
| `ALL_TABLES` | 56 列，含 `OWNER/TABLE_NAME/NUM_ROWS/STATUS` |
| `ALL_TAB_COLUMNS` | 31 列，含 `COLUMN_ID/DATA_DEFAULT/DATA_PRECISION/DATA_SCALE/NULLABLE` |
| `ALL_OBJECTS` | 15 列，含 `OBJECT_TYPE/STATUS/LAST_DDL_TIME` |
| `ALL_CONSTRAINTS` | 20 列，含 `CONSTRAINT_TYPE/R_OWNER/R_CONSTRAINT_NAME/DELETE_RULE/STATUS` |
| `ALL_CONS_COLUMNS` | 5 列，含 `POSITION` |
| `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS` | 均存在；列注释可直接读取，**无需 ASCIISTR 还原** |
| `OBJECT_TYPE` 取值 | `TABLE/VIEW/INDEX/PACKAGE/PACKAGE BODY/PROCEDURE/FUNCTION/SEQUENCE/SYNONYM/TYPE/CLASS/CONSTRAINT/CONTEXT/DSYNOM/SCH` |
| 参数绑定 | `paramstyle=qmark`；`?`、`:1`、`:name`+dict 均可用，服务端统一使用 `?` 位置绑定。裸参数投影返回字符串，谓词绑定按列类型正确转换 |
| 只读事务 | `SET TRANSACTION READ ONLY` 必须是事务首条语句（否则报 `-6510`）；只读事务内 DDL 报 `-6506 试图在只读事务中修改数据` |
| 连接级只读 | `dmPython.connect(..., access_mode=dmPython.DSQL_MODE_READ_ONLY)` 有效，连接内任何写操作报 `-6506` |
| 语句超时 | `connection_timeout=2` 时 3.2s 的查询被中断并报 `-608 请求执行超时`，确认其为语句级超时 |
| 行限制 | 外层 `ROWNUM <= 101`、`ROWNUM` 绑定、`FETCH FIRST` 均可用 |

因此服务端实现采用：连接级 `access_mode` 兜底 + 每事务 `SET TRANSACTION READ ONLY`
（先在连接上 `rollback()` 清空遗留事务，保证 SET 是首条语句）+ 内联整数的外层 `ROWNUM`。

### 补充核实（2026-09-23）：Linux 实机部署与最小权限账号

Linux 目标机为 CentOS（2 核 2 GB），与达梦同机部署。

| 项目 | 结果 |
| --- | --- |
| 离线包安装 | 自带 Miniconda + wheelhouse 全程无需外网；dmPython 2.5.38 内置 DPI 客户端导入成功 |
| 启动预检 | `ready=true`、`DM Database Server 64 V8`、`caseSensitive=true`、UTF-8；预检写入被拒 `-6506` |
| 双传输 | `/mcp`（协商 `2025-03-26`）与 `/sse`（协商 `2025-11-25`）均可用，7 个 `dm_*` 工具齐全 |
| 中文链路 | 联表聚合返回中文字段值无乱码，`DECIMAL`/`BIGINT` 类型正确 |
| 协议容错 | 请求 4 种 `protocolVersion` 均正常协商；不带 `Mcp-Session-Id` 调 `tools/list` 返回 400 `Missing session ID`（符合规范） |
| 新用户默认权限 | 建号即可连接（**无需 `GRANT CREATE SESSION`**）；默认可读 `V$VERSION`、`ID_CODE`、`CASE_SENSITIVE()` 与全部 `ALL_*` 字典视图（**无需 `SELECT ANY DICTIONARY`**） |
| 未授权访问 | 查未授权的表报 `-5504 没有[…]对象的查询权限`；建表报 `-5515 没有创建表权限` |
| 授权语法 | 逐表 `GRANT SELECT ON 模式.表` 支持；`SELECT ANY TABLE` 支持但会一并放开 `V$SESSIONS` 等动态视图；`GRANT SELECT ON SCHEMA` 报 `-2201`；`GRANT SELECT ON 模式.*` 报 `-2007` |
| 最小权限验收 | 用仅授 3 张表 SELECT 的账号跑完整链路，正向 6/6 通过、5 项拒绝用例全部被拒 |
| 平台接入 | 平台在独立网段，**连不到该公网地址**（其出网为目的地白名单，连 80/443 都不通）；改由一台能同时访问两侧的机器做中转后接入成功 |

结论：最小权限账号完全满足服务运行要求，生产必须使用。

## 6. 平台注册与验收

注册地址：`http://<host>:<MCP_PORT>/mcp`（旧版 SSE 客户端用 `/sse`）。
端口由部署配置的 `MCP_PORT` 决定，默认 8082。

验收命令：

```bash
# 协议与工具清单（应返回 7 个 dm_* 工具）
python scripts/probe_mcp.py --url http://<host>:8082/mcp --call --sql "SELECT 1 FROM DUAL"

# 智能体链路
#   1) dm_search_objects(pattern="DM_%", owner="TESTUSER")
#   2) dm_list_tables(owner="TESTUSER", pattern="DM%")
#   3) dm_describe_table(owner="TESTUSER", table_name="DM_CUSTOMER")
#   4) dm_get_comments(owner="TESTUSER", table_name="DM_CUSTOMER")   # 中文注释
#   5) dm_get_relationships(owner="TESTUSER", table_names=["DM_ORDER","DM_ORDER_ITEM"])
#   6) dm_execute_query(sql="SELECT C.CUSTOMER_NAME, SUM(O.AMOUNT) FROM TESTUSER.DM_ORDER O JOIN TESTUSER.DM_CUSTOMER C ON C.CUSTOMER_ID=O.CUSTOMER_ID GROUP BY C.CUSTOMER_NAME")

# 负例（均应 isError=true）
#   DROP TABLE ...            → 拒绝
#   SELECT * FROM V$SESSIONS  → 拒绝
#   SELECT * FROM SYS.SYSOBJECTS → 拒绝
```

自动化验收：`DM_SMOKE=1 python -m pytest -q -m smoke`（4 项，覆盖元数据、中文注释、
外键、联表查询、截断、负例与连接级只读）。

## 7. 已知限制

- `dm_get_comments` 返回 `TABLE_COMMENT` 与 `COLUMN_COMMENT`；DM 中未定义注释的对象为空值。
- `ALL_TABLES.NUM_ROWS` 依赖统计信息，未收集统计时为 `null`，属正常现象。
- 物化视图在 `dm_search_objects` 白名单中保留 `MATERIALIZED VIEW`，但当前实例的
  `ALL_OBJECTS` 未出现该取值；如目标库以其他名称登记，可用 `object_types` 显式传入前先核实。
- 服务端不缓存元数据；每次调用都会访问数据字典，保证描述与库内实时一致。
