#!/usr/bin/env bash
# Limit proxy-side client links to 1 Gb/s and the client side to 10 Gb/s.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PROXY_HOSTS="$ROOT/proxy_hosts"
REMOTE_DIR="${REMOTE_DIR:-DdlRT}"
CLIENT_IP="${CLIENT_IP:-10.10.1.1}"
PROXY_PROBE_IP="${PROXY_PROBE_IP:-10.10.1.3}"
PROXY_RATE_KBPS="${PROXY_RATE_KBPS:-1048576}"
CLIENT_RATE_KBPS="${CLIENT_RATE_KBPS:-10485760}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
else
  SUDO=(sudo)
fi

echo "Applying client-side 10 Gb/s limit..."
"${SUDO[@]}" bash "$ROOT/scripts/limit_client_proxy_iface.sh" \
  "$PROXY_PROBE_IP" "$CLIENT_RATE_KBPS" client

echo "Applying proxy-side 1 Gb/s limits..."
"${SUDO[@]}" pdsh -S -R ssh -w ^"$PROXY_HOSTS" -l root -f 17 \
  "cd '$REMOTE_DIR' && bash scripts/limit_client_proxy_iface.sh '$CLIENT_IP' '$PROXY_RATE_KBPS' proxy"

echo "Client/proxy bandwidth limits applied."
