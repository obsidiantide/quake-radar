"""Telegram alerts: (a) noticeable earthquakes for the user, (b) pipeline problems for the operator.
Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID the messages are only logged."""
from __future__ import annotations

import logging

import requests

from . import config
from .transform import connect

log = logging.getLogger("quake.alerts")


def send(text: str) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        log.info("ALERT (telegram not configured): %s", text)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    if not r.ok:
        log.warning("telegram error %s: %s", r.status_code, r.text[:200])
    return r.ok


def quake_alerts(state: dict) -> int:
    """Events of the last 24 h that matter to the user and were not alerted yet."""
    glob = (config.FINAL_DIR / "events" / "*.parquet").as_posix()
    if not any((config.FINAL_DIR / "events").glob("*.parquet")):
        return 0
    con = connect()
    rows = con.execute(f"""
        SELECT event_id, emsc_id, usgs_id, event_time, mag, mag_type, depth_km, place,
               nearest_city, nearest_city_km, dist_home_km, sources
        FROM read_parquet('{glob}')
        WHERE event_type = 'earthquake'
          AND event_time > now()::TIMESTAMP - INTERVAL 24 HOUR
          AND ((dist_home_km <= {config.ALERT_NEAR_KM} AND mag >= {config.ALERT_NEAR_MAG})
               OR mag >= {config.ALERT_REGION_MAG})
        ORDER BY event_time""").fetchall()
    alerted = set(state.setdefault("alerted", []))
    sent = 0
    for (eid, emsc_id, usgs_id, t, mag, mtype, depth, place, city, city_km, home_km, sources) in rows:
        keys = {k for k in (eid, f"emsc:{emsc_id}" if emsc_id else None,
                            f"usgs:{usgs_id}" if usgs_id else None) if k}
        if keys & alerted:  # same quake may change event_id when the 2nd agency reports it
            continue
        text = (f"🌍 <b>M{mag:.1f}</b> {mtype or ''} — {place or ''}\n"
                f"{t:%Y-%m-%d %H:%M} UTC, глубина {depth or 0:.0f} км\n"
                f"{city_km:.0f} км от {city}, <b>{home_km:.0f} км от {config.HOME_NAME}</b>\n"
                f"источники: {sources}")
        if not send(text):
            continue  # not delivered (or telegram not configured): retry on the next run
        alerted |= keys
        sent += 1
    state["alerted"] = sorted(alerted)[-2000:]
    return sent
