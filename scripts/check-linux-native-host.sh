#!/usr/bin/env bash
set -u

bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failed=0

ok() { printf '[OK] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; failed=1; }
info() { printf '[INFO] %s\n' "$1"; }

bundle_arch="$(ls "${bundle_root}"/vendor/Miniconda3-*-Linux-*.sh 2>/dev/null | head -n 1 | sed -n 's/.*-Linux-\(.*\)\.sh$/\1/p')"
host_arch="$(uname -m)"
if [[ -z "${bundle_arch}" ]]; then
    info "cannot determine the architecture from vendor/ (bundle integrity check will cover it)"
elif [[ "${host_arch}" == "${bundle_arch}" ]]; then
    ok "${bundle_arch} architecture"
else
    fail "this bundle targets ${bundle_arch}, but the host is ${host_arch}"
fi

glibc_version="$(ldd --version 2>/dev/null | head -n 1 | grep -oE '[0-9]+\.[0-9]+' | tail -n 1 || true)"
if [[ -n "${glibc_version}" ]] && awk -v value="${glibc_version}" 'BEGIN { split(value,v,"."); exit !((v[1] > 2) || (v[1] == 2 && v[2] >= 17)) }'; then
    ok "glibc ${glibc_version} (minimum 2.17)"
else
    fail "glibc 2.17 or newer is required"
fi

command -v systemctl >/dev/null 2>&1 && ok "systemd command" || fail "systemd is required"
command -v sha256sum >/dev/null 2>&1 && ok "sha256sum command" || fail "sha256sum is required"
command -v openssl >/dev/null 2>&1 && info "openssl $(openssl version 2>/dev/null | awk '{print $2}')" || info "openssl is not installed (the dmPython wheel bundles its own DM SSL libraries)"

if [[ -f "${bundle_root}/SHA256SUMS" ]]; then
    if (cd "${bundle_root}" && sha256sum --check --quiet SHA256SUMS); then
        ok "offline bundle file checksums"
    else
        fail "offline bundle checksum validation"
    fi
else
    fail "SHA256SUMS is missing"
fi

available_kb="$(df -Pk /opt | awk 'NR==2 {print $4}')"
if [[ "${available_kb:-0}" -ge 1048576 ]]; then ok "at least 1 GiB free under /opt"; else fail "at least 1 GiB free under /opt is required"; fi

port="${MCP_PORT:-8082}"
[[ "${port}" =~ ^[0-9]+$ ]] || port=8082

if ss -lnt 2>/dev/null | grep -qE "[:.]${port}[[:space:]]"; then
    if systemctl is-active --quiet axis-dameng-mcp.service 2>/dev/null; then
        info "TCP ${port} is used by the existing axis-dameng-mcp service and will be restarted"
    else
        fail "TCP ${port} is already in use by another process (set MCP_PORT in the env file to a free port)"
    fi
else
    ok "TCP port ${port} is free"
fi

if systemctl is-active --quiet axis-dameng-mcp.service 2>/dev/null; then
    info "an existing axis-dameng-mcp service will be updated in place"
fi

if [[ -d /opt/axis-dameng-mcp/python/lib/python3.11/site-packages/dmssl ]]; then
    ok "bundled DM SSL libraries are present"
fi

if command -v getenforce >/dev/null 2>&1; then info "SELinux mode: $(getenforce)"; fi
exit "${failed}"
