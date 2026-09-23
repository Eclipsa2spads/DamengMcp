# 达梦 DM8 MCP（Windows / Linux）

面向达梦数据库 DM8 的只读 MCP 服务。服务端使用 Python 3.11 与 dmPython 2.5.38，
通过 Streamable HTTP 与旧版 HTTP+SSE 同时暴露，供公司智能体完成 Text2SQL：
先用元数据工具理解库表结构与业务含义，再由模型生成 SQL，最后由本服务做安全校验与执行。

接口契约、给智能体的提示要点和实机核实记录见
[`docs/DAMENG_MCP_HANDOFF.md`](docs/DAMENG_MCP_HANDOFF.md)。

    http://<host>:8082/mcp        # Streamable HTTP
    http://<host>:8082/sse        # 旧版 HTTP+SSE
    http://<host>:8082/messages/  # SSE 消息端点（由 SSE 握手返回）

## 安全边界

- 只接受单条 SELECT 或最终产生查询的 WITH；SQLGlot 按 Oracle 方言解析 AST
  （DM8 语法兼容 Oracle，无独立 dameng 方言）。
- 在连接数据库前拒绝：注释、分号、多语句、`FOR UPDATE`、DML、DDL、事务、PL/SQL、
  数据库链接、`V$`/`GV$`/`DV$`/`DBA_` 系统视图、非白名单函数与非白名单 Schema。
- 连接层双重只读：dmPython `access_mode=DSQL_MODE_READ_ONLY`（连接级）+ 每个事务
  `SET TRANSACTION READ ONLY`（事务级），归还连接池前回滚。
- 行限制使用外层 `ROWNUM`，行数上限为服务端钳位后的整数内联，不接受外部拼接。
- 默认最多 200 行、SQL 10000 字符、单元格 10000 字符、结果 1 MiB、语句超时 30 秒
  （`DM_QUERY_TIMEOUT_SECONDS`，映射到 dmPython `connection_timeout`，实测可中断超时语句）。
- 元数据只读取 `ALL_*` 数据字典，并受 `DM_ALLOWED_OWNERS` 白名单与
  `DM_DENIED_SCHEMAS` 黑名单限制。

数据库专用账号仍是最终权限边界：只授予业务表/视图的 `SELECT`（以及 `V$VERSION`
的 `SELECT` 用于自检），不要使用 SYSDBA，不要授予 DML/DDL。

## 工具

| 工具 | 作用 |
| --- | --- |
| `dm_execute_query(sql, max_rows=100)` | 执行单条只读 SQL |
| `dm_list_tables(owner?, pattern?)` | 列出白名单模式下的表 |
| `dm_describe_table(owner, table_name)` | 列类型、可空性、默认值、主键成员 |
| `dm_search_objects(pattern, owner?, object_types?)` | 按名称搜索表/视图/序列/同义词/存储对象 |
| `dm_list_procedures(owner?, pattern?)` | 列出过程、函数、包 |
| `dm_get_comments(owner, table_name)` | 读取表/字段注释（中文正常返回，不读业务数据） |
| `dm_get_relationships(owner, table_names)` | 读取表间外键关联字段 |

查询统一返回 `{columns:[{name,type}], rows, rowCount, truncated, elapsedMs}`。

## Windows 部署

1. 安装 Python 3.11 x64（dmPython 的 Windows wheel 自带达梦 DPI，无需安装达梦客户端）。
2. 创建虚拟环境并安装依赖：

       py -3.11 -m venv .venv
       .\.venv\Scripts\python.exe -m pip install -r requirements.txt

3. 复制配置并填入只读账号，然后收紧 `.env` 权限：

       Copy-Item .env.example .env
       .\scripts\Protect-Env.ps1

4. 先做连通性、版本与只读自检（只有连库、版本前缀与“写入被拒绝”全部通过才会成功）：

       .\scripts\Check-Dameng.ps1

5. 启动并探测：

       .\scripts\Start-DamengMcp.ps1
       .\scripts\Probe-Mcp.ps1 -Call
       .\scripts\Probe-Sse.ps1 -Call

前台调试使用 `.\scripts\Start-DamengMcp.ps1 -Foreground`，停止使用
`.\scripts\Stop-DamengMcp.ps1`。PID 文件为 `dameng-mcp.pid`，日志在 `logs\`。

## 测试

    .\.venv\Scripts\python.exe -m pytest -q                 # 单元测试，全部 mock，不连库
    $env:DM_SMOKE=1; .\.venv\Scripts\python.exe -m pytest -q -m smoke   # 实机端到端

实机 smoke 需要 `.env` 指向可达的达梦实例；演示数据用
`.\scripts\setup_test_schema.py` 一键创建（`--drop` 可删除）。

诊断工具：`scripts\probe_dictionary.py` 会打印版本、字符集、大小写敏感、
`ALL_*` 字典视图列、绑定风格、只读事务行为与超时语义
（`--write-probe --timeout-probe` 打开写入与慢查询探测）。

## Linux（CentOS 7 x86-64 离线原生部署）

内置 Python 3.11 与全部 `manylinux2014_x86_64` wheel（glibc ≥ 2.17），
dmPython wheel 自带达梦 DPI 与 DM SSL 库，无需安装达梦客户端；
服务以 `dameng-mcp` 用户由 systemd 托管。

构建离线包（Windows 构建机，需要一次联网下载）：

    .\scripts\Download-DmWheels.ps1 -WithMiniconda
    .\scripts\build-linux-native-bundle.ps1

产物：`dist\dameng-mcp-linux-native-centos7-x86_64.tar.gz(.sha256)`。

拿到包之后的完整部署步骤（前置信息、只读账号建号 SQL、安装、验收用例、平台注册、
防火墙与排障）见 [`docs/LINUX_NATIVE_DEPLOYMENT.md`](docs/LINUX_NATIVE_DEPLOYMENT.md)，
该文件同时作为 `README.md` 打进离线包。

## 与 Oracle 版（axis-oracle-mcp）的差异

| 维度 | Oracle 版 | 达梦版 |
| --- | --- | --- |
| 驱动 | python-oracledb Thick + Instant Client | dmPython 2.5.38，wheel 自带 DPI |
| 连接池 | `oracledb.create_pool` | `DBUtils.PooledDB` + 信号量限流与等待超时 |
| 只读机制 | 每事务 `SET TRANSACTION READ ONLY` | 连接级 `access_mode` + 每事务 `SET TRANSACTION READ ONLY` |
| 语句超时 | `connection.call_timeout` | `connection_timeout`（实测可中断执行中的语句） |
| 参数绑定 | 命名绑定 `:name` | 位置绑定 `?`（dmPython paramstyle=qmark） |
| 分页 | 外层 `ROWNUM <= :bind` | 外层 `ROWNUM <= 整数`（避免绑定兼容性风险） |
| 注释编码 | 以 `ASCIISTR` 传输后在 Python 侧还原 | 直接读取，中文往返正常（已验证） |
| 额外拒绝项 | — | `V$`/`GV$`/`DV$`/`DBA_` 系统视图 |
| 环境变量前缀 | `ORACLE_*` | `DM_*`（传输层 `MCP_*` 保持同名） |
| 工具前缀 | `ora_*` | `dm_*` |
