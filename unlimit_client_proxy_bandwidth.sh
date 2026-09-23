#!/usr/bin/env bash
# Remove client/proxy link shaping configured by limit_client_proxy_bandwidth.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PROXY_HOSTS="$ROOT/proxy_hosts"
REMOTE_DIR="${REMOTE_DIR:-DdlRT}"
CLIENT_IP="${CLIENT_IP:-10.10.1.1}"
PROXY_PROBE_IP="${PROXY_PROBE_IP:-10.10.1.3}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

"${SUDO[@]}" bash "$ROOT/scripts/unlimit_client_proxy_iface.sh" "$PROXY_PROBE_IP" client
"${SUDO[@]}" pdsh -S -R ssh -w ^"$PROXY_HOSTS" -l root -f 17 \
  "cd '$REMOTE_DIR' && bash scripts/unlimit_client_proxy_iface.sh '$CLIENT_IP' proxy"
echo "Client/proxy bandwidth limits removed."
