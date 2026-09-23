# 达梦 MCP 部署规范

本文规定**在哪部署、装之前要准备什么、装到什么程度算合格、如何变更与回滚**。

- 具体操作步骤见 `docs/LINUX_NATIVE_DEPLOYMENT.md`（操作手册）
- 接口契约与 Agent 使用规则见 `docs/DAMENG_MCP_HANDOFF.md`

文中标注「实测」的结论均在实机验证过。验证环境：DM8 `0x7000d`，
BUILD `03134284336-20250117-257733-20132`，`CASE_SENSITIVE()=1`，UTF-8。

---

## 1. 环境分层（强制）

| 环境 | 定位 | 主机 | 数据源 | 端口 | 使用方 |
|---|---|---|---|---|---|
| 生产 | 正式服务 | 内网服务器（待定） | 内网达梦库 | 待定 | 智能体平台 |
| 测试 | 功能验证 | `82.156.246.154`（腾讯云 CentOS 2C2G，与达梦同机） | 同机达梦测试实例 | 8084 | 研发 |
| 开发 | 本机调试 / 演示 | 开发机 Windows `10.242.2.115` | 云上达梦测试实例 | 8082 | 研发 |

规定：

1. **生产必须部署在能直连达梦库的内网服务器上。** 禁止把公网穿透、反向隧道、
   开发机中转当作生产方案。
2. 生产库凭据不得进入测试 / 开发环境配置，反之亦然。
3. 测试环境验证完成后停服，把内存让给达梦（该云主机仅 2 GB）：
   `systemctl disable --now axis-dameng-mcp`
4. 交付形态统一为 **Linux 离线原生包**（自带 Miniconda + wheelhouse，
   目标机无需外网），不使用 Docker。Windows 版本仅用于开发与演示。

> **实测教训（2026-09-23）：** 智能体平台在内网，**连不到公网 IP**。
> `10.253.x` 网段出网是目的白名单 —— 连该公网 IP 的 80 / 443 都不通，
> 换端口无效。所以「平台连公网 MCP」这条路不成立，生产只能内网部署。

---

## 2. 部署前置条件（逐项确认后再动手）

**目标机要求**

- CentOS 7 / 8 x86_64，glibc ≥ 2.17，systemd，具备 root
- `/opt` 可用空间 ≥ 1 GiB
- **无需外网**：离线包自带 Python 运行时与全部依赖

**必须先拿到的信息**

| # | 信息 | 用途 |
|---|---|---|
| 1 | 达梦地址与端口 | `DM_HOST` / `DM_PORT`（生产填内网地址） |
| 2 | 达梦**只读账号**与密码 | `DM_USER` / `DM_PASSWORD`，见 §3，禁止 SYSDBA |
| 3 | 需要暴露的业务模式名 | `DM_ALLOWED_OWNERS` |
| 4 | 服务端口 | `MCP_PORT`（须避开已占用端口） |
| 5 | 智能体平台出口 IP | 收窄防火墙，见 §6 |
| 6 | 离线包 + SHA256 | 安装介质 |

---

## 3. 达梦只读账号规范

### 3.1 实测结论

- 普通用户**默认就能读** `V$VERSION`、`ID_CODE`、`CASE_SENSITIVE()` 以及全部
  `ALL_*` 字典视图 —— **不需要** `SELECT ANY DICTIONARY`，
  服务的启动预检在最小权限账号下即可正常工作。
- 访问未授权的表报 `[CODE:-5504] 没有[模式.表]对象的查询权限`。
- 达梦**不支持模式级授权**：`GRANT SELECT ON SCHEMA <模式>` 报
  `-2201 无效的数据库对象`；`GRANT SELECT ON <模式>.*` 报 `-2007 语法分析出错`。

### 3.2 建号与授权

