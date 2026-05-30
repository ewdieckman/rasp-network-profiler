"""
Turn raw samples into a verdict: is the problem the WIFI/local link, or the
INTERNET/ISP? Shared by the live dashboard and the generated report.

Core logic:
  * The hop to your gateway/router reflects the WIFI + local link.
  * The hop to internet hosts, minus the gateway component, reflects the ISP.
  * Wifi signal strength explains *why* the local link is bad.

So:
  - high gateway latency or loss  -> WIFI / local problem
  - gateway fine but internet bad -> INTERNET / ISP problem
  - weak signal alongside bad gateway -> confirms wifi coverage issue
"""
import statistics as stats
import datetime as dt

import config
import db


# Thresholds for classifying "bad". Tunable; chosen to be conservative.
GATEWAY_PING_WARN_MS = 15.0     # local round trip should be tiny
GATEWAY_PING_BAD_MS = 40.0
GATEWAY_LOSS_WARN_PCT = 1.0
GATEWAY_LOSS_BAD_PCT = 5.0

INTERNET_PING_WARN_MS = 60.0    # beyond the gateway component
INTERNET_PING_BAD_MS = 120.0
INTERNET_LOSS_WARN_PCT = 1.0
INTERNET_LOSS_BAD_PCT = 5.0

SIGNAL_WARN_DBM = -67.0         # -67 dBm ~ reliable threshold for most uses
SIGNAL_BAD_DBM = -75.0


def _median(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return stats.median(vals) if vals else None


def _pct(values, q):
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, int(round((len(vals) - 1) * q))))
    return vals[k]


