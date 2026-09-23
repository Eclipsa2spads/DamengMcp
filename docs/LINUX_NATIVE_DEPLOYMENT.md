# 达梦 MCP 部署文档（CentOS 7 x86-64 离线原生）

从**离线包已就绪**开始，一路做到智能体平台能正常调用。

## 0. 手上需要有的东西

```
dameng-mcp-linux-native-centos7-x86_64.tar.gz
dameng-mcp-linux-native-centos7-x86_64.tar.gz.sha256
```

包内自带（目标机**不需要 Python、不需要外网、不需要 Docker**）：

- Miniconda3 Python 3.11 运行时（`vendor/`）
- 全部依赖 wheel（`wheelhouse/`，`manylinux2014_x86_64`，glibc ≥ 2.17）
- `dmPython 2.5.38`：wheel 自带达梦 DPI 客户端（`libdmdpi`）与 DM SSL 库，
  **无需安装达梦客户端软件**
- 服务代码、systemd 单元、安装与运维脚本，以及本文件（包内 `README.md`）

**目标机要求**：CentOS 7 或 8 x86_64、glibc ≥ 2.17、systemd、具备 root、
`/opt` 剩余空间 ≥ 1 GiB。

服务以独立系统用户 `dameng-mcp` 运行，由 systemd 托管，同时提供
Streamable HTTP（`/mcp`）与旧版 HTTP+SSE（`/sse`、`/messages/`）。

---

## 1. 先准备好这几项信息

| # | 信息 | 填到哪 | 说明 |
|---|---|---|---|
| 1 | 达梦地址与端口 | `DM_HOST` / `DM_PORT` | 内网地址，端口默认 5236 |
| 2 | 达梦只读账号与密码 | `DM_USER` / `DM_PASSWORD` | **不要用 SYSDBA**，见下方建号 SQL |
| 3 | 要暴露的业务模式 | `DM_ALLOWED_OWNERS` | 逗号分隔，只能填业务模式 |
| 4 | 服务端口 | `MCP_PORT` | 默认 8082，被占用时改（如 8084） |
| 5 | 智能体平台出口 IP | 防火墙规则 | 用于收窄来源，见第 7 节 |

### 只读账号（实测过的建号 SQL）

```sql
-- 建号即可连接，达梦不需要 GRANT CREATE SESSION
CREATE USER DM_MCP_READ IDENTIFIED BY "<强密码>";

-- 逐表 / 逐视图授予 SELECT
GRANT SELECT ON APP_SCHEMA.SOME_TABLE TO DM_MCP_READ;
```

表或视图多的时候，先生成语句、核对无误再执行：

```sql
SELECT 'GRANT SELECT ON ' || OWNER || '.' || TABLE_NAME || ' TO DM_MCP_READ;'
  FROM ALL_TABLES WHERE OWNER = 'APP_SCHEMA';
SELECT 'GRANT SELECT ON ' || OWNER || '.' || VIEW_NAME || ' TO DM_MCP_READ;'
  FROM ALL_VIEWS WHERE OWNER = 'APP_SCHEMA';
```

授权时避开三个坑（均在 DM8 实机验证）：

- **不需要额外字典权限**：新用户默认就能读 `V$VERSION`、`ID_CODE`、
  `CASE_SENSITIVE()` 和全部 `ALL_*` 字典视图，无需 `SELECT ANY DICTIONARY`，
  也不必单独授 `V$VERSION`
- **达梦没有模式级授权**：`GRANT SELECT ON SCHEMA <模式>` 报 `-2201 无效的数据库对象`，
  `GRANT SELECT ON <模式>.*` 报 `-2007 语法分析出错`
- **不要用 `SELECT ANY TABLE` 图省事**：它会把 `V$SESSIONS` 这类动态性能视图一并放开

授权后自检一遍：查授权表应通过，查未授权表应报 `-5504 没有[…]对象的查询权限`。

---

## 2. 传输、校验、解压

```bash
# 在构建机执行
scp dameng-mcp-linux-native-centos7-x86_64.tar.gz* root@<target>:/root/

# 登录目标机
ssh root@<target>
cd /root

# 校验完整性，必须输出 OK
sha256sum -c dameng-mcp-linux-native-centos7-x86_64.tar.gz.sha256

# 解压
tar -xzf dameng-mcp-linux-native-centos7-x86_64.tar.gz
cd dameng-mcp-linux-native
```

