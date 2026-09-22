# 达梦 MCP 离线原生部署（CentOS 7 x86-64）

面向无 Python、无法联网、没有 Docker 的 CentOS 7 x86-64 服务器。离线包内置：

- Miniconda3 Python 3.11 运行时（`vendor/`）
- 全部 Linux 依赖 wheel（`wheelhouse/`，`manylinux2014_x86_64`，glibc ≥ 2.17）
- `dmPython 2.5.38`：wheel 自带达梦 DPI 客户端（`libdmdpi`）与 DM SSL 库，**无需安装达梦客户端软件**
- MCP 服务代码、systemd 单元、安装与运维脚本

服务以独立系统用户 `dameng-mcp` 运行，通过 systemd 托管，同时提供
Streamable HTTP（`/mcp`）和旧版 HTTP+SSE（`/sse`、`/messages/`）。

## 1. 构建离线包（在 Windows 构建机执行）

```powershell
# 1) 准备依赖闭包与 wheelhouse（首次构建需要联网）
.\scripts\Download-DmWheels.ps1 -WithMiniconda

# 2) 生成离线包
.\scripts\build-linux-native-bundle.ps1
```

产物位于 `dist/`：

```
dameng-mcp-linux-native-centos7-x86_64.tar.gz
dameng-mcp-linux-native-centos7-x86_64.tar.gz.sha256
```

离线包不包含 `.env.linux`，也不包含任何数据库密码。

## 2. 传输到目标机

```bash
scp dist/dameng-mcp-linux-native-centos7-x86_64.tar.gz* root@<target>:/root/
```

## 3. 校验与解压

```bash
cd /root
sha256sum -c dameng-mcp-linux-native-centos7-x86_64.tar.gz.sha256
tar -xzf dameng-mcp-linux-native-centos7-x86_64.tar.gz
cd dameng-mcp-linux-native
```

## 4. 准备配置

```bash
cp .env.linux-native.example .env.linux
vi .env.linux
chmod 600 .env.linux
```

必须正确填写：

| 变量 | 说明 |
| --- | --- |
| `DM_HOST` / `DM_PORT` | 达梦实例地址与端口（默认 5236） |
| `DM_USER` / `DM_PASSWORD` | 只读业务账号，**不要使用 SYSDBA** |
| `DM_ALLOWED_OWNERS` | 允许 MCP 访问的业务模式白名单，逗号分隔 |
| `DM_DENIED_SCHEMAS` | 禁止访问的系统模式，默认已包含 `SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG` |
| `DM_REQUIRED_VERSION_PREFIX` | 版本前缀校验，默认 `V8`；留空表示跳过校验 |

数据库账号的权限最小集合：业务模式（表/视图）`SELECT`，以及
`V$VERSION` 的 `SELECT`（用于启动自检）。**不要授予 DDL 或 DML 权限**，
服务端还会在连接上强制只读。

## 5. 安装

```bash
bash scripts/check-linux-native-host.sh          # 只检查，不改动系统
bash scripts/install-linux-native.sh .env.linux  # 需要 root
```

安装脚本会：创建 `dameng-mcp` 用户 → 安装内置 Python 3.11 → 离线安装 wheel →
安装服务代码到 `/opt/axis-dameng-mcp` → 配置 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`
（权限 0640，属主 root:dameng-mcp）→ 执行达梦连通性与只读自检 → 启动 systemd 服务
→ 30 秒内探测 `/mcp`。

成功输出形如：

```
[OK] Axis Dameng MCP native service is running.
[OK] Streamable HTTP: http://<host-ip>:8082/mcp
```

## 6. 日常运维

```bash
axis-dameng-mcp status      # systemd 状态
axis-dameng-mcp logs        # 最近 100 行日志
axis-dameng-mcp check       # 连通性 + 版本 + 只读自检，输出 JSON
axis-dameng-mcp probe       # 走 /mcp 与 /sse 各调用一次 dm_execute_query
axis-dameng-mcp restart
axis-dameng-mcp stop

journalctl -u axis-dameng-mcp -f      # 实时日志
```

## 7. 防火墙

平台侧只能访问服务端口，不要开放达梦 5236 入站。确认平台来源 IP 后：

```bash
bash scripts/configure-firewall-linux.sh 10.253.170.59/32 8082
```

先用 `journalctl -u axis-dameng-mcp | grep source_ip` 确认实际来源地址，再收紧规则。

## 8. 平台注册与验证

```bash
curl -sS -X POST http://<host-ip>:8082/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

平台侧注册地址：`http://<host-ip>:8082/mcp`（旧版 SSE 客户端用 `/sse`）。

## 9. 升级与回滚

升级：把新离线包解压后重复第 5 步即可，安装脚本会先停旧服务、原地替换代码与
wheel、保留 `/etc/axis-dameng-mcp/axis-dameng-mcp.env`。

回滚：保留上一版 `dist` 包与 `.env.linux`，重新执行上一版安装脚本。

## 10. 故障排查

| 现象 | 处理 |
| --- | --- |
| `dmPython` 导入失败 | `wheelhouse/` 中必须存在 `dmpython-*-manylinux2014_x86_64.whl`；确认目标机 glibc ≥ 2.17 |
| 达梦加密模块报错（如 `-70089`） | 单元文件已通过 `LD_LIBRARY_PATH` 指向 wheel 内置的 `dmssl` 目录；`axis-dameng-mcp check` 会打印真实错误 |
| 服务启动失败 | `journalctl -u axis-dameng-mcp -n 100 --no-pager`；常见原因是 `DM_PASSWORD` 未替换或 `DM_ALLOWED_OWNERS` 与 `DM_DENIED_SCHEMAS` 冲突 |
| `Unable to determine the Dameng version` | 给业务账号授予 `V$VERSION` 的 `SELECT`，或把 `DM_REQUIRED_VERSION_PREFIX` 置空 |
| 端口被占用 | `ss -lntp | grep 8082`；`install-linux-native.sh` 只能接管同名服务占用的端口 |
| 中文乱码 | 确认 `DM_ENCODING` 与数据库字符集匹配（UTF-8 库用 `UTF8`，GBK 库用 `GBK`） |
