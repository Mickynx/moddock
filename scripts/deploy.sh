#!/usr/bin/env bash
# Deploy the plugin to a handheld running Decky Loader.
# Usage: scripts/deploy.sh user@handheld-host
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?usage: scripts/deploy.sh user@handheld-host}"
PLUGIN_DIR="homebrew/plugins/moddock"

pnpm build
[ -d py_modules ] || scripts/vendor-deps.sh

# One-time on the handheld if rsync fails with permission errors:
#   ssh $HOST 'sudo chown -R $USER: ~/homebrew/plugins'
rsync -av --delete --rsync-path="mkdir -p $PLUGIN_DIR && rsync" \
  dist main.py plugin.json package.json LICENSE moddock py_modules \
  "$HOST:$PLUGIN_DIR/"

ssh "$HOST" 'sudo systemctl restart plugin_loader'
echo "Deployed. Open the Quick Access menu -> plug icon -> ModDock."
