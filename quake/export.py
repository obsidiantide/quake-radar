"""Serve layer: small JSON files the dashboard reads. Events are exported only after a
successful publish; run history and health are exported after EVERY run (incl. failed),
so the dashboard always shows the true state of the pipeline."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import config, storage
from .transform import connect


def _write(name: str, obj) -> None:
    config.SERVE_DIR.mkdir(parents=True, exist_ok=True)
    p = config.SERVE_DIR / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def export_events() -> int:
    events_dir = config.FINAL_DIR / "events"
    if not events_dir.exists() or not any(events_dir.glob("*.parquet")):
        _write("events.json", [])
        _write("daily.json", [])
        return 0
    glob = (events_dir / "*.parquet").as_posix()
    con = connect()
    cur = con.execute(f"""
        SELECT event_id, strftime(event_time, '%Y-%m-%dT%H:%M:%SZ') AS t, round(lat, 3) AS lat,
               round(lon, 3) AS lon, round(depth_km, 1) AS depth, round(mag, 1) AS mag, mag_type,
               place, event_type AS type, sources, preferred_source AS pref, mag_emsc, mag_usgs, emsc_id, usgs_id,
               nearest_city AS city, country_code AS cc, nearest_city_km AS city_km,
               dist_home_km AS home_km
        FROM read_parquet('{glob}')
        WHERE event_time > now()::TIMESTAMP - INTERVAL 30 DAY
        ORDER BY event_time DESC""")
    cols = [d[0] for d in cur.description]
    events = [dict(zip(cols, row)) for row in cur.fetchall()]
    _write("events.json", events)

    cur = con.execute(f"""
        SELECT strftime(date_trunc('day', event_time), '%Y-%m-%d') AS day, count(*) AS n,
               round(max(mag), 1) AS max_mag,
               count(*) FILTER (WHERE sources = 'emsc+usgs') AS matched
        FROM read_parquet('{glob}')
        WHERE event_time > now()::TIMESTAMP - INTERVAL 180 DAY AND event_type = 'earthquake'
        GROUP BY 1 ORDER BY 1""")
    cols = [d[0] for d in cur.description]
    _write("daily.json", [dict(zip(cols, r)) for r in cur.fetchall()])
    return len(events)


def _dir_stats(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()] if path.exists() else []
    return len(files), sum(p.stat().st_size for p in files)


def export_health(state: dict) -> dict:
    runs = storage.read_runs()
    now = storage.utcnow()
    last_ok_by_source: dict[str, str] = {}
    for run in runs:
        for name, t in run.get("tasks", {}).items():
            if name.startswith("extract_") and t.get("status") == "success":
                last_ok_by_source[name.removeprefix("extract_")] = run["finished_at"]
    sources = {}
    for s in ("usgs", "emsc", "geonames"):
        last = last_ok_by_source.get(s)
        limit = timedelta(days=31) if s == "geonames" else timedelta(hours=config.STALE_HOURS)
        stale = last is None or now - datetime.fromisoformat(last) > limit
        sources[s] = {"last_success": last, "stale": stale, "watermark": state["watermarks"].get(s)}

    raw_files, raw_bytes = _dir_stats(config.RAW_DIR)
    final_files, final_bytes = _dir_stats(config.FINAL_DIR)
    total_events = 0
    if (config.FINAL_DIR / "events").exists() and any((config.FINAL_DIR / "events").glob("*.parquet")):
        total_events = connect().execute(
            f"SELECT count(*) FROM read_parquet('{(config.FINAL_DIR / 'events' / '*.parquet').as_posix()}')"
        ).fetchone()[0]
    two_weeks = [r for r in runs if now - datetime.fromisoformat(r["started_at"]) < timedelta(days=14)]
    last = runs[-1] if runs else None
    last_success = next((r for r in reversed(runs) if r["status"] in ("success", "partial")), None)
    health = {
        "generated_at": now.isoformat(),
        "home": {"name": config.HOME_NAME, "lat": config.HOME_LAT, "lon": config.HOME_LON},
        "region": config.REGION.__dict__,
        "last_run": last,
        "last_success_at": last_success["finished_at"] if last_success else None,
        "sources": sources,
        "storage": {"raw_files": raw_files, "raw_bytes": raw_bytes,
                    "final_files": final_files, "final_bytes": final_bytes,
                    "events_total": total_events},
        "last_14_days": {
            "runs": len(two_weeks),
            "failed": sum(r["status"] == "failed" for r in two_weeks),
            "partial": sum(r["status"] == "partial" for r in two_weeks),
            "avg_duration_s": round(sum(r["duration_s"] for r in two_weeks) / len(two_weeks), 1) if two_weeks else None,
            "rows_loaded": sum(sum(r.get("rows_raw", {}).values()) for r in two_weeks),
        },
    }
    _write("health.json", health)
    compact = [{k: r.get(k) for k in ("run_id", "mode", "started_at", "duration_s", "status",
                                       "rows_raw", "bytes_raw", "final_rows", "error", "checks_failed")}
               for r in runs[-300:]]
    _write("runs.json", compact)
    return health
