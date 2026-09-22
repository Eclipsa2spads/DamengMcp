#!/usr/bin/env bash
set -Eeuo pipefail

python_bin=/opt/axis-dameng-mcp/python/bin/python
app_root=/opt/axis-dameng-mcp/app
env_file="${MCP_ENV_FILE:-/etc/axis-dameng-mcp/axis-dameng-mcp.env}"

port=8082
if [[ -f "${env_file}" ]]; then
    configured="$(grep -E '^MCP_PORT=' "${env_file}" | tail -n 1 | cut -d= -f2 | tr -d '[:space:]')"
    [[ "${configured}" =~ ^[0-9]+$ ]] && port="${configured}"
fi

streamable_url="${1:-http://127.0.0.1:${port}/mcp}"
sse_url="${2:-http://127.0.0.1:${port}/sse}"

[[ -x "${python_bin}" ]] || { echo "Native runtime is not installed." >&2; exit 1; }
"${python_bin}" "${app_root}/scripts/probe_mcp.py" --url "${streamable_url}" --call
"${python_bin}" "${app_root}/scripts/probe_sse.py" --url "${sse_url}" --call
