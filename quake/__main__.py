"""CLI:  python -m quake <command>

run        one incremental run (what the scheduler calls)
backfill   reload a past period:  backfill --start 2026-09-01 --end 2026-10-01
schedule   local scheduler loop:  schedule --every 30
stream     EMSC WebSocket listener (real-time raw ingestion)
status     print health: last run, freshness, volumes
rebuild    drop stage/final and rebuild them from the raw layer (e.g. after a schema change)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone

from . import config, export, pipeline, storage


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="quake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", default="manual", choices=["manual", "scheduled"])
    r.add_argument("--inject-bad", action="store_true", help="demo: add an invalid record")
    r.add_argument("--break-source", choices=["usgs", "emsc"], help="demo: simulate outage")
    r.add_argument("--refresh-cities", action="store_true")
    b = sub.add_parser("backfill")
    b.add_argument("--start", required=True, type=_date)
    b.add_argument("--end", required=True, type=_date)
    s = sub.add_parser("schedule")
    s.add_argument("--every", type=int, default=30, help="minutes")
    st = sub.add_parser("stream")
    st.add_argument("--seconds", type=int, default=None)
    sub.add_parser("status")
    sub.add_parser("rebuild")
    sub.add_parser("test-alert", help="send a test message to check Telegram settings")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args.port)
    if args.cmd == "test-alert":
        from . import alerts
        if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")
            return 1
        ok = alerts.send("✅ quake-radar: тестовое сообщение, бот настроен правильно")
        print("sent" if ok else "telegram rejected the message (see warning above)")
        return 0 if ok else 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")

    if args.cmd == "run":
        rec = pipeline.run(args.mode, inject_bad=args.inject_bad, break_source=args.break_source,
                           refresh_cities=args.refresh_cities)
        _print_run(rec)
        return 0 if rec["status"] != "failed" else 1
    if args.cmd == "backfill":
        rec = pipeline.run("backfill", start=args.start, end=args.end)
        _print_run(rec)
        return 0 if rec["status"] != "failed" else 1
    if args.cmd == "schedule":
        while True:
            pipeline.run("scheduled")
            time.sleep(args.every * 60)
    if args.cmd == "stream":
        asyncio.run(__import__("quake.stream", fromlist=["listen"]).listen(max_seconds=args.seconds))
        return 0
    if args.cmd == "status":
        h = export.export_health(storage.load_state())
        print(json.dumps({k: h[k] for k in ("last_success_at", "sources", "storage", "last_14_days")},
                         indent=2, ensure_ascii=False))
        lr = h["last_run"] or {}
        print(f"last run: {lr.get('run_id')} {lr.get('status')} {lr.get('duration_s')}s")
        return 0
    if args.cmd == "rebuild":
        for d in (config.STAGE_DIR / "usgs", config.STAGE_DIR / "emsc", config.FINAL_DIR):
            shutil.rmtree(d, ignore_errors=True)
        st_ = storage.load_state()
        st_["transform_pointer"] = {}
        storage.save_state(st_)
        rec = pipeline.run("manual")
        _print_run(rec)
        return 0 if rec["status"] != "failed" else 1
    return 2


def _serve(port: int) -> int:
    """Local dashboard: web/ at /, the serve layer at /data/."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "web"

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            clean = path.split("?", 1)[0]
            if clean.startswith("/data/"):
                return str(config.SERVE_DIR / Path(clean[len("/data/"):]).name)
            return super().translate_path(path)

    print(f"dashboard: http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), partial(Handler, directory=str(web))).serve_forever()
    return 0


def _print_run(rec: dict) -> None:
    print(f"\nrun {rec['run_id']}  status={rec['status'].upper()}  {rec['duration_s']}s")
    print(f"raw rows: {rec['rows_raw']}  bytes: {rec['bytes_raw']}  final rows (touched months): {rec['final_rows']}")
    for name, t in rec["tasks"].items():
        print(f"  {t['status']:8} {name:18} {t['duration_s']:>6}s  {t['error'] or ''}")
    for c in rec["checks"]:
        print(f"  check {'PASS' if c['passed'] else 'FAIL'}  {c['name']:22} rows={c['failing_rows']}  {c['detail']}")


if __name__ == "__main__":
    sys.exit(main())
