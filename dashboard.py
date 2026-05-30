#!/usr/bin/env python3
"""
Live web dashboard for the network profiler.

  python3 dashboard.py
then open http://<pi-ip>:8080/ (or http://localhost:8080 on a Mac).

Routes:
  /                      live dashboard with auto-refreshing charts + verdict
  /api/summary?hours=24  JSON verdict + aggregate stats
  /api/series?hours=24   JSON time series for all charts
  /report?hours=168      rendered HTML report (same as report.py output)
  /report.html?hours=168 download the standalone HTML report
"""
import datetime as dt

from flask import Flask, jsonify, request, Response, render_template

import config
import db
import analysis
import report

app = Flask(__name__)


def _hours():
    try:
        return max(1, int(request.args.get("hours", 24)))
    except (TypeError, ValueError):
        return 24


@app.route("/")
def index():
    return render_template(
        "index.html",
        targets=[label for label, _ in config.INTERNET_TARGETS],
        interval=config.SAMPLE_INTERVAL_SEC,
    )


@app.route("/api/summary")
def api_summary():
    return jsonify(analysis.summarize(_hours()))


@app.route("/api/series")
def api_series():
    return jsonify({"charts": report.gather_chart_data(_hours())})


@app.route("/report")
def report_view():
    hours = _hours()
    s = analysis.summarize(hours)
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = report.build_html(s, report.gather_chart_data(hours), generated)
    return Response(html, mimetype="text/html")


@app.route("/report.html")
def report_download():
    hours = _hours()
    s = analysis.summarize(hours)
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = report.build_html(s, report.gather_chart_data(hours), generated)
    return Response(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": "attachment; filename=network_report.html"},
    )


def main():
    db.init_db()
    print(f"dashboard on http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}/")
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, threaded=True)


if __name__ == "__main__":
    main()
