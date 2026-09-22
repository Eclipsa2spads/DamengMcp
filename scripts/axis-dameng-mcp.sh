#!/usr/bin/env bash
set -Eeuo pipefail

service_name=axis-dameng-mcp.service
python_bin=/opt/axis-dameng-mcp/python/bin/python
app_root=/opt/axis-dameng-mcp/app
env_file=/etc/axis-dameng-mcp/axis-dameng-mcp.env
dmssl_dir=/opt/axis-dameng-mcp/python/lib/python3.11/site-packages/dmssl

usage() {
    echo "Usage: axis-dameng-mcp {start|stop|restart|status|logs|check|probe}"
}

case "${1:-}" in
    start|stop|restart)
        systemctl "$1" "${service_name}"
        systemctl --no-pager --full status "${service_name}"
        ;;
    status)
        systemctl --no-pager --full status "${service_name}"
        ;;
    logs)
        journalctl -u "${service_name}" -n 100 --no-pager
        ;;
    check)
        LD_LIBRARY_PATH="${dmssl_dir}" "${python_bin}" "${app_root}/scripts/check_dameng.py" --env-file "${env_file}"
        ;;
    probe)
        "${app_root}/scripts/probe-linux-native.sh"
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