---

## 3. 配置

```bash
cp .env.linux-native.example .env.linux
vi .env.linux
chmod 600 .env.linux
```

按第 1 节准备的信息填写。各变量含义：

| 变量 | 说明 |
|---|---|
| `DM_HOST` / `DM_PORT` | 达梦实例地址与端口（默认 5236） |
| `DM_USER` / `DM_PASSWORD` | 只读业务账号，**不要使用 SYSDBA** |
| `DM_ALLOWED_OWNERS` | 允许访问的业务模式白名单，逗号分隔 |
| `DM_DENIED_SCHEMAS` | 禁止访问的系统模式，默认已含 `SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG` |
| `DM_REQUIRED_VERSION_PREFIX` | 版本前缀校验，默认 `V8`；留空表示跳过 |
| `MCP_PORT` | 服务端口，默认 `8082`；改名后安装、自检、探活脚本都会跟随该取值 |

以下规则由服务启动时强制校验，**不满足会直接起不来**：

- `DM_ALLOWED_OWNERS` 不得为空，且不得与 `DM_DENIED_SCHEMAS` 有交集
- `DM_POOL_MAX >= DM_POOL_MIN`
- `MCP_PATH` / `MCP_SSE_PATH` / `MCP_MESSAGE_PATH` 三者互不相同
- `DM_ENCODING` 仅可取 `UTF8` / `GBK` / `GB18030`（与数据库字符集匹配）

---

## 4. 安装

```bash
bash scripts/check-linux-native-host.sh          # 只检查，不改动系统
bash scripts/install-linux-native.sh .env.linux  # 需要 root
```

安装脚本依次完成：创建 `dameng-mcp` 用户 → 安装内置 Python 3.11 →
从 `wheelhouse/` 离线安装依赖 → 部署代码到 `/opt/axis-dameng-mcp` →
写配置到 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`（0640，root:dameng-mcp）→
安装 systemd 单元 → 跑达梦连通性与只读自检 → 启动服务并在 30 秒内探活 `/mcp`。

成功输出形如：

```
[OK] Axis Dameng MCP native service is running.
[OK] Streamable HTTP: http://<host-ip>:<MCP_PORT>/mcp
[OK] HTTP+SSE:       http://<host-ip>:<MCP_PORT>/sse
```

装完 `/root/dameng-mcp-linux-native` 就没用了（依赖已进 `/opt`），可以删掉。

---

## 5. 验收（必做）

```bash
axis-dameng-mcp status     # systemd 应为 active (running)
axis-dameng-mcp check      # 预检：ready=true、版本 V8、写操作被 -6506 拒绝
axis-dameng-mcp probe      # 协议握手 + 列出 7 个 dm_* 工具 + 真实查询一次
```

`probe` 输出里应能看到 `protocol`、7 个工具名，以及一次 `dm_execute_query` 的结果。

再按下面两组用例确认行为正确（下表中的实测结果来自 DM8 实机）：

**正向**（元数据 → 查询链路，用真实业务表替换示例表名）

| 调用 | 预期 |
|---|---|
| `dm_search_objects(pattern="DM_%", owner="<模式>")` | 返回对象列表 |
| `dm_list_tables(owner="<模式>")` | 返回表列表 |
| `dm_describe_table(owner="<模式>", table_name="<表>")` | 返回列定义 |
| `dm_get_comments(owner="<模式>", table_name="<表>")` | 返回中文注释 |
| `dm_get_relationships(owner="<模式>", table_names=["<表A>","<表B>"])` | 返回外键关系 |
| `dm_execute_query(sql="<含中文的联表聚合>")` | 返回结果且中文不乱码 |

**反向**（必须全部被拒绝）

| 输入 | 拒绝方式 |
|---|---|
| `DROP TABLE ...` | 只允许单条 SELECT 或 WITH |
| `INSERT ...` | 只允许单条 SELECT 或 WITH |
| `SELECT * FROM V$SESSIONS` | 系统与动态性能视图不允许 |
| `SELECT * FROM SYS.SYSOBJECTS` | 模式不允许 |
| `dm_list_tables(owner="SYSDBA")` | Owner 不在允许列表 |

---

## 6. 平台注册

先本机确认端点通：

```bash
curl -sS -X POST http://127.0.0.1:<MCP_PORT>/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

