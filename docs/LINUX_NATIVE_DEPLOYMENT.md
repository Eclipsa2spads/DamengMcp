# 达梦 MCP 部署文档

面向达梦 DM8 的**只读** MCP 服务，为智能体提供真实元数据与只读 SQL 执行。

## 1. 运行环境

| 项 | 要求 |
|---|---|
| 目标机 | CentOS 7 / 8 x86_64，glibc ≥ 2.17，systemd，root，`/opt` 剩余空间 ≥ 1 GiB |
| 目标机需自备 | **什么都不用**：不需要 Python，不需要外网，不需要 Docker，不需要达梦客户端 |
| 达梦 | DM8（`V$VERSION` 为 `DM Database Server 64 V8`） |

离线包自带：

- Miniconda3 Python 3.11 运行时（`vendor/`）
- 全部依赖 wheel（`wheelhouse/`，`manylinux2014_x86_64`，glibc ≥ 2.17）
- `dmPython 2.5.38`，wheel 内含达梦 DPI 客户端（`libdmdpi`）与 DM SSL 库
- 服务代码、systemd 单元、安装与运维脚本

服务以系统用户 `dameng-mcp` 运行，由 systemd 托管，同时提供
Streamable HTTP（`/mcp`）与 HTTP+SSE（`/sse`、`/messages/`）两种传输。

安装位置：

| 内容 | 路径 |
|---|---|
| 程序根目录 | `/opt/axis-dameng-mcp` |
| Python 运行时 | `/opt/axis-dameng-mcp/python` |
| 应用代码 | `/opt/axis-dameng-mcp/app` |
| 配置文件 | `/etc/axis-dameng-mcp/axis-dameng-mcp.env`（0640，root:dameng-mcp） |
| systemd 单元 | `/etc/systemd/system/axis-dameng-mcp.service` |
| 运维命令 | `/usr/local/sbin/axis-dameng-mcp` |

## 2. 功能

**只读**：不写入、不改结构。三层强制 —— 账号只授 `SELECT`、连接级
`access_mode=DSQL_MODE_READ_ONLY`、每事务 `SET TRANSACTION READ ONLY`。

自然语言转 SQL 由智能体完成，本服务只提供真实元数据与最终安全执行边界。

| 工具 | 用途 |
|---|---|
| `dm_search_objects` | 按名称模糊搜索对象（表、视图、序列、过程、函数等） |
| `dm_list_tables` | 列出表 |
| `dm_describe_table` | 查看列定义：类型、可空、默认值、主键 |
| `dm_get_comments` | 查看表注释与列注释（中文可用） |
| `dm_get_relationships` | 查看表之间的外键关联 |
| `dm_list_procedures` | 列出存储过程 / 函数 / 包 |
| `dm_execute_query` | 执行单条只读 SQL |

典型链路：`dm_search_objects` → `dm_list_tables` → `dm_describe_table` →
`dm_get_comments` → `dm_get_relationships` → `dm_execute_query`。

## 3. 部署

### 3.0 一键安装（可选）

解压后可以直接用向导，它会依次问 6 项必填配置，校验后写成 `.env.linux`，
再调用 3.3 的安装脚本：

```bash
bash scripts/quick-install.sh
```

也支持非交互（无人值守），答案放参数或环境变量里：

```bash
DM_PASSWORD='<密码>' bash scripts/quick-install.sh --yes \
    --host 10.1.2.3 --user DM_MCP_READ --owners APP_SCHEMA --mcp-port 8084
```

向导只收集配置、不重复安装逻辑；6 项以外的参数一律用默认值。不想要向导就按下面
3.1–3.4 手工执行。密码不能用命令行参数传（会进 shell 历史），只能用
`DM_PASSWORD` 环境变量、`--password-stdin` 或交互输入。

### 3.1 传输并校验

构建机上执行：

```bash
scp dameng-mcp-linux-native-centos7-x86_64.tar.gz* root@<target>:/root/
```

目标机上执行：

```bash
cd /root
sha256sum -c dameng-mcp-linux-native-centos7-x86_64.tar.gz.sha256   # 必须输出 OK
tar -xzf dameng-mcp-linux-native-centos7-x86_64.tar.gz
cd dameng-mcp-linux-native
```

### 3.2 准备配置

```bash
cp .env.linux-native.example .env.linux
vi .env.linux          # 按第 4 节填写
chmod 600 .env.linux
```

### 3.3 安装

```bash
bash scripts/check-linux-native-host.sh          # 只检查，不改动系统
bash scripts/install-linux-native.sh .env.linux  # 需要 root
```

