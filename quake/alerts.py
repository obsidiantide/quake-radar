"""Alerts.

* For the user: `serve/alerts.json` - earthquakes that matter (M3.5+ within 300 km of home,
  or M5+ anywhere in the region). The website shows them on the "Тревоги" page and as a banner.
* For the operator: a GitHub issue labelled `pipeline-alert` is opened when the pipeline breaks
  and closed (with a comment) when it recovers. Works inside GitHub Actions via GITHUB_TOKEN;
  locally (no token) the alert is only logged.
"""
from __future__ import annotations

import json
import logging
import os

import requests

from . import config
from .transform import connect

log = logging.getLogger("quake.alerts")
LABEL = "pipeline-alert"


# ------------------------------------------------------------------ user-facing alerts

def export_quake_alerts(days: int = 90) -> int:
    events_dir = config.FINAL_DIR / "events"
    target = config.SERVE_DIR / "alerts.json"
    config.SERVE_DIR.mkdir(parents=True, exist_ok=True)
    if not events_dir.exists() or not any(events_dir.glob("*.parquet")):
        target.write_text("[]", encoding="utf-8")
        return 0
    glob = (events_dir / "*.parquet").as_posix()
    cur = connect().execute(f"""
        SELECT event_id, strftime(event_time, '%Y-%m-%dT%H:%M:%SZ') AS t, round(lat, 3) AS lat,
               round(lon, 3) AS lon, round(depth_km, 1) AS depth, round(mag, 1) AS mag, mag_type,
               place, sources, mag_emsc, mag_usgs, emsc_id, usgs_id,
               nearest_city AS city, country_code AS cc, nearest_city_km AS city_km,
               dist_home_km AS home_km,
               CASE WHEN dist_home_km <= {config.ALERT_NEAR_KM} AND mag >= {config.ALERT_NEAR_MAG}
                    THEN 'near' ELSE 'strong' END AS reason
        FROM read_parquet('{glob}')
        WHERE event_type = 'earthquake'
          AND event_time > now()::TIMESTAMP - INTERVAL {days} DAY
          AND ((dist_home_km <= {config.ALERT_NEAR_KM} AND mag >= {config.ALERT_NEAR_MAG})
               OR mag >= {config.ALERT_REGION_MAG})
        ORDER BY event_time DESC""")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)
    return len(rows)


# ------------------------------------------------------------------ operator alerts (GitHub issues)

def _gh() -> tuple[str, dict] | None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    return (f"https://api.github.com/repos/{repo}",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})


def _open_issue(gh) -> dict | None:
    base, h = gh
    r = requests.get(f"{base}/issues", headers=h, params={"labels": LABEL, "state": "open"}, timeout=20)
    r.raise_for_status()
    return r.json()[0] if r.json() else None


def operator_alert(record: dict, recovered: bool) -> None:
    gh = _gh()
    run_url = os.environ.get("GITHUB_SERVER_URL", "") + "/" + os.environ.get("GITHUB_REPOSITORY", "") + \
        "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "")
    if gh is None:
        log.warning("OPERATOR ALERT (%s): %s", "recovered" if recovered else record["status"], record.get("error"))
        return
    base, h = gh
    try:
        issue = _open_issue(gh)
        if recovered:
            if issue:
                requests.post(f"{base}/issues/{issue['number']}/comments", headers=h, timeout=20,
                              json={"body": f"✅ Recovered in run `{record['run_id']}` ({run_url}). Rows: {record['rows_raw']}"})
                requests.patch(f"{base}/issues/{issue['number']}", headers=h, timeout=20,
                               json={"state": "closed", "state_reason": "completed"})
            return
        body = (f"Run `{record['run_id']}` finished with status **{record['status']}**.\n\n"
                f"- error: `{(record.get('error') or '')[:800]}`\n"
                f"- failed checks: {record.get('checks_failed') or '—'}\n"
                f"- rows: {record['rows_raw']}\n- log: {run_url}\n")
        if issue:
            requests.post(f"{base}/issues/{issue['number']}/comments", headers=h, timeout=20, json={"body": body})
        else:
            requests.post(f"{base}/issues", headers=h, timeout=20,
                          json={"title": f"Pipeline {record['status']}: {record['run_id']}", "body": body,
                                "labels": [LABEL]})
    except requests.RequestException as e:  # an alerting problem must not fail the pipeline
        log.warning("could not create GitHub alert: %s", e)
