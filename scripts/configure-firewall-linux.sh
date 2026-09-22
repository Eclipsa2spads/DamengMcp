#!/usr/bin/env bash
set -Eeuo pipefail

source_cidr="${1:-}"
port="${2:-8082}"

[[ "$(id -u)" -eq 0 ]] || { echo "Run this script as root." >&2; exit 1; }
[[ -n "${source_cidr}" ]] || {
    echo "Usage: bash scripts/configure-firewall-linux.sh <platform-ip-or-cidr> [port]" >&2
    exit 1
}
command -v firewall-cmd >/dev/null || { echo "firewall-cmd is required." >&2; exit 1; }
[[ "${port}" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || {
    echo "Invalid TCP port." >&2
    exit 1
}

rule="rule family=ipv4 source address=${source_cidr} port port=${port} protocol=tcp accept"
firewall-cmd --permanent --add-rich-rule="${rule}"
firewall-cmd --reload
firewall-cmd --list-rich-rules
