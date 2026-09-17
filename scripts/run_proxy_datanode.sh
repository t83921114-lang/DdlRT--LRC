#!/bin/bash
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export LD_LIBRARY_PATH="$BASE_DIR/project/third_party/jerasure/lib:$BASE_DIR/project/third_party/gf-complete/lib:${LD_LIBRARY_PATH:-}"

pkill -9 run_datanode 2>/dev/null || true
pkill -9 run_proxy 2>/dev/null || true
mkdir -p logs

# This file is regenerated on each host by scripts/generate_run_proxy.sh.
