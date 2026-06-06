#!/usr/bin/env bash
# Install the network profiler as launchd agents on macOS.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"

echo "==> Installing network profiler in $DIR (macOS)"

python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"
mkdir -p "$DIR/data" "$AGENTS"

if ! command -v speedtest >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "==> (optional) Ookla 'speedtest' CLI not found."
    read -rp "    Install it via Homebrew now? [y/N] " ans
    if [[ "${ans,,}" == "y" ]]; then
      brew install speedtest-cli
    else
      echo "    Skipped. To install later: brew install speedtest-cli"
    fi
  else
    echo "==> (optional) For speed tests: brew install speedtest-cli"
  fi
fi

for label in com.netprofiler.collector com.netprofiler.dashboard; do
  sed -e "s#__DIR__#$DIR#g" "$DIR/macos/$label.plist" > "$AGENTS/$label.plist"
  launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$label.plist"
done

echo ""
echo "==> Done! Collector and dashboard are running as launchd agents."
echo "    Dashboard:  http://localhost:8080/"
echo "    Logs:       tail -f $DIR/data/collector.log"
echo "    To stop:    launchctl unload $AGENTS/com.netprofiler.*.plist"
echo ""
echo "    NOTE: macOS Wi-Fi RSSI via system_profiler works without sudo. If signal"
echo "    shows as blank on newer macOS, run the collector once with: sudo wdutil info"
echo "    to confirm Wi-Fi details are accessible."
