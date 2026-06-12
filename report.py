#!/usr/bin/env python3
"""
Generate a standalone network report from the collected data.

  python3 report.py                      # last 7 days -> report.html + stdout summary
  python3 report.py --hours 336          # last 14 days
  python3 report.py --days 14 --out my_report.html
  python3 report.py --text               # print only the text summary

The HTML report is self-contained (data embedded inline) so you can email it
or open it anywhere. Charts use Chart.js from a CDN, with a graceful message
if you open it offline.
"""
import os
import json
import argparse
import datetime as dt

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
import db
import analysis

_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


METRIC_GROUPS = [
    {
        "title": "WiFi vs Wired baseline — internet latency",
        "desc": "The decisive comparison. Both lines go to the same internet over "
                "the same router/ISP. If wifi is worse than the wired cable, the "
                "wifi is the bottleneck. If both are equally bad, it's the ISP. "
                "(Wired only appears if a cable is plugged in.)",
        "unit": "ms",
        "series": [("internet_ping_ms", "wifi"), ("eth_internet_ping_ms", "wired")],
    },
    {
        "title": "WiFi vs Wired baseline — packet loss",
        "desc": "Same comparison for packet loss. Loss on wifi but not on the cable "
                "means a wifi problem; loss on both means an upstream/ISP problem.",
        "unit": "%",
        "series": [("internet_loss_pct", "wifi"), ("eth_internet_loss_pct", "wired")],
    },
    {
        "title": "Latency: router (wifi) vs internet",
        "desc": "If the orange 'router' line spikes, it's your wifi/local link. "
                "If only the internet lines spike, it's your ISP.",
        "unit": "ms",
        "series": [("gateway_ping_ms", "router (gateway)")]
                  + [(f"ping_{l}_ms", l) for l, _ in config.INTERNET_TARGETS],
    },
    {
        "title": "Packet loss",
        "desc": "Loss to the router means a wifi/local problem; loss only to "
                "internet hosts means an upstream problem.",
        "unit": "%",
        "series": [("gateway_loss_pct", "router (gateway)")]
                  + [(f"loss_{l}_pct", l) for l, _ in config.INTERNET_TARGETS],
    },
    {
        "title": "Wifi signal strength",
        "desc": "Closer to 0 is stronger. Below -67 dBm is marginal; below -75 dBm is weak.",
        "unit": "dBm",
        "series": [("wifi_signal_dbm", "signal")],
    },
    {
        "title": "Wifi link rate",
        "desc": "The negotiated wifi transmit rate. Drops often track with weak signal.",
        "unit": "Mbps",
        "series": [("wifi_bitrate_mbps", "tx bitrate")],
    },
    {
        "title": "DNS resolution time",
        "desc": "Slow DNS makes everything feel slow even when bandwidth is fine.",
        "unit": "ms",
        "series": [("dns_ms", "dns lookup")],
    },
]


def gather_chart_data(hours, path=None):
    all_metrics = []
    for g in METRIC_GROUPS:
        all_metrics.extend(name for name, _ in g["series"])
    series = db.fetch_series(all_metrics, analysis.since_iso(hours), path=path)
    charts = []
    for g in METRIC_GROUPS:
        datasets = []
        has_eth = any(name.startswith("eth_") for name, _ in g["series"])
        eth_empty = True
        for name, label in g["series"]:
            points = [
                {"x": ts, "y": v}
                for ts, v in series.get(name, [])
                if v is not None
            ]
            if name.startswith("eth_") and points:
                eth_empty = False
            datasets.append({"label": label, "data": points})
        # Skip wired comparison charts when no wired data was collected.
        if has_eth and eth_empty:
            continue
        charts.append({
            "title": g["title"], "desc": g["desc"],
            "unit": g["unit"], "datasets": datasets,
        })
    # Append speedtest charts from the separate speedtests table.
    rows = db.fetch_speedtests(analysis.since_iso(hours), path=path)
    if rows:
        dl_pts = [{"x": r["ts"], "y": r["download_mbps"]}
                  for r in rows if r["download_mbps"] is not None]
        ul_pts = [{"x": r["ts"], "y": r["upload_mbps"]}
                  for r in rows if r["upload_mbps"] is not None]
        ping_pts = [{"x": r["ts"], "y": r["ping_ms"]}
                    for r in rows if r["ping_ms"] is not None]
        charts.append({
            "title": "Speed test — throughput",
            "desc": "Download and upload bandwidth from periodic speed tests.",
            "unit": "Mbps", "pointRadius": 3,
            "datasets": [
                {"label": "download", "data": dl_pts},
                {"label": "upload", "data": ul_pts},
            ],
        })
        charts.append({
            "title": "Speed test — ping",
            "desc": "Latency to the speed-test server.",
            "unit": "ms", "pointRadius": 3,
            "datasets": [{"label": "ping", "data": ping_pts}],
        })
    return charts


