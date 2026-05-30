#!/usr/bin/env python3
"""
Collector daemon. Runs forever, taking a network sample every
SAMPLE_INTERVAL_SEC and an occasional speed test, writing everything to SQLite.

Designed to run unattended for weeks under systemd. It never crashes on a
single bad measurement — failures are recorded as NULL/100%-loss so gaps and
outages show up in the graphs instead of disappearing.

Each sample probes the gateway + internet targets over the WIFI path, and — if
an ethernet cable is also plugged in — over the WIRED path too (the "wired
baseline"). Comparing the two is the most decisive way to tell wifi problems
from ISP problems.

Run directly for a quick manual test:
    python3 collector.py --once
"""
import sys
import time
import datetime as dt

import config
import db
import probes


def utcnow_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def probe_path(prefix, gateway, iface):
    """
    Ping the gateway + all internet targets over one interface and return a
    metrics dict. `prefix` namespaces the metric names ("" for wifi, "eth_" for
    the wired baseline). `iface` is forced as the egress interface; pass None to
    use the OS default route.
    """
    out = {}
    g_avg, g_loss = probes.ping(gateway, config.PING_COUNT, config.PING_TIMEOUT_SEC, interface=iface)
    out[f"{prefix}gateway_ping_ms"] = g_avg
    out[f"{prefix}gateway_loss_pct"] = g_loss

    pings, losses = [], []
    for label, host in config.INTERNET_TARGETS:
        avg, loss = probes.ping(host, config.PING_COUNT, config.PING_TIMEOUT_SEC, interface=iface)
        out[f"{prefix}ping_{label}_ms"] = avg
        out[f"{prefix}loss_{label}_pct"] = loss
        if avg is not None:
            pings.append(avg)
        losses.append(loss)
    # Best-case latency ignores a single struggling host; loss is averaged.
    out[f"{prefix}internet_ping_ms"] = min(pings) if pings else None
    out[f"{prefix}internet_loss_pct"] = round(sum(losses) / len(losses), 1) if losses else None
    return out


def collect_sample(net, dns_index):
    """
    Take one full sample (wifi path, optional wired baseline, dns, wifi radio)
    and write it under a single timestamp so the series line up. Returns
    (ts, values). `net` is the dict returned by detect_network().
    """
    ts = utcnow_iso()
    values = {}

    # --- WIFI path (bound to the wifi interface when we have one) ---
    values.update(probe_path("", net["wifi_gateway"], net["wifi_iface"]))

    # --- WIRED baseline (only when a cable is connected) ---
    if net["eth_iface"]:
        values.update(probe_path("eth_", net["eth_gateway"], net["eth_iface"]))

    # --- DNS resolution time (rotating domain to dodge the OS cache) ---
    domain = config.DNS_DOMAINS[dns_index % len(config.DNS_DOMAINS)]
    values["dns_ms"] = probes.dns_lookup_ms(domain)

    # --- Wifi radio stats ---
    w = probes.wifi_stats(net["wifi_iface"])
    values["wifi_signal_dbm"] = w["signal_dbm"]
    values["wifi_link_quality_pct"] = w["link_quality_pct"]
    values["wifi_bitrate_mbps"] = w["bitrate_mbps"]

    db.write_metrics(ts, values)
    return ts, values


def detect_network():
    """Discover interfaces and their gateways. Re-run periodically to follow
    DHCP changes and cables being plugged/unplugged."""
    wifi_iface = probes.wifi_interface(config.WIFI_INTERFACE)
    default_gw = probes.default_gateway()

    eth_iface = None
    if config.ETHERNET_ENABLED:
        eth_iface = probes.wired_interface(config.ETHERNET_INTERFACE)

    return {
        "wifi_iface": wifi_iface,
        # Bind the wifi path to the wifi gateway so it's truly the wifi hop even
        # when ethernet owns the default route.
        "wifi_gateway": (probes.gateway_for_interface(wifi_iface) if wifi_iface else None) or default_gw,
        "eth_iface": eth_iface,
        "eth_gateway": (probes.gateway_for_interface(eth_iface) if eth_iface else None) or default_gw,
    }


