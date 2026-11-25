"""Raw layer, state (watermarks) and run log. Plain files with a predictable layout:

raw/<source>/dt=YYYY-MM-DD/<HHMMSS>_<run_id>.json.gz   one file per fetch, never modified
meta/state.json                                        watermarks + bookkeeping
meta/runs.jsonl                                        one line per pipeline run
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_raw(source: str, payload: bytes, run_id: str, params: dict[str, Any]) -> Path:
    """Store the untouched response body plus a sidecar with request params and checksum."""
    now = utcnow()
    folder = config.RAW_DIR / source / f"dt={now:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now:%H%M%S}_{run_id}.json.gz"
    with gzip.open(path, "wb") as f:
        f.write(payload)
    meta = {
        "source": source,
        "run_id": run_id,
        "fetched_at": now.isoformat(),
        "params": params,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    path.with_suffix("").with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    return path


def read_raw(path: Path) -> bytes:
    with gzip.open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- state

def _state_path() -> Path:
    return config.META_DIR / "state.json"


def load_state() -> dict[str, Any]:
    p = _state_path()
    if p.exists():
        return json.loads(p.read_text())
    return {"watermarks": {}, "processed_raw": [], "alerted": []}


def save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, p)  # atomic: a crash never leaves a half-written state


# ---------------------------------------------------------------- run log

def append_run(record: dict[str, Any]) -> None:
    p = config.META_DIR / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_runs(limit: int | None = None) -> list[dict[str, Any]]:
    p = config.META_DIR / "runs.jsonl"
    if not p.exists():
        return []
    lines = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-limit:] if limit else lines
