"""
Configuration for the Raspberry Pi network profiler.

Everything is overridable via environment variables so you don't have to edit
this file on the Pi. Defaults are chosen to be safe to run unattended for weeks.
"""
import os


def _env(name, default):
    return os.environ.get(name, default)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Where the SQLite database lives. Keep it on the SD card / disk, not tmpfs,
# so the multi-week history survives reboots.
DB_PATH = _env("NETPROF_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "netprofiler.db"))

# Network interface to read wifi stats from. "auto" picks the first wireless
# interface it finds (usually wlan0 on a Pi).
WIFI_INTERFACE = _env("NETPROF_WIFI_IFACE", "auto")

# How often to take a sample (latency / loss / wifi), in seconds.
SAMPLE_INTERVAL_SEC = _env_int("NETPROF_INTERVAL", 60)

# Internet targets to ping. These represent "the internet" path. We use a few
# so a single host having a bad moment doesn't look like an outage.
# Format: list of (label, host). The label is used in graphs and the DB.
INTERNET_TARGETS = [
    ("cloudflare", _env("NETPROF_TARGET_1", "1.1.1.1")),
    ("google", _env("NETPROF_TARGET_2", "8.8.8.8")),
    ("quad9", _env("NETPROF_TARGET_3", "9.9.9.9")),
]

# Domains used to measure DNS resolution time. Rotated so the OS cache doesn't
# hide real resolver latency.
DNS_DOMAINS = [
    "wikipedia.org",
    "github.com",
    "cloudflare.com",
    "amazon.com",
    "microsoft.com",
]

# Number of pings per sample and per-ping timeout (seconds).
PING_COUNT = _env_int("NETPROF_PING_COUNT", 5)
PING_TIMEOUT_SEC = _env_int("NETPROF_PING_TIMEOUT", 2)

# Speed test settings. Speed tests use real bandwidth, so they run infrequently.
# Requires the `speedtest-cli` python package OR the `speedtest` Ookla binary.
# Set NETPROF_SPEEDTEST=0 to disable entirely.
SPEEDTEST_ENABLED = _env("NETPROF_SPEEDTEST", "1") == "1"
SPEEDTEST_INTERVAL_SEC = _env_int("NETPROF_SPEEDTEST_INTERVAL", 3600)  # hourly

# Dashboard web server.
DASHBOARD_HOST = _env("NETPROF_HOST", "0.0.0.0")
DASHBOARD_PORT = _env_int("NETPROF_PORT", 8080)

# Optional: prune samples older than this many days (0 = keep forever).
RETENTION_DAYS = _env_int("NETPROF_RETENTION_DAYS", 0)
