#!/usr/bin/env bash
# 达梦 MCP 一键安装向导。
# 交互式收集 6 项必填配置 → 逐项校验 → 生成 .env.linux → 调用 install-linux-native.sh。
# 也可以用参数或环境变量提供答案，加 --yes 跳过确认，用于无人值守安装。
set -Eeuo pipefail

bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
installer="${bundle_root}/scripts/install-linux-native.sh"
env_target="${bundle_root}/.env.linux"
denied_schemas="SYS,SYSSSO,SYSAUDITOR,SYSJOB,SYSDBA,SYSCONFIG"
system_accounts=(SYSDBA SYS SYSSSO SYSAUDITOR SYSJOB SYSCONFIG)

if [[ -t 1 ]]; then
    warn_color=$'\033[1;31m'
    color_reset=$'\033[0m'
else
    warn_color=""
    color_reset=""
fi

step() { echo; echo "==> $*"; }
note() { echo "[信息] $*"; }
ok() { echo "[通过] $*"; }
die() { echo "[失败] $*" >&2; exit 1; }
warn() { printf '%s[警告] %s%s\n' "${warn_color}" "$*" "${color_reset}" >&2; }

usage() {
    cat <<'EOF'
用法：bash scripts/quick-install.sh [选项]

不加选项时逐项提示输入；把答案放进参数或环境变量即可无人值守安装。

选项：
  --host <地址>        达梦地址                        （环境变量 DM_HOST）
  --port <端口>        达梦端口，默认 5236              （环境变量 DM_PORT）
  --user <账号>        达梦只读账号                     （环境变量 DM_USER）
  --owners <模式列表>  允许访问的业务模式，逗号分隔       （环境变量 DM_ALLOWED_OWNERS）
  --mcp-port <端口>    服务端口，默认 8082              （环境变量 MCP_PORT）
  --password-stdin     从标准输入读一行作为达梦密码
  --yes, -y            跳过最后的确认，直接安装
  --help, -h           显示本帮助

密码不能用命令行参数传入（会进 shell 历史与进程列表），只能走 DM_PASSWORD
环境变量、--password-stdin 或交互输入。

示例：
  bash scripts/quick-install.sh
  DM_PASSWORD='xxx' bash scripts/quick-install.sh --yes \
      --host 10.1.2.3 --user DM_MCP_READ --owners APP_SCHEMA --mcp-port 8084
EOF
}

