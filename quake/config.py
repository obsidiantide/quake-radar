"""Central configuration. Everything tunable lives here or in environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Root of the data lake. Locally: ./data, in GitHub Actions: checkout of the `data` branch.
DATA_DIR = Path(os.environ.get("QUAKE_DATA_DIR", "data")).resolve()

RAW_DIR = DATA_DIR / "raw"          # immutable, exactly what the source returned (gzip)
STAGE_DIR = DATA_DIR / "stage"      # typed, deduplicated per source (parquet, by month)
FINAL_DIR = DATA_DIR / "final"      # unified events used by the product (parquet, by month)
SERVE_DIR = DATA_DIR / "serve"      # small JSON files read by the dashboard
META_DIR = DATA_DIR / "meta"        # state (watermarks), run log, quarantine

# Region of interest: Caucasus + Caspian (Azerbaijan, Georgia, Armenia, N. Iran, E. Turkey).
@dataclass(frozen=True)
class Region:
    min_lat: float = 36.0
    max_lat: float = 46.0
    min_lon: float = 40.0
    max_lon: float = 56.0
    min_mag: float = 2.0

REGION = Region()

# Home point for distances and alerts (Baku).
HOME_NAME = os.environ.get("QUAKE_HOME_NAME", "Baku")
HOME_LAT = float(os.environ.get("QUAKE_HOME_LAT", "40.4093"))
HOME_LON = float(os.environ.get("QUAKE_HOME_LON", "49.8671"))

# Incremental loading: re-read this many hours before the watermark, because
# agencies revise magnitude/location after the first publication.
OVERLAP_HOURS = 6
# First run with no watermark loads this many days.
INITIAL_LOOKBACK_DAYS = 30

# Cross-source matching: same physical event if within these tolerances.
MATCH_SECONDS = 60
MATCH_KM = 100.0

# Alerts
ALERT_NEAR_KM = 300.0
ALERT_NEAR_MAG = 3.5
ALERT_REGION_MAG = 5.0
STALE_HOURS = 3  # a source with no successful load for this long is "stale"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

USER_AGENT = "quake-radar/1.0 (student data-engineering project)"
HTTP_TIMEOUT = 60
