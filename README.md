# rasp-network-profiler

A small app for a **Raspberry Pi** (or a **Mac**) that you leave connected to your
network for a couple of weeks to figure out **whether slowdowns are your wifi or
your internet**.

## How it tells wifi vs internet apart

Every minute it measures two separate paths:

| Path | What it measures | What a problem there means |
|------|------------------|----------------------------|
| **Router / gateway** | ping latency + packet loss to your own router | a **wifi / local** problem |
| **Internet** | ping latency + packet loss to `1.1.1.1`, `8.8.8.8`, `9.9.9.9` | an **ISP / internet** problem |
| **Wired baseline** *(optional)* | the **same** gateway + internet targets pinged over an ethernet cable | see below — the decisive test |

### The wired baseline (the most decisive test)

If you also plug the device into ethernet, the collector pings the **same router
and the same internet hosts over the cable** every sample, alongside the wifi
measurements. Because both paths share the same router and ISP, comparing them
isolates the wifi:

- Internet slow over **wifi** but fine over the **cable** → it's your **wifi**
  (the ISP is proven fine).
- Internet slow over **both** wifi and cable → it's your **internet / ISP**.

The verdict uses this automatically and will say e.g. *"internet problems
disappear on the wired baseline, so it's the wifi, not the ISP."* The baseline is
skipped automatically whenever no cable is connected — wifi-only still works
exactly as before.

> The baseline forces traffic out each interface (`ping -I <iface>` on Linux,
> `ping -b <iface>` on macOS). This works out of the box on Raspberry Pi OS and
> macOS. If wired rows show 100% loss despite a working cable, your `ping` binary
> may lack the capability to bind an interface — `sudo setcap cap_net_raw+ep $(which ping)`.

It also records **wifi signal strength**, **link rate**, **DNS lookup time**, and
runs an hourly **speed test** (optional). By comparing the router path to the
internet path it produces a plain-language verdict:

- Router slow/lossy → **it's your wifi**
- Router fine but internet slow → **it's your ISP**
- Weak signal explains *why* the wifi is struggling

## Install