def text_summary(s):
    v = s["verdict"]
    lines = []
    lines.append("=" * 64)
    lines.append("  NETWORK PROFILER REPORT")
    lines.append("=" * 64)
    lines.append(f"Window:  last {s['window_hours']} hours   ({s['sample_count']} samples)")
    lines.append("")
    lines.append(f"VERDICT: {v['label'].upper()}")
    lines.append(f"  {v['headline']}")
    if v["wifi_issues"]:
        lines.append("  Wifi / local findings:")
        for it in v["wifi_issues"]:
            lines.append(f"    - {it}")
    if v["internet_issues"]:
        lines.append("  Internet / ISP findings:")
        for it in v["internet_issues"]:
            lines.append(f"    - {it}")
    if v.get("wired_note"):
        lines.append(f"  Wired baseline: {v['wired_note']}")
    lines.append("")
    g, i, w = s["gateway"], s["internet"], s["wifi"]
    lines.append("ROUTER (wifi/local path):")
    lines.append(f"    ping median {g['ping_median_ms']} ms / p95 {g['ping_p95_ms']} ms")
    lines.append(f"    loss avg {g['loss_avg_pct']}% / max {g['loss_max_pct']}%")
    lines.append("INTERNET over WIFI (best of targets):")
    lines.append(f"    ping median {i['ping_median_ms']} ms / p95 {i['ping_p95_ms']} ms")
    lines.append(f"    loss avg {i['loss_avg_pct']}% / max {i['loss_max_pct']}%")
    wired = s.get("wired") or {}
    if wired.get("present"):
        lines.append("INTERNET over WIRED baseline:")
        lines.append(f"    ping median {wired['ping_median_ms']} ms / p95 {wired['ping_p95_ms']} ms")
        lines.append(f"    loss avg {wired['loss_avg_pct']}% / max {wired['loss_max_pct']}%")
    lines.append("DNS:")
    lines.append(f"    median {s['dns']['median_ms']} ms / p95 {s['dns']['p95_ms']} ms")
    lines.append("WIFI:")
    lines.append(f"    signal median {w['signal_median_dbm']} dBm / min {w['signal_min_dbm']} dBm")
    lines.append(f"    bitrate median {w['bitrate_median_mbps']} Mbps")
    st = s["speedtests"]
    if st.get("count"):
        lines.append("SPEED TESTS:")
        lines.append(f"    {st['count']} runs; download median {st['download_median_mbps']} Mbps "
                     f"(min {st['download_min_mbps']}), upload median {st['upload_median_mbps']} Mbps"
                     f", ping median {st['ping_median_ms']} ms")
    lines.append("=" * 64)
    return "\n".join(lines)


_BADGE_COLORS = {
    "healthy": "#2e7d32", "wifi": "#e65100",
    "internet": "#c62828", "both": "#6a1b9a",
}


def build_html(s, charts, generated_at):
    """Render the self-contained HTML report via the Jinja template."""
    tmpl = _env.get_template("report.html")
    return tmpl.render(
        s=s,
        verdict=s["verdict"],
        badge_color=_BADGE_COLORS.get(s["verdict"]["label"], "#555"),
        charts_json=json.dumps(charts),
        generated_at=generated_at,
    )


def main():
    ap = argparse.ArgumentParser(description="Generate a network report.")
    ap.add_argument("--hours", type=int, default=None, help="window in hours")
    ap.add_argument("--days", type=int, default=None, help="window in days")
    ap.add_argument("--out", default="report.html", help="output HTML path")
    ap.add_argument("--text", action="store_true", help="print text summary only, no HTML")
    args = ap.parse_args()

    if args.days is not None:
        hours = args.days * 24
    elif args.hours is not None:
        hours = args.hours
    else:
        hours = 7 * 24  # default: last week

    db.init_db()
    s = analysis.summarize(hours)
    print(text_summary(s))

    if not args.text:
        generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        html = build_html(s, gather_chart_data(hours), generated_at)
        with open(args.out, "w") as f:
            f.write(html)
        print(f"\nHTML report written to: {args.out}")


if __name__ == "__main__":
    main()
