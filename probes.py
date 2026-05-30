"""
Low-level network probes: gateway discovery, ping, DNS timing, wifi stats,
and speed tests. Everything degrades gracefully — a failed probe returns None
rather than raising, so the collector loop never dies on a transient error.

Works on both Linux (Raspberry Pi OS) and macOS. Platform-specific bits
(gateway lookup, ping flags, wifi radio stats) are dispatched at runtime.
"""
import os
import re
import sys
import glob
import json
import shutil
import socket
import subprocess
import time

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _run(cmd, timeout):
    """Run a command, return stdout (str) or None on failure/timeout."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def default_gateway():
    """Return the default gateway IP, or None."""
    # Linux: `ip route show default` -> "default via 192.168.1.1 dev wlan0 ..."
    out = _run(["ip", "route", "show", "default"], timeout=5)
    if out:
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    # macOS: `route -n get default`
    out = _run(["route", "-n", "get", "default"], timeout=5)
    if out:
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    return None


def wifi_interface(preferred="auto"):
    """Return the wireless interface name (e.g. wlan0 / en0), or None if wired."""
    if preferred and preferred != "auto":
        return preferred
    if IS_LINUX:
        # /sys/class/net/<iface>/wireless exists only for wireless interfaces.
        for path in sorted(glob.glob("/sys/class/net/*/wireless")):
            return path.split("/")[-2]
        return None
    if IS_MAC:
        # Find the device backing the Wi-Fi service (usually en0).
        out = _run(["networksetup", "-listallhardwareports"], timeout=5)
        if out:
            m = re.search(r"Hardware Port:\s*Wi-Fi\s*\nDevice:\s*(\w+)", out)
            if m:
                return m.group(1)
        return "en0"
    return None


def ping(host, count, timeout_per_ping):
    """
    Ping a host. Returns (avg_rtt_ms, loss_pct). On total failure returns
    (None, 100.0). loss is always returned so outages are recorded, not lost.
    """
    if not host:
        return (None, 100.0)
    if IS_MAC:
        # macOS -W is per-packet timeout in milliseconds.
        cmd = ["ping", "-c", str(count), "-W", str(timeout_per_ping * 1000), host]
    else:
        # Linux -W is per-packet timeout in seconds.
        cmd = ["ping", "-c", str(count), "-W", str(timeout_per_ping), host]
    out = _run(cmd, timeout=count * (timeout_per_ping + 1) + 5)
    if not out:
        return (None, 100.0)

    loss = 100.0
    m = re.search(r"([\d.]+)%\s*packet loss", out)
    if m:
        loss = float(m.group(1))

    avg = None
    # Linux: rtt min/avg/max/mdev = 1.1/2.2/3.3/0.4 ms
    # macOS: round-trip min/avg/max/stddev = 1.1/2.2/3.3/0.4 ms
    m = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
    if m:
        avg = float(m.group(1))
    return (avg, loss)


def dns_lookup_ms(domain):
    """Time a DNS A-record lookup in ms. Returns None on failure."""
    try:
        start = time.perf_counter()
        socket.getaddrinfo(domain, None, family=socket.AF_INET)
        return (time.perf_counter() - start) * 1000.0
    except (socket.gaierror, OSError):
        return None


def _wifi_stats_linux(interface):
    stats = {"signal_dbm": None, "link_quality_pct": None, "bitrate_mbps": None}
    out = _run(["iw", "dev", interface, "link"], timeout=5)
    if out:
        m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
        if m:
            stats["signal_dbm"] = float(m.group(1))
        m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", out)
        if m:
            stats["bitrate_mbps"] = float(m.group(1))
    # /proc/net/wireless: link quality is column "link" out of (usually) 70.
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if line.strip().startswith(interface + ":"):
                    parts = line.split()
                    quality = float(parts[2].rstrip("."))
                    stats["link_quality_pct"] = round(quality / 70.0 * 100.0, 1)
                    if stats["signal_dbm"] is None:
                        stats["signal_dbm"] = float(parts[3].rstrip("."))
    except (OSError, IndexError, ValueError):
        pass
    return stats


def _wifi_stats_mac(interface):
    stats = {"signal_dbm": None, "link_quality_pct": None, "bitrate_mbps": None}
    # system_profiler reports the current AP's RSSI/noise/tx-rate without sudo.
    out = _run(["system_profiler", "SPAirPortDataType"], timeout=15)
    if out:
        m = re.search(r"Signal\s*/\s*Noise:\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", out)
        if m:
            signal = float(m.group(1))
            noise = float(m.group(2))
            stats["signal_dbm"] = signal
            # Approximate link quality from SNR: ~40 dB SNR ≈ excellent (100%).
            snr = signal - noise
            stats["link_quality_pct"] = round(max(0.0, min(100.0, snr / 40.0 * 100.0)), 1)
        m = re.search(r"Transmit Rate:\s*([\d.]+)", out)
        if m:
            stats["bitrate_mbps"] = float(m.group(1))
    # Newer macOS may need `wdutil info` (requires sudo) — try it if RSSI missing.
    if stats["signal_dbm"] is None and shutil.which("wdutil"):
        out = _run(["wdutil", "info"], timeout=10)
        if out:
            m = re.search(r"RSSI\s*:\s*(-?\d+)\s*dBm", out)
            if m:
                stats["signal_dbm"] = float(m.group(1))
            m = re.search(r"Tx Rate\s*:\s*([\d.]+)\s*Mbps", out)
            if m:
                stats["bitrate_mbps"] = float(m.group(1))
    return stats


def wifi_stats(interface):
    """
    Return dict with signal_dbm, link_quality_pct, bitrate_mbps (any may be None).
    """
    stats = {"signal_dbm": None, "link_quality_pct": None, "bitrate_mbps": None}
    if not interface:
        return stats
    if IS_MAC:
        return _wifi_stats_mac(interface)
    return _wifi_stats_linux(interface)


def run_speedtest(timeout=120):
    """
    Run a speed test. Returns dict(download_mbps, upload_mbps, ping_ms, server)
    or None. Tries the `speedtest` Ookla binary first (JSON), then the
    speedtest-cli python package. Both work on macOS and Linux.
    """
    if shutil.which("speedtest"):
        out = _run(
            ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"],
            timeout=timeout,
        )
        if out:
            try:
                d = json.loads(out)
                return {
                    "download_mbps": d["download"]["bandwidth"] * 8 / 1e6,
                    "upload_mbps": d["upload"]["bandwidth"] * 8 / 1e6,
                    "ping_ms": d["ping"]["latency"],
                    "server": d.get("server", {}).get("name", ""),
                }
            except (ValueError, KeyError):
                pass
    try:
        import speedtest  # type: ignore

        st = speedtest.Speedtest(secure=True)
        st.get_best_server()
        down = st.download() / 1e6
        up = st.upload() / 1e6
        res = st.results.dict()
        return {
            "download_mbps": down,
            "upload_mbps": up,
            "ping_ms": res.get("ping"),
            "server": (res.get("server") or {}).get("sponsor", ""),
        }
    except Exception:
        return None
