#!/usr/bin/env bash
# Limit both ingress and egress on the interface used to reach a peer.
# Usage: bash limit_client_proxy_iface.sh <peer-ip> <rate-kbps> <role>
set -euo pipefail

PEER_IP="${1:?Usage: $0 <peer-ip> <rate-kbps> <role>}"
RATE_KBPS="${2:?Usage: $0 <peer-ip> <rate-kbps> <role>}"
ROLE="${3:?Usage: $0 <peer-ip> <rate-kbps> <role>}"

if ! [[ "$RATE_KBPS" =~ ^[0-9]+$ ]] || [ "$RATE_KBPS" -le 0 ]; then
  echo "Error: rate must be a positive integer in Kbps: $RATE_KBPS" >&2
  exit 2
fi

IFACE="$(ip route get "$PEER_IP" | awk 'NR == 1 {for (i = 1; i <= NF; ++i) if ($i == "dev") {print $(i + 1); exit}}')"
if [ -z "$IFACE" ] || ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "Error: unable to find interface used to reach $PEER_IP" >&2
  exit 3
fi

WS_BIN="$(command -v wondershaper || true)"
if [ -z "$WS_BIN" ]; then
  for candidate in /usr/sbin/wondershaper /sbin/wondershaper /usr/bin/wondershaper; do
    if [ -x "$candidate" ]; then
      WS_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$WS_BIN" ]; then
  echo "Error: wondershaper not found" >&2
  exit 127
fi

# wondershaper uses a shared ifb0 device for ingress shaping. Its clear mode
# removes both the interface qdiscs and stale ifb0 state from prior runs.
"$WS_BIN" -c -a "$IFACE" >/dev/null 2>&1 || true
if ! APPLY_OUTPUT="$("$WS_BIN" -a "$IFACE" -d "$RATE_KBPS" -u "$RATE_KBPS" 2>&1)"; then
  echo "Error: wondershaper failed on $IFACE: $APPLY_OUTPUT" >&2
  exit 4
fi

QDISC_STATE="$(tc qdisc show dev "$IFACE" 2>/dev/null)"
EGRESS_RATE="$(tc class show dev "$IFACE" 2>/dev/null | awk '$1 == "class" && $3 == "1:1" {for (i = 1; i <= NF; ++i) if ($i == "rate") {print $(i + 1); exit}}')"
INGRESS_RATE="$(tc class show dev ifb0 2>/dev/null | awk '$1 == "class" && $3 == "2:1" {for (i = 1; i <= NF; ++i) if ($i == "rate") {print $(i + 1); exit}}')"
EXPECTED_MBIT=$((RATE_KBPS / 1000))

if [[ "$QDISC_STATE" != *"htb "* ]] || [[ "$QDISC_STATE" != *"ingress "* ]] ||
   [ -z "$EGRESS_RATE" ] || [ -z "$INGRESS_RATE" ] ||
   [[ "$EGRESS_RATE" != "${EXPECTED_MBIT}Mbit" ]] ||
   [[ "$INGRESS_RATE" != "${EXPECTED_MBIT}Mbit" ]]; then
  echo "Error: failed to verify $RATE_KBPS Kbps bidirectional shaping on $IFACE (egress=$EGRESS_RATE ingress=$INGRESS_RATE)" >&2
  exit 5
fi

echo "OK role=$ROLE peer=$PEER_IP iface=$IFACE ingress=$INGRESS_RATE egress=$EGRESS_RATE"
