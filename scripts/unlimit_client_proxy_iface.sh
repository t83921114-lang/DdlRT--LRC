#!/usr/bin/env bash
# Remove shaping from the interface used to reach a peer.
# Usage: bash unlimit_client_proxy_iface.sh <peer-ip> <role>
set -euo pipefail

PEER_IP="${1:?Usage: $0 <peer-ip> <role>}"
ROLE="${2:?Usage: $0 <peer-ip> <role>}"
IFACE="$(ip route get "$PEER_IP" | awk 'NR == 1 {for (i = 1; i <= NF; ++i) if ($i == "dev") {print $(i + 1); exit}}')"
if [ -z "$IFACE" ] || ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "Error: unable to find interface used to reach $PEER_IP" >&2
  exit 3
fi

WS_BIN="$(command -v wondershaper || true)"
if [ -n "$WS_BIN" ]; then
  "$WS_BIN" -c -a "$IFACE" >/dev/null 2>&1 || true
else
  tc qdisc del dev "$IFACE" root >/dev/null 2>&1 || true
  tc qdisc del dev "$IFACE" ingress >/dev/null 2>&1 || true
  tc qdisc del dev ifb0 root >/dev/null 2>&1 || true
  tc qdisc del dev ifb0 ingress >/dev/null 2>&1 || true
fi
QDISC_STATE="$(tc qdisc show dev "$IFACE" 2>/dev/null)"
IFB_STATE="$(tc qdisc show dev ifb0 2>/dev/null || true)"
if [[ "$QDISC_STATE" == *"htb "* ]] || [[ "$QDISC_STATE" == *"ingress "* ]] ||
   [[ "$IFB_STATE" == *"htb "* ]] || [[ "$IFB_STATE" == *"ingress "* ]]; then
  echo "Error: failed to clear shaping on $IFACE/ifb0" >&2
  exit 4
fi

echo "OK role=$ROLE peer=$PEER_IP iface=$IFACE cleared"
