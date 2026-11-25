"""Tests of the transformation logic on small hand-made raw files (no network)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import duckdb
import pytest

from quake import checks, config, dag, storage, transform

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for name, sub in [("RAW_DIR", "raw"), ("STAGE_DIR", "stage"), ("FINAL_DIR", "final"),
                      ("SERVE_DIR", "serve"), ("META_DIR", "meta")]:
        monkeypatch.setattr(config, name, tmp_path / sub)
    # tiny cities table instead of the GeoNames download
    (tmp_path / "stage").mkdir(parents=True)
    con = duckdb.connect()
    con.execute("""COPY (SELECT * FROM (VALUES (1, 'Baku', 40.41, 49.87, 'AZ', 2000000),
                                              (2, 'Shamakhi', 40.63, 48.64, 'AZ', 30000))
                        t(geonameid, name, lat, lon, country_code, population))
                   TO '""" + (tmp_path / "stage" / "cities.parquet").as_posix() + "' (FORMAT parquet)")
    return tmp_path


def usgs_feature(eid, t, lat, lon, mag, status="reviewed", updated=None):
    ms = int(t.timestamp() * 1000)
    return {"type": "Feature", "id": eid,
            "properties": {"mag": mag, "place": "test", "time": ms,
                           "updated": int((updated or t).timestamp() * 1000), "magType": "mb",
                           "type": "earthquake", "status": status},
            "geometry": {"type": "Point", "coordinates": [lon, lat, 10]}}


def emsc_feature(eid, t, lat, lon, mag, updated=None):
    return {"type": "Feature", "id": eid,
            "properties": {"unid": eid, "time": t.isoformat().replace("+00:00", "Z"),
                           "lastupdate": (updated or t).isoformat().replace("+00:00", "Z"),
                           "lat": lat, "lon": lon, "depth": 8, "mag": mag, "magtype": "ml",
                           "flynn_region": "AZERBAIJAN", "evtype": "ke", "auth": "EMSC"}}


def put_raw(source, features, run="t"):
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    return storage.write_raw(source, body, run + str(len(list(config.RAW_DIR.rglob("*.gz")))), {})


def final_rows():
    return duckdb.sql(f"SELECT * FROM '{(config.FINAL_DIR / 'events' / '*.parquet').as_posix()}' "
                      "ORDER BY event_time").fetchall()


def run_transform(state, **kw):
    stats = transform.build_candidate(state, **kw)
    results = checks.run_checks()
    if all(r.passed for r in results):
        transform.publish(state, stats)
    else:
        transform.discard_candidate()
    return stats, results


def test_cross_source_match_and_unmatched(lake):
    t = NOW - timedelta(hours=2)
    put_raw("emsc", [emsc_feature("E1", t, 40.6, 48.6, 4.0),
                     emsc_feature("E2", t + timedelta(hours=1), 40.0, 45.0, 2.5)])
    # same quake 20 s later, ~10 km away -> must be matched with E1
    put_raw("usgs", [usgs_feature("U1", t + timedelta(seconds=20), 40.65, 48.7, 4.2)])
    state = storage.load_state()
    _, results = run_transform(state)
    assert all(r.passed for r in results)
    rows = duckdb.sql(f"SELECT event_id, sources, mag, mag_emsc, mag_usgs, nearest_city "
                      f"FROM '{(config.FINAL_DIR / 'events' / '*.parquet').as_posix()}' "
                      "ORDER BY event_time").fetchall()
    assert rows[0] == ("emsc:E1", "emsc+usgs", 4.2, 4.0, 4.2, "Shamakhi")  # reviewed USGS wins
    assert rows[1][0:2] == ("emsc:E2", "emsc")
    assert len(rows) == 2


def test_far_apart_events_are_not_matched(lake):
    t = NOW - timedelta(hours=2)
    put_raw("emsc", [emsc_feature("E1", t, 40.6, 48.6, 4.0)])
    put_raw("usgs", [usgs_feature("U1", t + timedelta(seconds=20), 38.0, 44.0, 4.2)])  # ~500 km
    run_transform(storage.load_state())
    assert {r[0] for r in final_rows()} == {"emsc:E1", "usgs:U1"}


def test_incremental_rerun_is_idempotent_and_takes_latest_revision(lake):
    t = NOW - timedelta(hours=3)
    put_raw("emsc", [emsc_feature("E1", t, 40.6, 48.6, 3.0)])
    state = storage.load_state()
    run_transform(state)
    # the agency revises the magnitude; the same event arrives again in the overlap window
    put_raw("emsc", [emsc_feature("E1", t, 40.6, 48.6, 3.4, updated=t + timedelta(minutes=30))])
    stats, _ = run_transform(state)
    assert stats["raw_files"]["emsc"] == 1  # only the new file was read
    rows = final_rows()
    assert len(rows) == 1 and rows[0][5] == 3.4
    stats, _ = run_transform(state)  # nothing new -> nothing changes
    assert stats["raw_files"]["emsc"] == 0 and len(final_rows()) == 1


def test_bad_data_is_blocked_and_product_unchanged(lake):
    t = NOW - timedelta(hours=3)
    put_raw("emsc", [emsc_feature("E1", t, 40.6, 48.6, 3.0)])
    state = storage.load_state()
    run_transform(state)
    before = final_rows()
    put_raw("emsc", [emsc_feature("E2", t, 40.7, 48.7, 3.1)])
    _, results = run_transform(state, inject_bad=True)
    assert [r.name for r in results if not r.passed] == ["value_ranges"]
    assert final_rows() == before                       # published data untouched
    assert state["transform_pointer"]["emsc"].endswith(".json.gz")
    _, results = run_transform(state)                   # next normal run picks E2 up
    assert all(r.passed for r in results) and len(final_rows()) == 2


def test_malformed_record_goes_to_quarantine(lake):
    t = NOW - timedelta(hours=1)
    broken = emsc_feature("E9", t, 40.6, 48.6, 3.0)
    del broken["properties"]["time"]
    put_raw("emsc", [broken, emsc_feature("E1", t, 40.6, 48.6, 3.0)])
    stats, _ = run_transform(storage.load_state())
    assert stats["quarantined"] == 1 and len(final_rows()) == 1


def test_dag_skips_downstream_of_failed_required_task_but_not_optional():
    calls = []
    tasks = [
        dag.Task("a", lambda c: calls.append("a")),
        dag.Task("src_down", lambda c: 1 / 0, optional=True),
        dag.Task("b", lambda c: calls.append("b"), deps=["a", "src_down"]),
        dag.Task("boom", lambda c: 1 / 0, deps=["b"]),
        dag.Task("after", lambda c: calls.append("after"), deps=["boom"]),
    ]
    runs = dag.run_dag(tasks, {})
    assert calls == ["a", "b"]
    assert runs["src_down"].status == "failed" and runs["b"].status == "success"
    assert runs["after"].status == "skipped"


def test_dag_detects_cycles():
    with pytest.raises(ValueError):
        dag.topo_order([dag.Task("x", print, deps=["y"]), dag.Task("y", print, deps=["x"])])
