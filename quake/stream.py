"""Real-time source: EMSC WebSocket ("standing order"). Pushes every new/updated event worldwide.
We keep only the region and flush micro-batches into the SAME raw layer as the REST loader
(raw/emsc/...), so the scheduled transform treats both paths identically and dedups them.

Runs as a long-lived process (docker compose service `stream`). In the cloud, where no
long-lived process is available, the scheduled REST poll of EMSC is the fallback."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import websockets

from . import config, storage

WS_URL = "wss://www.seismicportal.eu/standing_order/websocket"
log = logging.getLogger("quake.stream")


def in_region(props: dict) -> bool:
    r = config.REGION
    try:
        return (r.min_lat <= float(props["lat"]) <= r.max_lat and
                r.min_lon <= float(props["lon"]) <= r.max_lon and
                float(props.get("mag") or 0) >= r.min_mag)
    except (KeyError, TypeError, ValueError):
        return False


async def listen(flush_seconds: int = 60, max_seconds: int | None = None) -> None:
    buffer: list[dict] = []
    loop = asyncio.get_running_loop()
    started = loop.time()
    backoff = 1

    def flush() -> None:
        if buffer:
            body = json.dumps({"type": "FeatureCollection", "features": buffer}).encode()
            path = storage.write_raw("emsc", body, f"stream-{uuid.uuid4().hex[:6]}", {"mode": "stream"})
            log.info("flushed %d events -> %s", len(buffer), path.name)
            buffer.clear()

    while max_seconds is None or loop.time() - started < max_seconds:
        try:
            async with websockets.connect(WS_URL, ping_interval=15) as ws:
                log.info("connected to %s", WS_URL)
                backoff = 1
                last_flush = loop.time()
                while max_seconds is None or loop.time() - started < max_seconds:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=flush_seconds)
                        data = json.loads(msg).get("data", {})
                        if in_region(data.get("properties", {})):
                            buffer.append(data)
                    except asyncio.TimeoutError:
                        pass
                    if loop.time() - last_flush >= flush_seconds:
                        flush()
                        last_flush = loop.time()
        except (OSError, websockets.WebSocketException) as e:
            log.warning("stream disconnected (%s), reconnect in %ss", e, backoff)
            flush()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)
    flush()
