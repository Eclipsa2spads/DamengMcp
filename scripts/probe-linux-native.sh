#!/usr/bin/env bash
set -Eeuo pipefail

python_bin=/opt/axis-dameng-mcp/python/bin/python
app_root=/opt/axis-dameng-mcp/app
streamable_url="${1:-http://127.0.0.1:8082/mcp}"
sse_url="${2:-http://127.0.0.1:8082/sse}"

[[ -x "${python_bin}" ]] || { echo "Native runtime is not installed." >&2; exit 1; }
"${python_bin}" "${app_root}/scripts/probe_mcp.py" --url "${streamable_url}" --call
"${python_bin}" "${app_root}/scripts/probe_sse.py" --url "${sse_url}" --call
