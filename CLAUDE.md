# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Network diagnostics tool for Raspberry Pi and macOS that runs continuously for weeks, collecting metrics to distinguish WiFi/local network problems from ISP/Internet problems. It samples every 60 seconds, stores data in SQLite, and presents results via a Flask dashboard and self-contained HTML reports.

## Running the Project

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Collect a single sample (useful for testing probes)
python3 collector.py --once

# Run continuous collector
python3 collector.py

# Start web dashboard (port 8080)
python3 dashboard.py

# Generate report
python3 report.py --days 14
```

Install as system service: `./install.sh` (Linux/systemd) or `./install-macos.sh` (macOS/launchd).

## Architecture

The system has two long-running processes (collector + dashboard) sharing a SQLite database in WAL mode:

- **probes.py** — Platform-abstracted network measurements (ping, DNS, WiFi stats, speedtest) using subprocess calls to OS tools (`ping`, `iw`, `ip route`, `system_profiler`). Detects platform at import time (`IS_MAC`, `IS_LINUX`) and selects implementations accordingly.
- **collector.py** — Daemon loop that calls probes every interval and writes to the database. Never crashes on measurement failures; records NULLs or 100% loss so gaps are visible in charts.
- **db.py** — SQLite layer with three tables: `metrics` (long format: one row per metric per timestamp), `speedtests`, `events`. Uses WAL for concurrent read/write.
- **analysis.py** — Verdict engine that compares aggregated metrics against thresholds to classify issues as WiFi, ISP, or both. Includes wired baseline comparison logic (if internet is slow on WiFi but clean over wired → WiFi problem).
- **dashboard.py** — Flask server with JSON API endpoints (`/api/summary`, `/api/series`) and HTML report rendering (`/report`, `/report.html`).
- **report.py** — Standalone CLI for generating self-contained HTML reports with embedded Chart.js data.
- **config.py** — All settings via environment variables prefixed `NETPROF_` (interval, targets, interfaces, ports, retention).

## Key Design Decisions

- **Only dependency is Flask** — keeps installation lightweight for Raspberry Pi. All network probing uses stdlib `subprocess` calls to system tools.
- **Wired baseline probing** — When an Ethernet interface is connected, the same measurements are repeated over the wired link to definitively separate WiFi issues from ISP issues. Ping uses interface binding (`-I` on Linux, `-b` on macOS).
- **Long-format metrics table** — Each measurement is a separate row keyed by (timestamp, metric_name), not one wide row per sample. This makes the schema flexible for adding new probe types.
- **Templates use Chart.js + Luxon** — Dashboard and reports render interactive charts client-side with embedded data.