```sql
-- 1) 建号（密码自行替换，勿用弱口令）
CREATE USER DM_MCP_READ IDENTIFIED BY "********";

-- 2) 单表最小授权（推荐）
GRANT SELECT ON TESTUSER.DM_CUSTOMER TO DM_MCP_READ;

-- 3) 表 / 视图多的时候，先生成语句、核对无误后再执行
SELECT 'GRANT SELECT ON ' || OWNER || '.' || TABLE_NAME || ' TO DM_MCP_READ;'
  FROM ALL_TABLES WHERE OWNER = 'TESTUSER' ORDER BY TABLE_NAME;

SELECT 'GRANT SELECT ON ' || OWNER || '.' || VIEW_NAME || ' TO DM_MCP_READ;'
  FROM ALL_VIEWS WHERE OWNER = 'TESTUSER' ORDER BY VIEW_NAME;
```

**不推荐的写法（实测代价）**：`GRANT SELECT ANY TABLE` 虽然达梦支持，
但会把 `V$SESSIONS` 这类动态性能视图一并放开，数据库侧边界形同虚设 ——
应用层仍会拦截这些查询，但等于少了一层防护。

### 3.3 授权后自检

```sql
-- 应通过
SELECT COUNT(*) FROM TESTUSER.DM_CUSTOMER;
-- 应报 -5504
SELECT COUNT(*) FROM <未授权的表>;
```

### 3.4 三层只读防护

| 层 | 机制 | 实测表现 |
|---|---|---|
| 数据库授权 | 只授 `SELECT` | 建表报 `-5515 没有创建表权限` |
| 连接层 | `access_mode=DSQL_MODE_READ_ONLY` | 写操作被拒 |
| 事务层 | `SET TRANSACTION READ ONLY` | `[CODE:-6506] Readonly transaction` |

DBA 只需负责第一层；后两层由服务自身强制，任何配置下都不放开。

---

## 4. 标准部署流程

严格按 `docs/LINUX_NATIVE_DEPLOYMENT.md` 执行，共 7 步，每步通过判据如下：

| 步 | 动作 | 通过判据 |
|---|---|---|
| 1 | 构建离线包（构建机） | 产出 `dameng-mcp-linux-native-centos7-x86_64.tar.gz` + `.sha256` |
| 2 | 传输到目标机 | 包、`.sha256`、env 三个文件到位 |
| 3 | 校验完整性 | `sha256sum -c` 输出 `OK` |
| 4 | 准备配置 | 从 `.env.linux-native.example` 复制并逐项填好；`DM_HOST` 与 `DM_PASSWORD` 非空、密码非 `replace-me` |
| 5 | 安装 | `bash scripts/install-linux-native.sh <env 文件>` 全绿；脚本自动建账号、装运行时、跑预检、起服务并探活 30 秒 |
| 6 | 验收 | 见 §5，必须全绿 |
| 7 | 平台注册 + 收窄防火墙 | 平台侧能列出 7 个工具，防火墙仅放行平台出口 IP |

**配置项校验规则**（服务启动时强制，不满足直接起不来）：

- `DM_ALLOWED_OWNERS` 不得为空，且不得与 `DM_DENIED_SCHEMAS` 有交集
- `DM_POOL_MAX >= DM_POOL_MIN`
- `MCP_PATH` / `MCP_SSE_PATH` / `MCP_MESSAGE_PATH` 三者必须互不相同
- `DM_ENCODING` 仅可取 `UTF8` / `GBK` / `GB18030`

---

## 5. 验收标准（Definition of Done）

以下四组全部满足，才算部署合格。

### a) 服务侧

```bash
axis-dameng-mcp status     # systemd 显示 active
axis-dameng-mcp check      # 预检：ready=true、版本前缀 V8、写操作被 -6506 拒绝
axis-dameng-mcp probe      # 协议握手 + 7 个 dm_* 工具 + 一次真实查询
```

### b) 功能链路（2026-09-23 实测，最小权限账号下 6/6 通过）

| # | 调用 | 实测结果 |
|---|---|---|
| 1 | `dm_search_objects(pattern="DM_%", owner="TESTUSER")` | rowCount=3 |
| 2 | `dm_list_tables(owner="TESTUSER")` | rowCount=3 |
| 3 | `dm_describe_table(owner="TESTUSER", table_name="DM_CUSTOMER")` | rowCount=5 |
| 4 | `dm_get_comments(owner="TESTUSER", table_name="DM_CUSTOMER")` | rowCount=5 |
| 5 | `dm_get_relationships(owner="TESTUSER", table_names=["DM_ORDER","DM_ORDER_ITEM"])` | rowCount=2 |
| 6 | `dm_execute_query` 中文聚合查询 | rowCount=2，中文无乱码 |

