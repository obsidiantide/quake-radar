"""A tiny DAG runner: tasks with explicit dependencies, topological order,
per-task timing/status. Chosen over Airflow/Prefect deliberately: the whole
pipeline runs inside a 2-minute GitHub Actions job, and every line here can be explained.

Rules:
* a task runs only if all its dependencies succeeded, or are `optional` and failed
  (e.g. one earthquake agency is down -> continue with the other one = "partial" run);
* a failed required task marks all downstream tasks as `skipped`.
"""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("quake.dag")


@dataclass
class Task:
    name: str
    fn: Callable[[dict], Any]
    deps: list[str] = field(default_factory=list)
    optional: bool = False


@dataclass
class TaskRun:
    status: str = "pending"          # success | failed | skipped
    duration_s: float = 0.0
    result: Any = None
    error: str | None = None


def topo_order(tasks: list[Task]) -> list[Task]:
    by_name = {t.name: t for t in tasks}
    for t in tasks:
        for d in t.deps:
            if d not in by_name:
                raise ValueError(f"task {t.name} depends on unknown task {d}")
    order, seen, visiting = [], set(), set()

    def visit(t: Task) -> None:
        if t.name in seen:
            return
        if t.name in visiting:
            raise ValueError(f"cycle at {t.name}")
        visiting.add(t.name)
        for d in t.deps:
            visit(by_name[d])
        visiting.discard(t.name)
        seen.add(t.name)
        order.append(t)

    for t in tasks:
        visit(t)
    return order


def run_dag(tasks: list[Task], ctx: dict, runs: dict[str, TaskRun] | None = None) -> dict[str, TaskRun]:
    """`runs` is filled as tasks finish, so a task can look at its upstream results."""
    by_name = {t.name: t for t in tasks}
    runs = {} if runs is None else runs
    for task in topo_order(tasks):
        blocked = [d for d in task.deps
                   if runs[d].status != "success" and not (by_name[d].optional and runs[d].status == "failed")]
        if blocked:
            runs[task.name] = TaskRun(status="skipped", error=f"upstream not ok: {', '.join(blocked)}")
            log.info("SKIP  %-18s (%s)", task.name, runs[task.name].error)
            continue
        t0 = time.monotonic()
        try:
            result = task.fn(ctx)
            runs[task.name] = TaskRun("success", round(time.monotonic() - t0, 2), result)
            log.info("OK    %-18s %.2fs", task.name, runs[task.name].duration_s)
        except Exception as e:  # noqa: BLE001 - recorded, not swallowed
            runs[task.name] = TaskRun("failed", round(time.monotonic() - t0, 2), None,
                                      f"{type(e).__name__}: {e}")
            log.error("FAIL  %-18s %s", task.name, e)
            log.debug(traceback.format_exc())
    return runs
