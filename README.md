# 达梦 DM8 MCP

面向达梦数据库 DM8 的**只读** MCP 服务。为智能体提供真实元数据与安全的只读 SQL 执行，
用于实现 Text2SQL：先理解库表结构与业务含义，再由模型生成 SQL，最后由本服务校验并执行。

## 运行环境

| 项 | 要求 |
|---|---|
| 达梦 | DM8（`V$VERSION` 为 `DM Database Server 64 V8`） |
| Linux | CentOS 7 / 8 x86_64，glibc ≥ 2.17，systemd。用离线原生包部署，目标机**不需要** Python、外网、Docker 或达梦客户端 |
| Windows | Python 3.11 x64（dmPython 的 Windows wheel 自带达梦 DPI） |
| 服务形态 | 系统用户 `dameng-mcp` + systemd 托管；同时提供 Streamable HTTP 与旧版 HTTP+SSE |

```
http://<host>:8082/mcp        # Streamable HTTP
http://<host>:8082/sse        # 旧版 HTTP+SSE
http://<host>:8082/messages/  # SSE 消息端点（由 SSE 握手返回）
```

## 工具

| 工具 | 必填 | 可选 |
| --- | --- | --- |
| `dm_execute_query` | `sql` | `max_rows`（默认 100，上限 `DM_MAX_ROWS`） |
| `dm_list_tables` | — | `owner`、`pattern` |
| `dm_describe_table` | `owner`、`table_name` | — |
| `dm_get_comments` | `owner`、`table_name` | — |
| `dm_get_relationships` | `owner`、`table_names`（最多 20 张） | — |
| `dm_search_objects` | `pattern` | `owner`、`object_types` |
| `dm_list_procedures` | — | `owner`、`pattern` |

查询与元数据工具统一返回：

```json
{"columns": [{"name": "CUSTOMER_NAME", "type": "STRING"}], "rows": [["上海长江实业"]],
 "rowCount": 1, "truncated": false, "elapsedMs": 137}
```

`type` 为 dmPython 类型名；`BLOB` 以 `<BLOB n bytes>` 占位；超出上限时 `truncated=true`。

## 安全边界

- 只接受单条 SELECT，或最终产出查询的 WITH；用 SQLGlot 按 Oracle 方言解析 AST
  （DM8 语法兼容 Oracle，无独立 dameng 方言）。
- 连库前拒绝：注释、分号、多语句、`FOR UPDATE`、DML、DDL、事务、PL/SQL、数据库链接、
  `V$`/`GV$`/`DV$`/`DBA_` 系统视图、白名单外的函数与 Schema。
- 三层只读：数据库账号只授 `SELECT`、连接级 `access_mode=DSQL_MODE_READ_ONLY`、
  每事务 `SET TRANSACTION READ ONLY`（归还连接池前回滚）。
- 上限：200 行、SQL 10000 字符、单元格 10000 字符、结果 1 MiB、语句超时 30 秒
  （`DM_QUERY_TIMEOUT_SECONDS` 映射到 dmPython `connection_timeout`，实测可中断执行中的语句）。
- 行限制用外层 `ROWNUM` 内联整数，不接受外部拼接。
- 元数据只读 `ALL_*` 数据字典，并受 `DM_ALLOWED_OWNERS` 白名单与
  `DM_DENIED_SCHEMAS` 黑名单约束。

## 部署

### Linux（CentOS 7 / 8 离线原生包）

在 Windows 构建机产出离线包（首次需要一次联网下载）：

```powershell
.\scripts\Download-DmWheels.ps1 -WithMiniconda
.\scripts\build-linux-native-bundle.ps1
```

产物为 `dist\dameng-mcp-linux-native-centos7-x86_64.tar.gz(.sha256)`。

目标机的环境要求、部署步骤、配置项与工具指令见
[`docs/LINUX_NATIVE_DEPLOYMENT.md`](docs/LINUX_NATIVE_DEPLOYMENT.md)——
该文件同时作为 `README.md` 打进离线包。解压后也可用向导收集配置：

```bash
bash scripts/quick-install.sh
```

### Windows

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env            # 填入达梦只读账号
.\scripts\Protect-Env.ps1              # 收紧 .env 权限
.\scripts\Check-Dameng.ps1             # 连通性 + 版本 + 只读自检
.\scripts\Start-DamengMcp.ps1          # 启动
.\scripts\Probe-Mcp.ps1 -Call          # 探测
```

`-Foreground` 前台调试，`.\scripts\Stop-DamengMcp.ps1` 停止；
PID 文件为 `dameng-mcp.pid`，日志在 `logs\`。

## 配置

Windows 用 `.env`，离线包用 `.env.linux`（可从 `.env.linux-native.example` 复制，
或由 `quick-install.sh` 生成）。完整变量表见部署文档第 4 节，常用项：

| 变量 | 说明 |
| --- | --- |
| `DM_HOST` / `DM_PORT` | 达梦地址与端口，默认端口 5236 |
| `DM_USER` / `DM_PASSWORD` | 达梦**只读账号**，不要用 SYSDBA |
| `DM_ALLOWED_OWNERS` | 允许访问的业务模式白名单，逗号分隔 |
| `DM_DENIED_SCHEMAS` | 禁止访问的系统模式 |
| `MCP_HOST` / `MCP_PORT` | 服务监听地址与端口，默认 `0.0.0.0` / `8082` |

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q                              # 单元测试，全部 mock，不连库
$env:DM_SMOKE=1; .\.venv\Scripts\python.exe -m pytest -q -m smoke    # 实机端到端
```

实机 smoke 需要 `.env` 指向可达的达梦实例；演示数据用
`.\scripts\setup_test_schema.py` 一键创建（`--drop` 删除）。
诊断脚本 `scripts\probe_dictionary.py` 会打印版本、字符集、大小写敏感、`ALL_*` 字典视图列、
绑定风格、只读事务行为与超时语义（`--write-probe --timeout-probe` 打开写入与慢查询探测）。

## 文档

- [`docs/LINUX_NATIVE_DEPLOYMENT.md`](docs/LINUX_NATIVE_DEPLOYMENT.md)——
  运行环境、功能、部署、配置、指令，同时作为 `README.md` 打进离线包
- [`docs/DAMENG_MCP_HANDOFF.md`](docs/DAMENG_MCP_HANDOFF.md)——
  接口契约、给智能体的生成规则、实机核实记录
