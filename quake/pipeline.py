"""The pipeline DAG and one run of it.

extract_usgs ─┐
extract_emsc ─┼─> transform ─> quality_checks ─> publish ─┬─> export_events
extract_geonames ─> stage_cities ─┘                        └─> export_alerts
                         (export_health + run log: always, even after a failure)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from . import alerts, checks, config, export, sources, storage, transform
from .dag import Task, run_dag

log = logging.getLogger("quake.pipeline")


class QualityGateFailed(RuntimeError):
    pass


def _chunks(start: datetime, end: datetime, days: int = 30):
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        yield cur, nxt
        cur = nxt


def _extract_events(source: str):
    fn = {"usgs": sources.extract_usgs, "emsc": sources.extract_emsc}[source]

    def task(ctx: dict):
        if ctx.get("break_source") == source:  # demo switch: simulate the agency being down
            raise sources.SourceError(f"{source} unavailable (simulated outage)")
        state = ctx["state"]
        if ctx["mode"] == "backfill":
            rows = size = 0
            for a, b in _chunks(ctx["start"], ctx["end"]):
                r = fn(ctx["run_id"], state, a, b)
                rows, size = rows + r.rows, size + r.bytes
            return {"rows": rows, "bytes": size}
        r = fn(ctx["run_id"], state)
        # the raw file is safely stored -> the extract watermark may advance
        if r.max_updated:
            state["watermarks"][source] = max(r.max_updated, state["watermarks"].get(source, ""))
        elif source not in state["watermarks"]:
            state["watermarks"][source] = storage.utcnow().isoformat()
        return {"rows": r.rows, "bytes": r.bytes}
    return task


def _extract_geonames(ctx: dict):
    r = sources.extract_geonames(ctx["run_id"], ctx["state"], force=ctx.get("refresh_cities", False))
    ctx["geonames_path"] = r.path
    if r.path is not None:
        ctx["state"]["watermarks"]["geonames"] = r.max_updated
    return {"rows": 0, "bytes": r.bytes, "downloaded": r.path is not None}


def _stage_cities(ctx: dict):
    n = transform.stage_cities(ctx.get("geonames_path"))
    return {"cities": n}


def _transform(ctx: dict):
    tr = ctx["tasks_so_far"]()
    if all(tr[f"extract_{s}"].status == "failed" for s in transform.EVENT_SOURCES):
        raise RuntimeError("all event sources failed - nothing to transform")
    stats = transform.build_candidate(ctx["state"], inject_bad=ctx.get("inject_bad", False))
    ctx["transform_stats"] = stats
    return stats


def _quality_checks(ctx: dict):
    results = checks.run_checks()
    ctx["checks"] = checks.as_dicts(results)
    failed = [r.name for r in results if not r.passed]
    if failed:
        transform.discard_candidate()
        raise QualityGateFailed(f"blocked by checks: {', '.join(failed)}")
    return {"checks": len(results)}


def _publish(ctx: dict):
    moved = transform.publish(ctx["state"], ctx["transform_stats"])
    storage.save_state(ctx["state"])
    return {"files_promoted": moved}


def _export_events(ctx: dict):
    return {"events_exported": export.export_events()}


def _export_alerts(ctx: dict):
    return {"alerts": alerts.export_quake_alerts()}


def build_tasks() -> list[Task]:
    return [
        Task("extract_usgs", _extract_events("usgs"), optional=True),
        Task("extract_emsc", _extract_events("emsc"), optional=True),
        Task("extract_geonames", _extract_geonames, optional=True),
        Task("stage_cities", _stage_cities, deps=["extract_geonames"]),
        Task("transform", _transform, deps=["extract_usgs", "extract_emsc", "stage_cities"]),
        Task("quality_checks", _quality_checks, deps=["transform"]),
        Task("publish", _publish, deps=["quality_checks"]),
        Task("export_events", _export_events, deps=["publish"]),
        Task("export_alerts", _export_alerts, deps=["publish"]),
    ]


def run(mode: str = "manual", start: datetime | None = None, end: datetime | None = None,
        inject_bad: bool = False, break_source: str | None = None,
        refresh_cities: bool = False) -> dict:
    run_id = f"{storage.utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = storage.utcnow()
    state = storage.load_state()
    tasks = build_tasks()
    task_runs: dict = {}
    ctx = {"run_id": run_id, "mode": mode, "state": state, "start": start, "end": end,
           "inject_bad": inject_bad, "break_source": break_source,
           "refresh_cities": refresh_cities, "tasks_so_far": lambda: task_runs}
    log.info("run %s mode=%s", run_id, mode)

    run_dag(tasks, ctx, runs=task_runs)
    by_name = {t.name: t for t in tasks}

    required_bad = [n for n, r in task_runs.items() if r.status != "success" and not by_name[n].optional]
    optional_bad = [n for n, r in task_runs.items() if r.status == "failed" and by_name[n].optional]
    status = "failed" if required_bad else ("partial" if optional_bad else "success")
    if status == "failed":
        transform.discard_candidate()
    storage.save_state(state)  # watermarks of successful extracts persist even if the run failed

    finished = storage.utcnow()
    extract_res = {n.removeprefix("extract_"): (r.result or {}) for n, r in task_runs.items()
                   if n.startswith("extract_")}
    record = {
        "run_id": run_id, "mode": mode,
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 2),
        "status": status,
        "rows_raw": {s: max(v.get("rows", 0), 0) for s, v in extract_res.items()},
        "bytes_raw": sum(v.get("bytes", 0) for v in extract_res.values()),
        "final_rows": (ctx.get("transform_stats") or {}).get("final_rows_in_touched_months", 0),
        "months": (ctx.get("transform_stats") or {}).get("months", []),
        "checks": ctx.get("checks", []),
        "checks_failed": [c["name"] for c in ctx.get("checks", []) if not c["passed"]],
        "tasks": {n: {"status": r.status, "duration_s": r.duration_s, "error": r.error}
                  for n, r in task_runs.items()},
        "error": "; ".join(f"{n}: {r.error}" for n, r in task_runs.items() if r.error) or None,
    }
    storage.append_run(record)
    _operator_alert(state, record)
    storage.save_state(state)
    export.export_health(state)
    log.info("run %s finished: %s in %.1fs", run_id, status, record["duration_s"])
    return record


def _operator_alert(state: dict, record: dict) -> None:
    """GitHub issue on state changes only: ok->broken opens it, broken->ok closes it."""
    prev = state.get("last_status", "success")
    cur = record["status"]
    if cur != "success" and prev == "success":
        alerts.operator_alert(record, recovered=False)
    elif cur == "success" and prev != "success":
        alerts.operator_alert(record, recovered=True)
    state["last_status"] = cur