arg_host=""; arg_port=""; arg_user=""; arg_owners=""; arg_mcp_port=""
assume_yes=0
password_stdin=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) [[ $# -ge 2 ]] || die "--host 缺少取值"; arg_host="$2"; shift 2 ;;
        --port) [[ $# -ge 2 ]] || die "--port 缺少取值"; arg_port="$2"; shift 2 ;;
        --user) [[ $# -ge 2 ]] || die "--user 缺少取值"; arg_user="$2"; shift 2 ;;
        --owners) [[ $# -ge 2 ]] || die "--owners 缺少取值"; arg_owners="$2"; shift 2 ;;
        --mcp-port) [[ $# -ge 2 ]] || die "--mcp-port 缺少取值"; arg_mcp_port="$2"; shift 2 ;;
        --password-stdin) password_stdin=1; shift ;;
        --yes|-y) assume_yes=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) die "未知参数：$1（用 --help 查看用法）" ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || die "安装需要 root 权限，请用：sudo bash scripts/quick-install.sh"
[[ -f "${installer}" ]] || die "找不到 ${installer}，请在解压后的离线包目录内运行本脚本"

# ---------- 取值：参数 > 环境变量 > 交互提示 ----------
# 注意：取值的函数把结果写进全局变量 value，不用命令替换返回。
# 因为在命令替换出的子进程里调用 die 只会结束子进程，主进程会拿到空值继续跑，
# 非交互场景下会变成死循环。

is_interactive() { [[ -t 0 && -t 1 ]]; }

prompt_text() {   # $1=提示语 $2=默认值；结果写入 value，读不到返回 1
    local label="$1" fallback="${2:-}" answer
    if [[ -n "${fallback}" ]]; then
        read -r -p "${label} [${fallback}]: " answer || return 1
    else
        read -r -p "${label}: " answer || return 1
    fi
    value="${answer:-${fallback}}"
}

# read_value <提示语> <已有取值> <默认值> <校验函数> <要求说明>
read_value() {
    local label="$1" provided="$2" fallback="$3" validator="$4" hint="$5"
    if [[ -n "${provided}" ]]; then
        "${validator}" "${provided}" || die "${label}「${provided}」不合法：${reason:-${hint}}"
        value="${provided}"
        return 0
    fi
    while :; do
        prompt_text "${label}" "${fallback}" || die "读取输入失败"
        if "${validator}" "${value}"; then
            return 0
        fi
        echo "  [!] ${reason:-${hint}}，请重新输入" >&2
    done
}

read_secret() {   # $1=提示语 $2=已有取值；结果写入 value
    local label="$1" provided="$2" first second
    if [[ -n "${provided}" ]]; then
        value="${provided}"
        return 0
    fi
    read -r -s -p "${label}: " first || die "读取输入失败"
    echo >&2
    read -r -s -p "${label}（再输一次确认）: " second || die "读取输入失败"
    echo >&2
    [[ -n "${first}" ]] || die "密码不能为空"
    [[ "${first}" == "${second}" ]] || die "两次输入的密码不一致"
    value="${first}"
}

is_port() { [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )); }
is_host() { [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ ]]; }
is_identifier() { [[ "$1" =~ ^[A-Za-z][A-Za-z0-9_\$#]{0,127}$ ]]; }

# 多条件校验把命中的那条原因写进 reason，报错时才不会一口气念一串要求
reason=""

is_account() {
    reason=""
    if [[ -z "$1" ]]; then
        reason="账号不能为空"
        return 1
    fi
    if ! is_identifier "$1"; then
        reason="账号名要以字母开头，只能含字母、数字、下划线、\$ 和 #"
        return 1
    fi
    return 0
}

is_system_account() {
    local candidate="${1^^}" item
    for item in "${system_accounts[@]}"; do
        [[ "${candidate}" == "${item}" ]] && return 0
    done
    return 1
}

is_owners() {
    local item
    reason=""
    if [[ -z "$1" ]]; then
        reason="业务模式不能为空"
        return 1
    fi
    local IFS=','
    for item in $1; do
        item="${item// /}"
        if [[ -z "${item}" ]]; then
            reason="逗号之间有空项"
            return 1
        fi
        if ! is_identifier "${item}"; then
            reason="「${item}」不是合法的模式名"
            return 1
        fi
        if [[ ",${denied_schemas}," == *",${item^^},"* ]]; then
            reason="「${item}」是系统模式，不能出现在这里"
            return 1
        fi
    done
    return 0
}

local_port_in_use() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -lnt 2>/dev/null | awk 'NR > 1 { print $4 }' | grep -qE "[:.]${port}\$"
    else
        timeout 1 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${port}" 2>/dev/null
    fi
}

is_free_service_port() { is_port "$1" && ! local_port_in_use "$1"; }

tcp_reachable() { timeout 5 bash -c "cat < /dev/null > /dev/tcp/$1/$2" 2>/dev/null; }

# ---------- 逐项收集 ----------

# 非交互时先把缺失项一次报全，而不是卡在等输入
if ! is_interactive; then
    missing=""
    [[ -n "${arg_host:-${DM_HOST:-}}" ]] || missing="${missing} --host"
    [[ -n "${arg_port:-${DM_PORT:-}}" ]] || missing="${missing} --port"
    [[ -n "${arg_user:-${DM_USER:-}}" ]] || missing="${missing} --user"
    if [[ -z "${DM_PASSWORD:-}" ]] && (( ! password_stdin )); then
        missing="${missing} --password-stdin"
    fi
    [[ -n "${arg_owners:-${DM_ALLOWED_OWNERS:-}}" ]] || missing="${missing} --owners"
    [[ -n "${arg_mcp_port:-${MCP_PORT:-}}" ]] || missing="${missing} --mcp-port"
    if [[ -n "${missing}" ]]; then
        die "当前不是交互终端，以下配置必须用参数或环境变量提供：${missing}（--help 查看用法）"
    fi
fi

step "配置达梦连接"

read_value "达梦地址（内网 IP 或主机名）" "${arg_host:-${DM_HOST:-}}" "127.0.0.1" is_host "只允许字母、数字、点、下划线和短横线"
dm_host="${value}"

read_value "达梦端口" "${arg_port:-${DM_PORT:-}}" "5236" is_port "应为 1-65535 的端口号"
dm_port="${value}"

read_value "达梦只读账号" "${arg_user:-${DM_USER:-}}" "" is_account "账号名不合法"
dm_user="${value^^}"

system_account_used=0
if is_system_account "${dm_user}"; then
    system_account_used=1
    warn "「${dm_user}」是达梦系统账号。用它接入等于把整个实例的权限交给服务进程："
    warn "服务端会强制只读，但账号本身权限过高，一旦服务出问题，敞口是整库而不是几张表。"
    warn "测试期可以继续；正式交付前请换成只读业务账号（只授业务表/视图的 SELECT）。"
fi

dm_password="${DM_PASSWORD:-}"
if (( password_stdin )); then
    IFS= read -r dm_password || die "--password-stdin 没有读到内容"
fi
read_secret "达梦密码（不回显）" "${dm_password}"
dm_password="${value}"

if (( system_account_used )); then
    owners_default=""          # 系统账号名不能当业务模式用
else
    owners_default="${dm_user}"
fi
read_value "允许访问的业务模式（逗号分隔）" "${arg_owners:-${DM_ALLOWED_OWNERS:-}}" "${owners_default}" is_owners "只能用业务模式，且不能与系统模式 ${denied_schemas} 重合"
dm_owners="$(printf '%s' "${value}" | tr 'a-z' 'A-Z' | tr -d ' ')"

read_value "MCP 服务端口" "${arg_mcp_port:-${MCP_PORT:-}}" "8082" is_free_service_port "应为 1-65535 且本机未被占用（ss -lntp 可查看占用）"
mcp_port="${value}"

step "检查可达性"

if tcp_reachable "${dm_host}" "${dm_port}"; then
    ok "达梦 ${dm_host}:${dm_port} 可建立 TCP 连接"
else
    die "连不上达梦 ${dm_host}:${dm_port}。请确认地址与端口是否正确、达梦服务是否运行、网络是否放行。"
fi
note "登录校验（账号密码是否正确）由安装脚本的预检完成，此处只确认网络可达。"

# ---------- 生成配置文件 ----------

quote_value() {
    local value="$1" escaped
    if [[ "${value}" != *"'"* ]]; then
        printf "'%s'" "${value}"
    else
        escaped="${value//\\/\\\\}"
        escaped="${escaped//\"/\\\"}"
        printf '"%s"' "${escaped}"
    fi
}

step "生成配置文件"

if [[ -e "${env_target}" ]]; then
    backup="${env_target}.bak.$(date '+%Y%m%d%H%M%S')"
    cp -p "${env_target}" "${backup}"
    note "已备份原配置到 ${backup}"
fi

password_literal="$(quote_value "${dm_password}")"
umask 077
cat > "${env_target}" <<EOF
# 由 scripts/quick-install.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')
DM_HOST=${dm_host}
DM_PORT=${dm_port}
DM_USER=${dm_user}
DM_PASSWORD=${password_literal}
DM_ENCODING=UTF8

DM_ALLOWED_OWNERS=${dm_owners}
DM_DENIED_SCHEMAS=${denied_schemas}
DM_REQUIRED_VERSION_PREFIX=V8

DM_POOL_MIN=1
DM_POOL_MAX=3
DM_POOL_WAIT_TIMEOUT_MS=5000
DM_QUERY_TIMEOUT_SECONDS=30
DM_MAX_ROWS=200
DM_MAX_SQL_LENGTH=10000
DM_MAX_CELL_CHARS=10000
DM_MAX_RESULT_BYTES=1048576

MCP_HOST=0.0.0.0
MCP_PORT=${mcp_port}
MCP_PATH=/mcp
MCP_SSE_PATH=/sse
MCP_MESSAGE_PATH=/messages/
EOF
chmod 600 "${env_target}"
ok "已写入 ${env_target}（权限 600）"
note "连接池、超时、返回上限等参数已使用默认值，需要调整就改这个文件后重跑安装。"

# ---------- 确认并安装 ----------

cat <<EOF

即将使用以下配置安装：

  达梦地址      ${dm_host}
  达梦端口      ${dm_port}
  达梦账号      ${dm_user}
  达梦密码      ********
  业务模式      ${dm_owners}
  服务端口      ${mcp_port}
  配置文件      ${env_target}

EOF

if (( system_account_used )); then
    warn "当前用的达梦账号是系统账号 ${dm_user}，与只读原则不符；正式交付前请换成只读业务账号。"
fi

if (( ! assume_yes )); then
    is_interactive || die "非交互模式下请加 --yes 确认安装"
    read -r -p "确认开始安装？[y/N] " reply || die "读取输入失败"
    if [[ ! "${reply}" =~ ^[Yy]$ ]]; then
        note "已取消。配置文件已生成，之后可直接执行：bash scripts/install-linux-native.sh .env.linux"
        exit 0
    fi
fi

step "开始安装"
bash "${installer}" "${env_target}"
