"""Transform step: raw -> stage -> final, incrementally, into a *candidate* area.

Nothing here touches the published stage/ and final/ folders. Files are written to
_candidate/ with the same relative paths; publish() moves them in only after the
quality checks pass. A failed run therefore leaves the product exactly as it was.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from . import config, storage

SQL_DIR = Path(__file__).parent / "sql"
EVENT_SOURCES = ("usgs", "emsc")

STAGE_COLUMNS = """
    source VARCHAR, source_event_id VARCHAR, event_time TIMESTAMP, updated_at TIMESTAMP,
    lat DOUBLE, lon DOUBLE, depth_km DOUBLE, mag DOUBLE, mag_type VARCHAR, place VARCHAR,
    event_type VARCHAR, status VARCHAR, raw_file VARCHAR, loaded_at TIMESTAMP
"""

EMSC_TYPES = {"ke": "earthquake", "se": "earthquake", "fe": "earthquake",
              "qb": "quarry_blast", "sqb": "quarry_blast", "ex": "explosion"}


def candidate_dir() -> Path:
    return config.DATA_DIR / "_candidate"


def _rel(p: Path) -> str:
    return p.relative_to(config.DATA_DIR).as_posix()


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("""
        CREATE MACRO hav_km(lat1, lon1, lat2, lon2) AS
          2 * 6371 * asin(sqrt(
              pow(sin(radians(lat2 - lat1) / 2), 2) +
              cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)))
    """)
    return con


# ------------------------------------------------------------------ parsing (raw -> rows)

def _ts_ms(v: Any) -> datetime | None:
    return datetime.fromtimestamp(v / 1000, timezone.utc).replace(tzinfo=None) if v is not None else None


def _ts_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def parse_usgs(feature: dict, raw_file: str, loaded_at: datetime) -> dict:
    p, (lon, lat, depth) = feature["properties"], feature["geometry"]["coordinates"][:3]
    return {
        "source": "usgs", "source_event_id": feature["id"],
        "event_time": _ts_ms(p.get("time")), "updated_at": _ts_ms(p.get("updated")),
        "lat": lat, "lon": lon, "depth_km": depth, "mag": p.get("mag"),
        "mag_type": p.get("magType"), "place": p.get("place"),
        "event_type": (p.get("type") or "unknown").replace(" ", "_"),
        "status": p.get("status"), "raw_file": raw_file, "loaded_at": loaded_at,
    }


def parse_emsc(feature: dict, raw_file: str, loaded_at: datetime) -> dict:
    p = feature["properties"]
    return {
        "source": "emsc", "source_event_id": p.get("unid") or feature.get("id"),
        "event_time": _ts_iso(p.get("time")), "updated_at": _ts_iso(p.get("lastupdate")),
        "lat": p.get("lat"), "lon": p.get("lon"), "depth_km": p.get("depth"), "mag": p.get("mag"),
        "mag_type": p.get("magtype"), "place": (p.get("flynn_region") or "").title() or None,
        "event_type": EMSC_TYPES.get((p.get("evtype") or "").lower(), "other"),
        "status": "automatic" if (p.get("auth") or "").upper() == "EMSC" else "reported",
        "raw_file": raw_file, "loaded_at": loaded_at,
    }


PARSERS = {"usgs": parse_usgs, "emsc": parse_emsc}
REQUIRED = ("source_event_id", "event_time", "lat", "lon")


def parse_raw_file(source: str, path: Path) -> tuple[list[dict], list[dict]]:
    """Returns (good_rows, quarantined). Structural problems go to quarantine,
    value problems (e.g. mag=42) are left for the quality checks to catch."""
    meta_path = path.with_suffix("").with_suffix(".meta.json")
    loaded_at = (_ts_iso(json.loads(meta_path.read_text())["fetched_at"]) if meta_path.exists()
                 else datetime.now(timezone.utc).replace(tzinfo=None))
    doc = json.loads(storage.read_raw(path) or b"{}")
    good, bad = [], []
    for f in doc.get("features", []):
        try:
            row = PARSERS[source](f, _rel(path), loaded_at)
            missing = [k for k in REQUIRED if row.get(k) is None]
            if missing:
                raise ValueError(f"missing {missing}")
            good.append(row)
        except Exception as e:  # noqa: BLE001 - any malformed record is quarantined, not fatal
            bad.append({"source": source, "raw_file": _rel(path), "error": str(e),
                        "record": json.dumps(f)[:2000]})
    return good, bad


def new_raw_files(source: str, state: dict) -> list[Path]:
    """Files are named so that the path sorts chronologically -> a single pointer is enough."""
    pointer = state.get("transform_pointer", {}).get(source, "")
    files = sorted((config.RAW_DIR / source).glob("dt=*/*.json.gz"), key=_rel)
    return [p for p in files if _rel(p) > pointer]


# ------------------------------------------------------------------ cities (file source)

def stage_cities(zip_path: Path | None) -> int:
    """GeoNames TSV -> stage/cities.parquet, only cities near the region."""
    target = config.STAGE_DIR / "cities.parquet"
    if zip_path is None:
        if target.exists():
            return -1
        zips = sorted((config.RAW_DIR / "geonames").glob("dt=*/cities15000.zip"))
        if not zips:
            raise RuntimeError("no GeoNames file in raw layer")
        zip_path = zips[-1]
    r, pad = config.REGION, 3.0
    rows = []
    with zipfile.ZipFile(zip_path) as z:
        with z.open("cities15000.txt") as f:
            for rec in csv.reader(io.TextIOWrapper(f, "utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE):
                lat, lon = float(rec[4]), float(rec[5])
                if r.min_lat - pad <= lat <= r.max_lat + pad and r.min_lon - pad <= lon <= r.max_lon + pad:
                    rows.append((int(rec[0]), rec[2] or rec[1], lat, lon, rec[8], int(rec[14] or 0)))
    target.parent.mkdir(parents=True, exist_ok=True)
    con = connect()
    con.execute("CREATE TABLE c(geonameid BIGINT, name VARCHAR, lat DOUBLE, lon DOUBLE, "
                "country_code VARCHAR, population BIGINT)")
    con.executemany("INSERT INTO c VALUES (?,?,?,?,?,?)", rows)
    tmp = target.with_suffix(".tmp")
    con.execute(f"COPY c TO '{tmp.as_posix()}' (FORMAT parquet)")
    os.replace(tmp, target)
    return len(rows)


# ------------------------------------------------------------------ stage + final

def _published_or_candidate(rel: str) -> Path | None:
    for base in (candidate_dir(), config.DATA_DIR):
        p = base / rel
        if p.exists():
            return p
    return None


def build_candidate(state: dict, inject_bad: bool = False) -> dict[str, Any]:
    """Incremental: only raw files after the pointer, only months they touch."""
    shutil.rmtree(candidate_dir(), ignore_errors=True)
    con = connect()
    con.execute(f"CREATE TABLE new_rows({STAGE_COLUMNS})")
    cols = [c.split()[0] for c in STAGE_COLUMNS.replace("\n", " ").split(",") if c.strip()]
    placeholders = ",".join("?" * len(cols))

    stats: dict[str, Any] = {"raw_files": {}, "new_rows": {}, "quarantined": 0, "pointers": {}}
    quarantine: list[dict] = []
    for source in EVENT_SOURCES:
        files = new_raw_files(source, state)
        stats["raw_files"][source] = len(files)
        n = 0
        for path in files:
            good, bad = parse_raw_file(source, path)
            quarantine += bad
            con.executemany(f"INSERT INTO new_rows VALUES ({placeholders})",
                            [tuple(r[c] for c in cols) for r in good])
            n += len(good)
        stats["new_rows"][source] = n
        if files:
            stats["pointers"][source] = _rel(files[-1])

    if inject_bad:  # demo switch: a record that must never reach the user
        con.execute("""INSERT INTO new_rows VALUES ('usgs', 'INJECTED-BAD', now()::TIMESTAMP,
                       now()::TIMESTAMP, 999, 999, -50, 42, 'xx', 'nowhere', 'earthquake',
                       'automatic', 'injected', now()::TIMESTAMP)""")

    stats["quarantined"] = len(quarantine)
    if quarantine:
        qp = config.META_DIR / "quarantine.jsonl"
        qp.parent.mkdir(parents=True, exist_ok=True)
        with qp.open("a", encoding="utf-8") as f:
            for q in quarantine:
                f.write(json.dumps(q) + "\n")

    months = [m for (m,) in con.execute(
        "SELECT DISTINCT strftime(event_time, '%Y-%m') FROM new_rows ORDER BY 1").fetchall()]
    stats["months"] = months

    # 1) stage: upsert new rows into the month partition of each source (latest revision wins)
    for source in EVENT_SOURCES:
        for m in months:
            rel = f"stage/{source}/month={m}.parquet"
            existing = _published_or_candidate(rel)
            con.execute(f"CREATE OR REPLACE TABLE part({STAGE_COLUMNS})")
            if existing:
                con.execute(f"INSERT INTO part SELECT * FROM read_parquet('{existing.as_posix()}')")
            con.execute("INSERT INTO part SELECT * FROM new_rows "
                        "WHERE source = ? AND strftime(event_time, '%Y-%m') = ?", [source, m])
            if con.execute("SELECT count(*) FROM part").fetchone()[0] == 0:
                continue
            out = candidate_dir() / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"""
                COPY (SELECT * FROM part
                      QUALIFY row_number() OVER (PARTITION BY source_event_id
                                                 ORDER BY updated_at DESC NULLS LAST, loaded_at DESC) = 1
                      ORDER BY event_time)
                TO '{out.as_posix()}' (FORMAT parquet)""")

    # 2) final: recompute the unified table for each touched month from both stage partitions
    cities = config.STAGE_DIR / "cities.parquet"
    if cities.exists():
        con.execute(f"CREATE OR REPLACE VIEW cities AS SELECT * FROM read_parquet('{cities.as_posix()}')")
    else:
        con.execute("CREATE OR REPLACE TABLE cities(name VARCHAR, lat DOUBLE, lon DOUBLE, country_code VARCHAR)")
    sql = (SQL_DIR / "unify_events.sql").read_text().format(
        MATCH_SECONDS=config.MATCH_SECONDS, MATCH_KM=config.MATCH_KM,
        HOME_LAT=config.HOME_LAT, HOME_LON=config.HOME_LON)
    final_rows = 0
    for m in months:
        for source in EVENT_SOURCES:
            p = _published_or_candidate(f"stage/{source}/month={m}.parquet")
            if p:
                con.execute(f"CREATE OR REPLACE VIEW stg_{source} AS SELECT * FROM read_parquet('{p.as_posix()}')")
            else:
                con.execute(f"CREATE OR REPLACE VIEW stg_{source} AS SELECT * FROM part WHERE false")
        out = candidate_dir() / f"final/events/month={m}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY ({sql}) TO '{out.as_posix()}' (FORMAT parquet)")
        final_rows += con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    stats["final_rows_in_touched_months"] = final_rows
    return stats


def publish(state: dict, stats: dict) -> int:
    """Move candidate files over the published ones (os.replace is atomic per file),
    then advance the transform pointers. Returns number of files promoted."""
    cand = candidate_dir()
    moved = 0
    if cand.exists():
        for f in sorted(cand.rglob("*.parquet")):
            target = config.DATA_DIR / f.relative_to(cand)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(f, target)
            moved += 1
        shutil.rmtree(cand, ignore_errors=True)
    state.setdefault("transform_pointer", {}).update(stats.get("pointers", {}))
    return moved


def discard_candidate() -> None:
    shutil.rmtree(candidate_dir(), ignore_errors=True)
