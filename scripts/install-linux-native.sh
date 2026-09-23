#!/usr/bin/env bash
set -Eeuo pipefail
umask 022   # 固定权限掩码：安装出的运行时与配置必须让 dameng-mcp 用户可读可执行，
            # 不依赖调用方的 umask（曾因调用方 umask 077 导致服务 203/EXEC）

install_root=/opt/axis-dameng-mcp
app_root=${install_root}/app
python_root=${install_root}/python
dmssl_dir=${python_root}/lib/python3.11/site-packages/dmssl
config_root=/etc/axis-dameng-mcp
config_target=${config_root}/axis-dameng-mcp.env
service_target=/etc/systemd/system/axis-dameng-mcp.service
manager_target=/usr/local/sbin/axis-dameng-mcp
service_name=axis-dameng-mcp.service
bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ge 1 ]]; then
    config_source="$1"
elif [[ -f "${bundle_root}/.env.linux" ]]; then
    config_source=${bundle_root}/.env.linux
else
    config_source=${config_target}
fi

die() { echo "[FAIL] $*" >&2; exit 1; }
step() { echo "[STEP] $*"; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root: bash scripts/install-linux-native.sh [.env.linux]"
[[ -f "${config_source}" ]] || die "Missing configuration: ${config_source}. Copy .env.linux-native.example to .env.linux and edit it first."
grep -qE '^DM_HOST=.+$' "${config_source}" || die "DM_HOST is missing or empty."
grep -qE '^DM_PASSWORD=.+$' "${config_source}" || die "DM_PASSWORD is missing or empty."
grep -qE '^DM_PASSWORD=replace-me[[:space:]]*$' "${config_source}" && die "Replace the example Dameng password before installation."

mcp_port="$(grep -E '^MCP_PORT=' "${config_source}" | tail -n 1 | cut -d= -f2 | tr -d '[:space:]')"
mcp_port="${mcp_port:-8082}"
[[ "${mcp_port}" =~ ^[0-9]+$ ]] && (( mcp_port >= 1 && mcp_port <= 65535 )) || die "MCP_PORT must be a valid TCP port."

for required in \
    vendor/Miniconda3-py311_24.3.0-0-Linux-x86_64.sh \
    app/server.py app/requirements.txt \
    deploy/systemd/axis-dameng-mcp.service SHA256SUMS; do
    [[ -f "${bundle_root}/${required}" ]] || die "Offline bundle is missing ${required}."
done

step "Checking CentOS 7 host and bundle integrity"
MCP_PORT="${mcp_port}" bash "${bundle_root}/scripts/check-linux-native-host.sh"

if systemctl is-active --quiet "${service_name}" 2>/dev/null; then
    step "Stopping the existing service for an in-place update"
    systemctl stop "${service_name}"
fi

step "Creating the dedicated service account and directories"
getent group dameng-mcp >/dev/null 2>&1 || groupadd --system dameng-mcp
id -u dameng-mcp >/dev/null 2>&1 || useradd --system --gid dameng-mcp --home-dir "${install_root}" --shell /sbin/nologin dameng-mcp
install -d -m 0755 -o root -g dameng-mcp "${install_root}" "${app_root}"
install -d -m 0750 -o root -g dameng-mcp "${config_root}"

miniconda_installer=${bundle_root}/vendor/Miniconda3-py311_24.3.0-0-Linux-x86_64.sh
if [[ ! -x "${python_root}/bin/python" ]]; then
    [[ ! -e "${python_root}" ]] || die "${python_root} exists but does not contain a usable Python runtime."
    step "Installing the bundled Python 3.11 runtime"
    bash "${miniconda_installer}" -b -p "${python_root}"
fi
python_version="$(${python_root}/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "${python_version}" == "3.11" ]] || die "Bundled runtime must be Python 3.11, found ${python_version}."

step "Installing Python dependencies from the offline wheelhouse"
"${python_root}/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-index \
    --upgrade \
    --find-links "${bundle_root}/wheelhouse" \
    --requirement "${bundle_root}/app/requirements.txt"

step "Verifying the bundled dmPython driver"
"${python_root}/bin/python" - <<'PY'
import dmPython

print(f"[OK] dmPython {dmPython.version} imported from a bundled DPI client")
PY
[[ -d "${dmssl_dir}" ]] || die "The dmPython wheel did not provide ${dmssl_dir}."

step "Installing the MCP application without secrets"
install -m 0644 "${bundle_root}/app/server.py" "${app_root}/server.py"
install -m 0644 "${bundle_root}/app/requirements.txt" "${app_root}/requirements.txt"
install -d -m 0755 "${app_root}/core" "${app_root}/tools" "${app_root}/scripts"
cp -a "${bundle_root}/app/core/." "${app_root}/core/"
cp -a "${bundle_root}/app/tools/." "${app_root}/tools/"
cp -a "${bundle_root}/app/scripts/." "${app_root}/scripts/"
find "${app_root}" -type d -exec chmod 0755 {} +
find "${app_root}" -type f -exec chmod 0644 {} +
chmod 0755 "${app_root}/scripts/probe-linux-native.sh"
chown -R root:dameng-mcp "${app_root}"

step "Installing the protected configuration and systemd unit"
if [[ "$(readlink -f "${config_source}")" != "$(readlink -f "${config_target}" 2>/dev/null || echo "${config_target}")" ]]; then
    install -m 0640 -o root -g dameng-mcp "${config_source}" "${config_target}"
else
    chown root:dameng-mcp "${config_target}"
    chmod 0640 "${config_target}"
fi
install -m 0644 -o root -g root "${bundle_root}/deploy/systemd/axis-dameng-mcp.service" "${service_target}"
install -m 0755 -o root -g root "${bundle_root}/scripts/axis-dameng-mcp.sh" "${manager_target}"
systemctl daemon-reload

step "Running the Dameng preflight before service startup"
LD_LIBRARY_PATH="${dmssl_dir}" "${python_root}/bin/python" \
    "${app_root}/scripts/check_dameng.py" --env-file "${config_target}"

step "Enabling and starting axis-dameng-mcp"
systemctl enable "${service_name}"
if ! systemctl restart "${service_name}"; then
    journalctl -u "${service_name}" -n 100 --no-pager >&2 || true
    die "The service failed to start."
fi

deadline=$((SECONDS + 30))
until "${python_root}/bin/python" "${app_root}/scripts/probe_mcp.py" \
    --url "http://127.0.0.1:${mcp_port}/mcp" --call >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
        journalctl -u "${service_name}" -n 100 --no-pager >&2 || true
        die "The MCP endpoint did not become ready within 30 seconds."
    fi
    sleep 1
done

host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "[OK] Axis Dameng MCP native service is running."
echo "[OK] Streamable HTTP: http://${host_ip:-127.0.0.1}:${mcp_port}/mcp"
echo "[OK] HTTP+SSE:       http://${host_ip:-127.0.0.1}:${mcp_port}/sse"
echo "[INFO] Manage it with: axis-dameng-mcp {status|logs|check|probe|restart|stop}"