def collect_speedtest():
    if not config.SPEEDTEST_ENABLED:
        return
    res = probes.run_speedtest()
    ts = utcnow_iso()
    if res:
        db.write_speedtest(
            ts, res["download_mbps"], res["upload_mbps"], res["ping_ms"], res["server"]
        )
        print(
            f"[{ts}] speedtest: {res['download_mbps']:.1f}↓ / "
            f"{res['upload_mbps']:.1f}↑ Mbps, {res['ping_ms']:.0f}ms"
        )
    else:
        db.write_event(ts, "warn", "speedtest failed or unavailable")


def fmt(v, unit=""):
    return f"{v:.1f}{unit}" if isinstance(v, (int, float)) else "—"


def main():
    once = "--once" in sys.argv
    db.init_db()

    net = detect_network()
    start_ts = utcnow_iso()
    db.write_event(
        start_ts,
        "info",
        f"collector started; wifi={net['wifi_iface']}({net['wifi_gateway']}) "
        f"eth={net['eth_iface']}({net['eth_gateway']}) "
        f"interval={config.SAMPLE_INTERVAL_SEC}s",
    )
    print(f"[{start_ts}] collector started")
    print(f"  wifi interface: {net['wifi_iface'] or '(none)'}  gw={net['wifi_gateway']}")
    print(f"  wired baseline: {net['eth_iface'] or '(no cable / disabled)'}"
          + (f"  gw={net['eth_gateway']}" if net["eth_iface"] else ""))
    print(f"  interval:       {config.SAMPLE_INTERVAL_SEC}s")
    print(f"  db:             {config.DB_PATH}")

    dns_index = 0
    last_speedtest = 0.0
    last_detect = time.time()

    while True:
        loop_start = time.time()

        # Re-detect interfaces/gateways occasionally (DHCP, cable plug/unplug).
        if loop_start - last_detect > 300:
            new = detect_network()
            if new != net:
                db.write_event(utcnow_iso(), "info", f"network changed: {net} -> {new}")
                net = new
            last_detect = loop_start

        try:
            ts, v = collect_sample(net, dns_index)
            dns_index += 1
            line = (
                f"[{ts}] wifi: gw={fmt(v['gateway_ping_ms'],'ms')}/{fmt(v['gateway_loss_pct'],'%')} "
                f"inet={fmt(v['internet_ping_ms'],'ms')} "
                f"sig={fmt(v['wifi_signal_dbm'],'dBm')}"
            )
            if net["eth_iface"]:
                line += (f"  |  wired: gw={fmt(v.get('eth_gateway_ping_ms'),'ms')} "
                         f"inet={fmt(v.get('eth_internet_ping_ms'),'ms')}")
            line += f"  dns={fmt(v['dns_ms'],'ms')}"
            print(line)
        except Exception as e:  # noqa: BLE001 - loop must survive anything
            db.write_event(utcnow_iso(), "error", f"sample failed: {e}")
            print(f"sample error: {e}", file=sys.stderr)

        # Periodic speed test (uses real bandwidth, so it's infrequent).
        if config.SPEEDTEST_ENABLED and (loop_start - last_speedtest) >= config.SPEEDTEST_INTERVAL_SEC:
            try:
                collect_speedtest()
            except Exception as e:  # noqa: BLE001
                db.write_event(utcnow_iso(), "error", f"speedtest error: {e}")
            last_speedtest = loop_start

        # Optional retention pruning, once per loop is cheap enough.
        if config.RETENTION_DAYS > 0:
            cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.RETENTION_DAYS))
            db.prune(cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"))

        if once:
            break

        # Sleep the remainder of the interval (account for time spent sampling).
        elapsed = time.time() - loop_start
        time.sleep(max(1.0, config.SAMPLE_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    main()
