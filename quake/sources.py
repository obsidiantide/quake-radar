"""Extract step: three source types.

* USGS  - REST API, GeoJSON (FDSN event service), incremental by `updatedafter`
* EMSC  - REST API (FDSN) for scheduled runs + WebSocket stream for real-time (see stream.py)
* GeoNames - static bulk file (zip/TSV) with cities, refreshed monthly

Each extractor stores the raw response first and only then reports what it got.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import config, storage

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
EMSC_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"
GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"


class SourceError(RuntimeError):
    pass


@dataclass
class ExtractResult:
    source: str
    path: Path | None
    rows: int
    bytes: int
    max_updated: str | None


def _get(url: str, params: dict | None = None, retries: int = 3) -> requests.Response:
    """GET with exponential backoff. 204 (no content) is a valid empty answer for FDSN."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT,
                             headers={"User-Agent": config.USER_AGENT})
            if r.status_code in (200, 204):
                return r
            last = SourceError(f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            last = e
        time.sleep(2 ** attempt)
    raise SourceError(f"source unavailable after {retries} attempts: {last}")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def window(source: str, state: dict, start: datetime | None, end: datetime | None) -> dict:
    """Decide what to ask for. Normal run = incremental from watermark; backfill = explicit window."""
    if start is not None:
        return {"starttime": _iso(start), "endtime": _iso(end or storage.utcnow())}
    wm = state["watermarks"].get(source)
    if wm:
        since = datetime.fromisoformat(wm) - timedelta(hours=config.OVERLAP_HOURS)
    else:
        since = storage.utcnow() - timedelta(days=config.INITIAL_LOOKBACK_DAYS)
    # updatedafter catches revisions of older events; starttime bounds the scan.
    return {
        "updatedafter": _iso(since),
        "starttime": _iso(storage.utcnow() - timedelta(days=config.INITIAL_LOOKBACK_DAYS)),
    }


def extract_usgs(run_id: str, state: dict, start=None, end=None) -> ExtractResult:
    r = config.REGION
    params = {
        "format": "geojson", "orderby": "time-asc",
        "minlatitude": r.min_lat, "maxlatitude": r.max_lat,
        "minlongitude": r.min_lon, "maxlongitude": r.max_lon,
        "minmagnitude": r.min_mag, "limit": 20000,
        **window("usgs", state, start, end),
    }
    resp = _get(USGS_URL, params)
    body = resp.content if resp.status_code == 200 else b'{"type":"FeatureCollection","features":[]}'
    path = storage.write_raw("usgs", body, run_id, params)
    feats = json.loads(body).get("features", [])
    upd = [f["properties"].get("updated") for f in feats if f["properties"].get("updated")]
    max_upd = (datetime.fromtimestamp(max(upd) / 1000, timezone.utc).isoformat() if upd else None)
    return ExtractResult("usgs", path, len(feats), len(body), max_upd)


def extract_emsc(run_id: str, state: dict, start=None, end=None) -> ExtractResult:
    r = config.REGION
    params = {
        "format": "json", "orderby": "time-asc",
        "minlat": r.min_lat, "maxlat": r.max_lat,
        "minlon": r.min_lon, "maxlon": r.max_lon,
        "minmag": r.min_mag, "limit": 20000,
        **window("emsc", state, start, end),
    }
    resp = _get(EMSC_URL, params)
    body = resp.content if resp.status_code == 200 else b'{"type":"FeatureCollection","features":[]}'
    path = storage.write_raw("emsc", body, run_id, params)
    feats = json.loads(body).get("features", [])
    upd = [f["properties"].get("lastupdate") for f in feats if f["properties"].get("lastupdate")]
    max_upd = (datetime.fromisoformat(max(upd).replace("Z", "+00:00")).isoformat() if upd else None)
    return ExtractResult("emsc", path, len(feats), len(body), max_upd)


def extract_geonames(run_id: str, state: dict, force: bool = False) -> ExtractResult:
    """Bulk file: download at most once per 30 days (it changes slowly, and it is 2+ MB)."""
    last = state["watermarks"].get("geonames")
    if last and not force:
        age = storage.utcnow() - datetime.fromisoformat(last)
        if age < timedelta(days=30):
            return ExtractResult("geonames", None, 0, 0, last)
    resp = _get(GEONAMES_URL)
    folder = config.RAW_DIR / "geonames" / f"dt={storage.utcnow():%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "cities15000.zip"
    path.write_bytes(resp.content)
    return ExtractResult("geonames", path, -1, len(resp.content), storage.utcnow().isoformat())