### Raspberry Pi / Linux
```bash
git clone <this repo> rasp-network-profiler
cd rasp-network-profiler
./install.sh
```
This sets up a virtualenv, installs `iw` if needed, optionally installs the
[Ookla speedtest CLI](https://www.speedtest.net/apps/cli), and runs the collector
+ dashboard as systemd services that start on boot and auto-restart.

### macOS
```bash
cd rasp-network-profiler
./install-macos.sh
```
Installs the same two processes as launchd agents. Wi-Fi signal is read via
`system_profiler` (no sudo needed).

### Speed test CLI (optional)

The installer will offer to set this up, or you can do it manually:

**Raspberry Pi / Debian:**
```bash
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest
```

**macOS:**
```bash
brew install speedtest-cli
```

Without it, speed tests fall back to the Python `speedtest-cli` package if
installed, or are skipped entirely. Everything else works fine either way.

Then open **http://<device-ip>:8080/** (or http://localhost:8080 on a Mac).

## Run manually (no service)
```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python3 collector.py        # start collecting (Ctrl-C to stop)
python3 collector.py --once # take a single sample and exit (good for testing)
python3 dashboard.py        # serve the dashboard
```

## Reports

The dashboard has **Open report** / **Download** buttons, or generate one from the CLI:

```bash
python3 report.py                 # last 7 days  -> report.html + a text summary
python3 report.py --days 14       # last 2 weeks
python3 report.py --out wk.html   # custom filename
python3 report.py --text          # text summary only
```

The HTML report is self-contained (data embedded) so you can email it or open it
anywhere. It leads with the verdict, then key numbers, then the full charts.

### Auto-generate a report on a schedule

**Linux (cron):** email yourself a fresh report every Monday:
```cron
0 8 * * 1 /path/to/.venv/bin/python /path/to/report.py --days 7 --out /path/to/data/weekly.html
```

**macOS:** add a launchd agent like the included ones with `StartCalendarInterval`,
or just run `report.py` from `cron`/`launchd` the same way.

## What you'll see (graphs)

- **WiFi vs Wired baseline — internet latency & loss** — the decisive charts when
  a cable is plugged in: the same internet over wifi vs over ethernet.
- **Latency: router vs internet** — the key chart. Orange = your wifi/router.
- **Packet loss** — same split.
- **Wifi signal strength (dBm)** — closer to 0 is stronger; < -67 marginal, < -75 weak.
- **Wifi link rate (Mbps)**
- **DNS resolution time (ms)**
- **Speed test — throughput** (download & upload Mbps over time)
- **Speed test — ping** (latency to the speed-test server)

## Configuration

All settings are environment variables (see `config.py` for the full list):

| Var | Default | Meaning |
|-----|---------|---------|
| `NETPROF_INTERVAL` | 60 | seconds between samples |
| `NETPROF_TARGET_1/2/3` | 1.1.1.1 / 8.8.8.8 / 9.9.9.9 | internet ping targets |
| `NETPROF_SPEEDTEST` | 1 | set `0` to disable speed tests |
| `NETPROF_SPEEDTEST_INTERVAL` | 3600 | seconds between speed tests |
| `NETPROF_PORT` | 8080 | dashboard port |
| `NETPROF_WIFI_IFACE` | auto | force a wifi interface (e.g. `wlan0`, `en0`) |
| `NETPROF_ETHERNET` | 1 | set `0` to disable the wired baseline |
| `NETPROF_ETH_IFACE` | auto | force a wired interface (e.g. `eth0`) |
| `NETPROF_RETENTION_DAYS` | 0 | auto-delete data older than N days (0 = keep) |

On Linux, set these in the systemd unit (`Environment=NETPROF_INTERVAL=30`); on
macOS, add an `EnvironmentVariables` dict to the plist.

## Files

```
collector.py   long-running sampler -> SQLite
probes.py      ping / dns / wifi / speedtest (Linux + macOS)
analysis.py    wifi-vs-internet verdict logic
dashboard.py   Flask live dashboard + report routes
report.py      standalone HTML/text report generator
db.py          SQLite storage (data/netprofiler.db)
config.py      settings (env-overridable)
templates/     dashboard HTML
uninstall.sh   remove services + data
systemd/       Linux service units
macos/         launchd agent plists
```

## Updating an existing install

Pull the latest code and restart the services — no reinstall needed:

**Linux / Raspberry Pi:**
```bash
cd /path/to/rasp-network-profiler
git pull
sudo systemctl restart netprofiler-collector netprofiler-dashboard
```

**macOS:**
```bash
cd /path/to/rasp-network-profiler
git pull
launchctl unload ~/Library/LaunchAgents/com.netprofiler.collector.plist
launchctl unload ~/Library/LaunchAgents/com.netprofiler.dashboard.plist
launchctl load ~/Library/LaunchAgents/com.netprofiler.collector.plist
launchctl load ~/Library/LaunchAgents/com.netprofiler.dashboard.plist
```

If `requirements.txt` changed, also run `.venv/bin/pip install -r requirements.txt`
before restarting.

## Uninstall

To remove the services, venv, and optionally the collected data:

```bash
cd /path/to/rasp-network-profiler
./uninstall.sh
```

Works on both Linux and macOS. It will prompt before deleting your data.
To start fresh, run the install script again after uninstalling.

## Notes

- Data lives in `data/netprofiler.db` (SQLite, WAL mode so reads/writes don't block).
- The collector never crashes on a bad measurement — outages are recorded as
  100% loss / gaps so they show up in the graphs instead of disappearing.
- Charts load Chart.js from a CDN; the device needs occasional internet for the
  dashboard UI (the data collection itself works fully offline).
