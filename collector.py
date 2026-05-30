#!/usr/bin/env python3
"""
Collector daemon. Runs forever, taking a network sample every
SAMPLE_INTERVAL_SEC and an occasional speed test, writing everything to SQLite.

Designed to run unattended for weeks under systemd. It never crashes on a
single bad measurement — failures are recorded as NULL/100%-loss so gaps and
outages show up in the graphs instead of disappearing.

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


def collect_sample(gateway, wifi_iface, dns_index):
    """Take one full sample and write it. Returns the values dict."""
    ts = utcnow_iso()
    values = {}

    # --- Gateway (wifi/local path) ---
    g_avg, g_loss = probes.ping(gateway, config.PING_COUNT, config.PING_TIMEOUT_SEC)
    values["gateway_ping_ms"] = g_avg
    values["gateway_loss_pct"] = g_loss

    # --- Internet path (one set of metrics per target) ---
    inet_pings = []
    for label, host in config.INTERNET_TARGETS:
        avg, loss = probes.ping(host, config.PING_COUNT, config.PING_TIMEOUT_SEC)
        values[f"ping_{label}_ms"] = avg
        values[f"loss_{label}_pct"] = loss
        if avg is not None:
            inet_pings.append(avg)
    # Best-case internet latency: ignores a single struggling host.
    values["internet_ping_ms"] = min(inet_pings) if inet_pings else None

    # --- DNS resolution time (rotating domain to dodge the OS cache) ---
    domain = config.DNS_DOMAINS[dns_index % len(config.DNS_DOMAINS)]
    values["dns_ms"] = probes.dns_lookup_ms(domain)

    # --- Wifi radio stats ---
    w = probes.wifi_stats(wifi_iface)
    values["wifi_signal_dbm"] = w["signal_dbm"]
    values["wifi_link_quality_pct"] = w["link_quality_pct"]
    values["wifi_bitrate_mbps"] = w["bitrate_mbps"]

    db.write_metrics(ts, values)
    return ts, values


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

    gateway = probes.default_gateway()
    wifi_iface = probes.wifi_interface(config.WIFI_INTERFACE)
    start_ts = utcnow_iso()
    db.write_event(
        start_ts,
        "info",
        f"collector started; gateway={gateway} wifi={wifi_iface} "
        f"interval={config.SAMPLE_INTERVAL_SEC}s",
    )
    print(f"[{start_ts}] collector started")
    print(f"  gateway:        {gateway}")
    print(f"  wifi interface: {wifi_iface or '(none / wired)'}")
    print(f"  interval:       {config.SAMPLE_INTERVAL_SEC}s")
    print(f"  db:             {config.DB_PATH}")

    dns_index = 0
    last_speedtest = 0.0
    last_gateway_check = 0.0

    while True:
        loop_start = time.time()

        # Re-detect the gateway occasionally in case of DHCP changes / reconnect.
        if loop_start - last_gateway_check > 300:
            new_gw = probes.default_gateway()
            if new_gw and new_gw != gateway:
                db.write_event(utcnow_iso(), "info", f"gateway changed {gateway} -> {new_gw}")
                gateway = new_gw
            last_gateway_check = loop_start

        try:
            ts, v = collect_sample(gateway, wifi_iface, dns_index)
            dns_index += 1
            print(
                f"[{ts}] gw={fmt(v['gateway_ping_ms'],'ms')}/{fmt(v['gateway_loss_pct'],'%')} "
                f"inet={fmt(v['internet_ping_ms'],'ms')} "
                f"dns={fmt(v['dns_ms'],'ms')} "
                f"sig={fmt(v['wifi_signal_dbm'],'dBm')}"
            )
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
