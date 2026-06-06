#!/usr/bin/env bash
# Uninstall the network profiler services and optionally remove data.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Uninstalling network profiler from $DIR"

# Detect platform
if [[ "$(uname)" == "Darwin" ]]; then
  # macOS — launchd agents
  AGENTS="$HOME/Library/LaunchAgents"
  for label in com.netprofiler.collector com.netprofiler.dashboard; do
    if [[ -f "$AGENTS/$label.plist" ]]; then
      echo "==> Stopping $label"
      launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
      rm -f "$AGENTS/$label.plist"
    fi
  done
else
  # Linux — systemd services
  for unit in netprofiler-collector netprofiler-dashboard; do
    if systemctl list-unit-files "$unit.service" >/dev/null 2>&1; then
      echo "==> Stopping $unit"
      sudo systemctl stop "$unit" 2>/dev/null || true
      sudo systemctl disable "$unit" 2>/dev/null || true
      sudo rm -f "/etc/systemd/system/$unit.service"
    fi
  done
  sudo systemctl daemon-reload
fi

# Remove venv
if [[ -d "$DIR/.venv" ]]; then
  echo "==> Removing virtualenv"
  rm -rf "$DIR/.venv"
fi

# Optionally remove collected data
if [[ -d "$DIR/data" ]]; then
  read -rp "==> Delete collected data ($DIR/data)? [y/N] " ans
  if [[ "${ans,,}" == "y" ]]; then
    rm -rf "$DIR/data"
    echo "    Data deleted."
  else
    echo "    Data kept. Remove manually: rm -rf $DIR/data"
  fi
fi

echo ""
echo "==> Uninstall complete. To reinstall, run ./install.sh (Linux) or ./install-macos.sh (macOS)."