### c) 拒绝类用例（必须全部被拒）

| 输入 | 拒绝方式 |
|---|---|
| `DROP TABLE ...` | 只允许单条 SELECT 或 WITH |
| `INSERT ...` | 只允许单条 SELECT 或 WITH |
| `SELECT * FROM V$SESSIONS` | 系统与动态性能视图不允许 |
| `SELECT * FROM SYS.SYSOBJECTS` | 模式不允许 |
| `dm_list_tables(owner="SYSDBA")` | Owner 不在允许列表 |

### d) 平台侧

注册后能列出 7 个 `dm_*` 工具，并完成一次中文问答（如「TESTUSER 下有哪些表」）。

---

## 6. 暴露面与防火墙

MCP 端点**没有认证**，能访问它等于拿到该账号的数据库读权限。因此：

1. 无论内网还是云上，防火墙只放行**智能体平台出口 IP**，禁止 `0.0.0.0/0`：

   ```bash
   bash /opt/axis-dameng-mcp/app/scripts/configure-firewall-linux.sh <平台出口IP>/32 <MCP_PORT>
   ```

2. 云环境须在**安全组**同步收窄（服务器内 firewalld 与云安全组是两层，都要管；
   部分镜像根本不装 firewalld，此时只剩安全组这一层）。
3. 达梦端口（默认 5236）**不得对公网开放**。
4. 部署完成后回查一次实际暴露面：从外部扫描该 IP 的开放端口，确认只剩预期端口。

---

## 7. 变更、升级与回滚

- **升级**：重跑 `install-linux-native.sh` 即为就地升级（先停服务、换代码、
  重跑预检、重启并探活）。升级后必须重跑 §5 验收。
- **改配置**：编辑 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`（0640 root:dameng-mcp）
  后 `systemctl restart axis-dameng-mcp`。
- **回滚**：保留上一版 `tar.gz` 与旧 env 文件，重跑旧包即可；离线包不依赖网络，
  回滚不受网络影响。
- **卸载**：删 `/opt/axis-dameng-mcp`、`/etc/axis-dameng-mcp`、
  `/etc/systemd/system/axis-dameng-mcp.service`、`/usr/local/sbin/axis-dameng-mcp`，
  再 `userdel dameng-mcp`。
- **开发机实例无自愈**：Windows 版重启后不会自动拉起，需手动执行
  `scripts\Start-DamengMcp.ps1`。

---

## 8. 部署记录（每次部署必须填写）

| 日期 | 环境 | 主机:端口 | 达梦地址 | 接入账号 | 暴露模式 | 包 SHA256（前 12 位） | 验收结论 | 执行人 |
|---|---|---|---|---|---|---|---|---|
| 2026-09-23 | 测试 | 82.156.246.154:8084 | 127.0.0.1:5236 | SYSDBA（遗留） | TESTUSER | `22b4ba680b30` | 全绿 | Eclipsa2spads |
| 2026-09-23 | 开发 | 10.242.2.115:8082 | 82.156.246.154:5236 | SYSDBA（遗留） | TESTUSER | — | 全绿，平台已接入 | Eclipsa2spads |
| 待定 | 生产 | | | | | | | |

标注「遗留」表示该环境仍使用 `SYSDBA`，与 §3 不符。测试与开发环境属临时验证用途，
有意保留现状不做整改；生产部署时由运维提供正式的只读账号与内网库地址，
不沿用这两份配置。

---

## 9. 禁止事项

1. 用 `SYSDBA` / `SYS` / `SYSSSO` / `SYSAUDITOR` 等系统账号接入 MCP
2. `DM_ALLOWED_OWNERS` 填系统模式，或与 `DM_DENIED_SCHEMAS` 交叠
3. 把 MCP 端点对 `0.0.0.0/0` 开放
4. 把生产库凭据写入测试 / 开发配置，或提交进仓库
   （`.env`、`.env.linux` 等均已在 `.gitignore` 内）
5. 把公网穿透、反向隧道、开发机中转当成生产方案
6. 跳过 §5 验收，直接把服务交给平台使用
