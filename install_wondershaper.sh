#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOSTS_FILE="$REPO_ROOT/hosts"

USER="root"

REMOTE_COMMAND="
cd ~ && \
git clone https://github.com/magnific0/wondershaper.git && \
cd wondershaper && \
sudo make install
"
PARALLEL=5

echo "Running command on all nodes..."
pdsh -R ssh -w ^"$HOSTS_FILE" -l "$USER" -f "$PARALLEL" "$REMOTE_COMMAND"

if [ $? -eq 0 ]; then
	echo "Command executed successfully on all nodes."
else
	echo "Failed to execute command on some nodes."
fi
