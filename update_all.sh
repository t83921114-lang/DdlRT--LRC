#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
SOURCE_DIR="$REPO_ROOT"
HOSTS_FILE="$REPO_ROOT/hosts"
REMOTE_DIR="${REMOTE_DIR:-DdlRT}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# check if the hosts file exists
if [[ ! -f "$HOSTS_FILE" ]]; then
    echo "Error: hosts file not found!"
    exit 1
fi

# iterate over each IP address in the hosts file
while read -r ip; do
    [[ -z "$ip" ]] && continue

    echo "Copying to host: $ip..."

    # use rsync to copy the folder
    sudo rsync -avz --delete --exclude='project/cmake/build/CMakeFiles' --exclude='project/cmake/build/run_client' --exclude='project/cmake/build/main_test' --exclude='project/cmake/build/main_client' --exclude='storage/*' --exclude='experiments/results/' --exclude='**/__pycache__/' --exclude='*.pyc' -e "ssh $SSH_OPTS" "$SOURCE_DIR/" "$ip:$REMOTE_DIR/"
    #rsync -avz -e ssh "$SOURCE_DIR/" "$ip:$REMOTE_DIR/"

    # check if rsync is successful
    if [ $? -eq 0 ]; then
        echo "Successfully copied to $ip!"
    else
        echo "Failed to copy to $ip!"
    fi

done < "$HOSTS_FILE"

cd "$REPO_ROOT"
bash "$REPO_ROOT/scripts/generate_run_proxy.sh"

echo "All done!"