#!/usr/bin/env bash
# Install the network profiler as systemd services on a Raspberry Pi / Linux.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"

echo "==> Installing network profiler in $DIR for user $USER_NAME"

# 1. Python venv + deps
echo "==> Creating virtualenv"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"

mkdir -p "$DIR/data"

# 2. Make sure the wifi/ping tools exist
if ! command -v iw >/dev/null 2>&1; then
  echo "==> 'iw' not found — installing (needed for wifi stats)"
  sudo apt-get update -qq && sudo apt-get install -y iw
fi

# 3. Optional Ookla speedtest CLI (skip silently if unavailable)
if ! command -v speedtest >/dev/null 2>&1; then
  echo "==> (optional) Ookla 'speedtest' CLI not found. Speed tests will fall back"
  echo "    to the python speedtest-cli if installed, or be skipped. To install:"
  echo "    https://www.speedtest.net/apps/cli"
fi

# 4. Install systemd units with paths substituted
echo "==> Installing systemd services"
for unit in netprofiler-collector netprofiler-dashboard; do
  sed -e "s#__DIR__#$DIR#g" -e "s#__USER__#$USER_NAME#g" \
    "$DIR/systemd/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done

sudo systemctl daemon-reload
sudo systemctl enable --now netprofiler-collector netprofiler-dashboard

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "==> Done! Collector and dashboard are running."
echo "    Dashboard:  http://${IP:-<pi-ip>}:8080/"
echo "    Logs:       journalctl -u netprofiler-collector -f"
echo "    Let it run for a couple of weeks, then open the dashboard or run:"
echo "      $DIR/.venv/bin/python $DIR/report.py --days 14"