然后在智能体平台的 MCP 管理里新增：

```
名称：DamengMCP
URL：http://<host-ip>:<MCP_PORT>/mcp
协议：Streamable HTTP
```

旧版 SSE 客户端用 `http://<host-ip>:<MCP_PORT>/sse`。一个智能体只需配其中一个地址。

注册后让它跑一句中文问答（例如「<模式> 下有哪些表」），能正常返回就算打通。

---

## 7. 防火墙

**MCP 端点没有认证**，能访问它就等于拿到该账号的数据库读权限，所以只放行平台出口 IP，
不要对 `0.0.0.0/0` 开放。

```bash
# 先确认实际来源地址
journalctl -u axis-dameng-mcp | grep source_ip

# 再按来源收窄（firewalld 未安装时跳过，用云安全组代替）
firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=<平台出口IP>/32 port port=<MCP_PORT> protocol=tcp accept'
firewall-cmd --reload
firewall-cmd --list-rich-rules
```

解压目录里带了一个封装脚本 `scripts/configure-firewall-linux.sh <平台出口IP>/32 <MCP_PORT>`，
效果与上面等价（注意它只存在于解压目录，不会被装到 `/opt`）。

两个注意点：

- 服务器内 firewalld 与**云安全组是两层，都要管**；部分镜像根本不装 firewalld，
  此时只剩安全组这一层
- 达梦端口（默认 5236）**不要对公网开放**

---

## 8. 日常运维

```bash
axis-dameng-mcp status      # systemd 状态
axis-dameng-mcp logs        # 最近 100 行日志
axis-dameng-mcp check       # 连通性 + 版本 + 只读自检（JSON）
axis-dameng-mcp probe       # 走 /mcp 与 /sse 各调用一次
axis-dameng-mcp restart
axis-dameng-mcp stop

journalctl -u axis-dameng-mcp -f      # 实时日志
```

日志中每次工具调用都会记录 `request_id`、`source_ip`、`tool`、`elapsed_ms`、`rows`。

---

## 9. 升级与回滚

- **升级**：解压新包，重跑第 4 步即可。安装脚本会先停服务、原地替换代码与依赖，
  并保留 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`。升级后重跑第 5 节验收。
- **回滚**：保留上一版 `tar.gz` 与 `.env.linux`，重新执行上一版安装脚本。
  离线包不依赖网络，回滚不受网络影响。
- **卸载**：删 `/opt/axis-dameng-mcp`、`/etc/axis-dameng-mcp`、
  `/etc/systemd/system/axis-dameng-mcp.service`、`/usr/local/sbin/axis-dameng-mcp`，
  再 `userdel dameng-mcp`。

---

## 10. 故障排查

| 现象 | 处理 |
|---|---|
| `dmPython` 导入失败 | 确认 `wheelhouse/` 里有 `dmpython-*-manylinux2014_x86_64.whl`，且目标机 glibc ≥ 2.17 |
| 达梦加密模块报错（如 `-70089`） | 单元文件已用 `LD_LIBRARY_PATH` 指向 wheel 内置的 `dmssl` 目录；`axis-dameng-mcp check` 会打印真实错误 |
| 服务启动失败 | `journalctl -u axis-dameng-mcp -n 100 --no-pager`；常见原因是 `DM_PASSWORD` 没替换、`DM_ALLOWED_OWNERS` 与 `DM_DENIED_SCHEMAS` 冲突 |
| `Unable to determine the Dameng version` | 确认账号能执行 `SELECT BANNER FROM V$VERSION`（默认权限即可，无需额外授权）；确有必要时把 `DM_REQUIRED_VERSION_PREFIX` 置空跳过校验 |
| 端口被占用 | `ss -lntp \| grep <端口>`；改 `.env.linux` 的 `MCP_PORT` 换端口后重装 |
| 中文乱码 | 确认 `DM_ENCODING` 与数据库字符集匹配（UTF-8 库用 `UTF8`，GBK 库用 `GBK`） |
| 平台注册报 `failed to initialize` | 先在服务器上 `curl 127.0.0.1:<MCP_PORT>/mcp` 确认服务本身正常，再看防火墙是否放行平台来源、平台所在网段能否路由到该地址 |
