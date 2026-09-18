#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LD_LIBRARY_PATH="$REPO_ROOT/project/third_party/jerasure/lib:$REPO_ROOT/project/third_party/gf-complete/lib:${LD_LIBRARY_PATH:-}"

pkill -9 run_coordinator 2>/dev/null || true

cd "$REPO_ROOT"
mkdir -p logs
nohup ./project/cmake/build/run_coordinator \
  </dev/null >>logs/coordinator.log 2>&1 &