安装脚本依次完成：创建 `dameng-mcp` 用户 → 安装内置 Python 3.11 →
从 `wheelhouse/` 离线安装依赖 → 部署代码到 `/opt/axis-dameng-mcp` →
写入配置文件 → 安装 systemd 单元 → 跑达梦连通性与只读自检 →
启动服务并在 30 秒内探活 `/mcp`。

成功输出形如：

```
[OK] Axis Dameng MCP native service is running.
[OK] Streamable HTTP: http://<host-ip>:<MCP_PORT>/mcp
[OK] HTTP+SSE:       http://<host-ip>:<MCP_PORT>/sse
```

装完 `/root/dameng-mcp-linux-native` 已无用（依赖已进 `/opt`），可删除。

### 3.4 自检

```bash
axis-dameng-mcp check      # 返回 JSON：ready=true、版本 V8、写操作被 -6506 拒绝
axis-dameng-mcp probe      # 协议握手 + 列出 7 个工具 + 真实查询一次
```

## 4. 配置

配置文件为 `.env` 格式，安装时写入 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DM_HOST` | 必填 | 达梦地址 |
| `DM_PORT` | `5236` | 达梦端口 |
| `DM_USER` | 必填 | **只读账号**，不要用 SYSDBA |
| `DM_PASSWORD` | 必填 | 账号密码 |
| `DM_ENCODING` | `UTF8` | 仅可取 `UTF8` / `GBK` / `GB18030`，与数据库字符集匹配 |
| `DM_ALLOWED_OWNERS` | 同 `DM_USER` | 允许访问的业务模式白名单，逗号分隔 |
| `DM_DENIED_SCHEMAS` | `SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG` | 禁止访问的系统模式 |
| `DM_REQUIRED_VERSION_PREFIX` | `V8` | 版本前缀校验，留空则跳过 |
| `DM_ALLOWED_FUNCTIONS` | 内置常用函数 | 允许在 SQL 中使用的函数白名单 |
| `DM_POOL_MIN` / `DM_POOL_MAX` | `1` / `3` | 连接池上下限 |
| `DM_POOL_WAIT_TIMEOUT_MS` | `5000` | 取连接等待上限 |
| `DM_QUERY_TIMEOUT_SECONDS` | `30` | 语句级超时 |
| `DM_MAX_ROWS` | `200` | 单次返回行数上限 |
| `DM_MAX_SQL_LENGTH` | `10000` | SQL 长度上限 |
| `DM_MAX_CELL_CHARS` | `10000` | 单单元格字符上限 |
| `DM_MAX_RESULT_BYTES` | `1048576` | 单次结果字节上限 |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8082` | 监听地址与端口 |
| `MCP_PATH` / `MCP_SSE_PATH` / `MCP_MESSAGE_PATH` | `/mcp` / `/sse` / `/messages/` | 三个端点路径 |

以下规则在启动时强制校验，不满足直接起不来：

- `DM_ALLOWED_OWNERS` 不得为空，且不得与 `DM_DENIED_SCHEMAS` 有交集
- `DM_POOL_MAX >= DM_POOL_MIN`
- `MCP_PATH` / `MCP_SSE_PATH` / `MCP_MESSAGE_PATH` 三者互不相同

配置改动后重启生效：`axis-dameng-mcp restart`。


## 5. 指令

工具与入参：

| 工具 | 必填 | 可选 |
|---|---|---|
| `dm_execute_query` | `sql` | `max_rows`（默认 100，上限 `DM_MAX_ROWS`） |
| `dm_list_tables` | — | `owner`、`pattern` |
| `dm_describe_table` | `owner`、`table_name` | — |
| `dm_get_comments` | `owner`、`table_name` | — |
| `dm_get_relationships` | `owner`、`table_names`（最多 20 张） | — |
| `dm_search_objects` | `pattern` | `owner`、`object_types` |
| `dm_list_procedures` | — | `owner`、`pattern` |

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

- `type` 为 dmPython 类型名（`STRING` / `NUMBER` / `BIGINT` / `DECIMAL` / `DATE` /
  `TIMESTAMP` / `CLOB` / `BLOB` 等）；`BLOB` 以 `<BLOB n bytes>` 占位。
- 超过行数或字节上限时 `truncated=true`。
- 被拒绝时返回 `isError=true`，message 说明原因。以下均会被拒绝：
  非 SELECT 语句、多语句、注释、`FOR UPDATE`、系统与动态性能视图（`V$` / `DBA_`）、
  系统模式、白名单外的函数。

服务端运维命令：

```bash
axis-dameng-mcp status | logs | check | probe | restart | stop
journalctl -u axis-dameng-mcp -f        # 实时日志
```