def _avg(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def since_iso(hours):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize(hours=24, path=None):
    """
    Build a structured summary + verdict over the last `hours`.
    Returns a dict suitable for JSON or templating.
    """
    metrics = [
        "gateway_ping_ms", "gateway_loss_pct",
        "internet_ping_ms", "internet_loss_pct", "dns_ms",
        "wifi_signal_dbm", "wifi_link_quality_pct", "wifi_bitrate_mbps",
        # Wired baseline (present only when a cable is plugged in).
        "eth_gateway_ping_ms", "eth_gateway_loss_pct",
        "eth_internet_ping_ms", "eth_internet_loss_pct",
    ]
    # Per-target internet loss columns.
    for label, _ in config.INTERNET_TARGETS:
        metrics.append(f"loss_{label}_pct")
        metrics.append(f"ping_{label}_ms")

    series = db.fetch_series(metrics, since_iso(hours), path=path)

    def col(name):
        return [v for _, v in series.get(name, []) if v is not None]

    gw_ping = col("gateway_ping_ms")
    gw_loss = col("gateway_loss_pct")
    inet_ping = col("internet_ping_ms")
    dns = col("dns_ms")
    signal = col("wifi_signal_dbm")
    bitrate = col("wifi_bitrate_mbps")

    # Average internet loss across targets (prefer the aggregate column written
    # by newer collectors; fall back to per-target columns for old data).
    inet_loss = col("internet_loss_pct")
    if not inet_loss:
        for label, _ in config.INTERNET_TARGETS:
            inet_loss.extend(col(f"loss_{label}_pct"))

    # Wired baseline series.
    eth_gw_ping = col("eth_gateway_ping_ms")
    eth_gw_loss = col("eth_gateway_loss_pct")
    eth_inet_ping = col("eth_internet_ping_ms")
    eth_inet_loss = col("eth_internet_loss_pct")
    eth_present = len(series.get("eth_internet_ping_ms", [])) > 0

    sample_count = len(series.get("gateway_ping_ms", []))

    s = {
        "window_hours": hours,
        "sample_count": sample_count,
        "gateway": {
            "ping_median_ms": _round(_median(gw_ping)),
            "ping_p95_ms": _round(_pct(gw_ping, 0.95)),
            "loss_avg_pct": _round(_avg(gw_loss)),
            "loss_max_pct": _round(max(gw_loss) if gw_loss else None),
        },
        "internet": {
            "ping_median_ms": _round(_median(inet_ping)),
            "ping_p95_ms": _round(_pct(inet_ping, 0.95)),
            "loss_avg_pct": _round(_avg(inet_loss)),
            "loss_max_pct": _round(max(inet_loss) if inet_loss else None),
        },
        "dns": {
            "median_ms": _round(_median(dns)),
            "p95_ms": _round(_pct(dns, 0.95)),
        },
        "wifi": {
            "signal_median_dbm": _round(_median(signal)),
            "signal_min_dbm": _round(min(signal) if signal else None),
            "bitrate_median_mbps": _round(_median(bitrate)),
        },
        "wired": {
            "present": eth_present,
            "gateway_ping_median_ms": _round(_median(eth_gw_ping)),
            "gateway_loss_max_pct": _round(max(eth_gw_loss) if eth_gw_loss else None),
            "ping_median_ms": _round(_median(eth_inet_ping)),
            "ping_p95_ms": _round(_pct(eth_inet_ping, 0.95)),
            "loss_avg_pct": _round(_avg(eth_inet_loss)),
            "loss_max_pct": _round(max(eth_inet_loss) if eth_inet_loss else None),
        },
    }
    s["verdict"] = _verdict(s)
    s["speedtests"] = _speedtest_summary(hours, path)
    return s


def _speedtest_summary(hours, path):
    rows = db.fetch_speedtests(since_iso(hours), path=path)
    if not rows:
        return {"count": 0}
    down = [r["download_mbps"] for r in rows if r["download_mbps"] is not None]
    up = [r["upload_mbps"] for r in rows if r["upload_mbps"] is not None]
    return {
        "count": len(rows),
        "download_median_mbps": _round(_median(down)),
        "download_min_mbps": _round(min(down) if down else None),
        "upload_median_mbps": _round(_median(up)),
        "upload_min_mbps": _round(min(up) if up else None),
    }


def _verdict(s):
    """Produce a plain-language diagnosis and severity flags."""
    g = s["gateway"]
    i = s["internet"]
    w = s["wifi"]

    wifi_issues = []
    inet_issues = []

    gp = g["ping_p95_ms"]
    gl = g["loss_max_pct"]
    sig = w["signal_min_dbm"]

    if gp is not None and gp >= GATEWAY_PING_BAD_MS:
        wifi_issues.append(f"gateway latency is high (p95 {gp} ms)")
    elif gp is not None and gp >= GATEWAY_PING_WARN_MS:
        wifi_issues.append(f"gateway latency is elevated (p95 {gp} ms)")

    if gl is not None and gl >= GATEWAY_LOSS_BAD_PCT:
        wifi_issues.append(f"packet loss to the router (up to {gl}%)")
    elif gl is not None and gl >= GATEWAY_LOSS_WARN_PCT:
        wifi_issues.append(f"some packet loss to the router (up to {gl}%)")

    if sig is not None and sig <= SIGNAL_BAD_DBM:
        wifi_issues.append(f"weak wifi signal (down to {sig} dBm)")
    elif sig is not None and sig <= SIGNAL_WARN_DBM:
        wifi_issues.append(f"marginal wifi signal (down to {sig} dBm)")

    # Internet component = how much worse the internet is than the gateway.
    ip = i["ping_p95_ms"]
    il = i["loss_max_pct"]
    gp_base = gp or 0
    if ip is not None:
        extra = ip - gp_base
        if extra >= INTERNET_PING_BAD_MS:
            inet_issues.append(f"high latency beyond your router (+{_round(extra)} ms to the internet)")
        elif extra >= INTERNET_PING_WARN_MS:
            inet_issues.append(f"elevated latency beyond your router (+{_round(extra)} ms to the internet)")
    if il is not None and gl is not None:
        extra_loss = il - gl
        if extra_loss >= INTERNET_LOSS_BAD_PCT:
            inet_issues.append(f"packet loss out on the internet (up to {_round(il)}%)")
        elif extra_loss >= INTERNET_LOSS_WARN_PCT:
            inet_issues.append(f"some packet loss out on the internet (up to {_round(il)}%)")

    # --- Wired baseline: the decisive cross-check ---
    # If the internet looks slow/lossy over wifi, but a cable to the SAME router
    # and ISP is clean, then the ISP is fine and the problem is the wifi link.
    # If even the cable is bad, it's genuinely the internet/ISP.
    wired_note = None
    wired = s.get("wired") or {}
    if wired.get("present"):
        wp = wired["ping_p95_ms"]
        wl = wired["loss_max_pct"]
        wired_clean = (
            (wp is None or wp < INTERNET_PING_WARN_MS) and
            (wl is None or wl < INTERNET_LOSS_WARN_PCT)
        )
        wired_bad = (
            (wp is not None and wp >= INTERNET_PING_BAD_MS) or
            (wl is not None and wl >= INTERNET_LOSS_BAD_PCT)
        )
        if inet_issues and wired_clean:
            # Reattribute: the internet itself is fine over the cable.
            wired_note = ("The internet was slow/lossy over wifi, but the wired "
                          "baseline to the same router and ISP is clean — so this is a "
                          "WIFI problem, not your internet provider.")
            wifi_issues = wifi_issues + ["internet problems disappear on the wired baseline (so it's the wifi, not the ISP)"]
            inet_issues = []
        elif inet_issues and wired_bad:
            wired_note = ("The wired baseline sees the same internet slowness as wifi — "
                          "this confirms an INTERNET / ISP problem, not your wifi.")
        elif not inet_issues and not wifi_issues:
            wired_note = "Wired baseline confirms a healthy connection."

    if not wifi_issues and not inet_issues:
        label = "healthy"
        headline = "No significant problems detected — both your wifi and internet look healthy in this window."
    elif wifi_issues and not inet_issues:
        label = "wifi"
        headline = "Slowdowns point to your WIFI / local network, not your internet connection."
    elif inet_issues and not wifi_issues:
        label = "internet"
        headline = "Slowdowns point to your INTERNET / ISP, not your local wifi."
    else:
        label = "both"
        headline = "Problems on BOTH your local wifi and your internet connection."

    return {
        "label": label,
        "headline": headline,
        "wifi_issues": wifi_issues,
        "internet_issues": inet_issues,
        "wired_note": wired_note,
    }


def _round(v, n=1):
    return round(v, n) if isinstance(v, (int, float)) else None
