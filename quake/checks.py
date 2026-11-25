"""Quality checks. Run on the CANDIDATE final partitions on every load.
Any failed check blocks publish(): bad data never reaches the dashboard or the alerts."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import config
from .transform import candidate_dir, connect


@dataclass
class CheckResult:
    name: str
    passed: bool
    failing_rows: int
    detail: str


def _candidate_glob() -> str | None:
    files = list((candidate_dir() / "final" / "events").glob("*.parquet"))
    return (candidate_dir() / "final" / "events" / "*.parquet").as_posix() if files else None


def run_checks() -> list[CheckResult]:
    glob = _candidate_glob()
    if glob is None:
        return [CheckResult("nothing_to_check", True, 0, "no new data in this run")]
    con = connect()
    con.execute(f"CREATE VIEW cand AS SELECT * FROM read_parquet('{glob}')")
    r, results = config.REGION, []

    def check(name: str, sql: str, detail: str) -> None:
        n = con.execute(sql).fetchone()[0]
        results.append(CheckResult(name, n == 0, n, detail))

    # 1. completeness of mandatory fields
    check("not_null_keys",
          "SELECT count(*) FROM cand WHERE event_id IS NULL OR event_time IS NULL "
          "OR lat IS NULL OR lon IS NULL OR mag IS NULL",
          "event_id, event_time, lat, lon, mag must be present")
    # 2. physically plausible values inside the region we asked for
    check("value_ranges",
          f"SELECT count(*) FROM cand WHERE lat NOT BETWEEN {r.min_lat - 0.5} AND {r.max_lat + 0.5} "
          f"OR lon NOT BETWEEN {r.min_lon - 0.5} AND {r.max_lon + 0.5} "
          "OR mag NOT BETWEEN -1 AND 10 OR coalesce(depth_km, 0) NOT BETWEEN -10 AND 800",
          "lat/lon inside region, magnitude -1..10, depth -10..800 km")
    # 3. one row per event (dedup/matching worked)
    check("unique_event_id",
          "SELECT count(*) - count(DISTINCT event_id) FROM cand",
          "event_id is unique in the final table")
    # 4. no events from the future (clock/timezone bugs)
    check("no_future_events",
          "SELECT count(*) FROM cand WHERE event_time > now()::TIMESTAMP + INTERVAL 10 MINUTE",
          "event_time is not later than now + 10 min")
    # 5. volume anomaly: a day with far more events than usual means a broken filter or a double load
    hist = (config.FINAL_DIR / "events" / "*.parquet").as_posix()
    has_hist = any((config.FINAL_DIR / "events").glob("*.parquet")) if (config.FINAL_DIR / "events").exists() else False
    # min 200/day: a real M6+ aftershock sequence can legitimately give ~100+ events per day
    baseline = 200
    if has_hist:
        med = con.execute(f"""
            SELECT median(n) FROM (SELECT date_trunc('day', event_time) d, count(*) n
                                   FROM read_parquet('{hist}') GROUP BY 1)""").fetchone()[0]
        baseline = max(200, int((med or 0) * 10))
    check("daily_volume",
          f"SELECT count(*) FROM (SELECT date_trunc('day', event_time) d, count(*) n FROM cand "
          f"GROUP BY 1 HAVING n > {baseline})",
          f"no day with more than {baseline} events (10x median, min 200)")
    # 6. enrichment from the file source succeeded
    check("enrichment_complete",
          "SELECT count(*) FROM cand WHERE nearest_city IS NULL OR dist_home_km IS NULL",
          "every event has nearest city and distance to home")
    return results


def as_dicts(results: list[CheckResult]) -> list[dict]:
    return [asdict(r) for r in results]
