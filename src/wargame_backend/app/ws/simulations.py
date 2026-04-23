"""WebSocket handler for live simulation streaming.

Endpoint: ``ws://host/ws/simulations/{sim_id}``

Protocol (matches architecture.md Section 4):
  1. On connect: validate sim exists, send ``connected`` frame.
  2. Subscribe to Redis PubSub channel ``sim:{sim_id}:events``.
  3. Forward every published JSON message as a ``WsFrame`` to the client.
  4. Accept ``control`` frames from the client; forward to Redis channel
     ``sim:{sim_id}:control``.
  5. Send ``heartbeat`` every 15 seconds of idle.
  6. On ``sim_complete`` frame: close the connection cleanly.
  7. On DB fetch failure: send ``error`` frame and close.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wargame_backend.app.db.models import Scenario, Simulation
from wargame_backend.app.db.session import AsyncSessionLocal
from wargame_backend.app.sim_runner import control_channel, events_channel

log = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL: float = 15.0  # seconds

# Sentinel value used to signal the pubsub reader that Redis closed
_PUBSUB_CLOSED = object()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_simulation(db: AsyncSession, sim_id: uuid.UUID) -> Simulation | None:
    result = await db.execute(select(Simulation).where(Simulation.id == sim_id))
    return result.scalar_one_or_none()


async def _send_json(ws: WebSocket, data: dict) -> None:  # type: ignore[type-arg]
    """Send a JSON-serializable dict as a text frame; swallow send errors."""
    try:
        await ws.send_text(json.dumps(data))
    except Exception as exc:
        log.warning("WS send failed", error=str(exc))


async def _send_error(
    ws: WebSocket,
    sim_id: uuid.UUID,
    seq: int,
    code: str,
    message: str,
    recoverable: bool = False,
) -> None:
    await _send_json(
        ws,
        {
            "frame_type": "error",
            "sim_id": str(sim_id),
            "seq": seq,
            "ts": _now_iso(),
            "payload": {
                "frame_type": "error",
                "code": code,
                "message": message,
                "recoverable": recoverable,
                "turn": None,
            },
        },
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/simulations/{sim_id}")
async def ws_simulation(ws: WebSocket, sim_id: uuid.UUID) -> None:
    """Stream simulation events to a connected WebSocket client."""
    await ws.accept()
    seq = 0

    # -- Fetch simulation from DB --
    try:
        async with AsyncSessionLocal() as db:
            simulation = await _fetch_simulation(db, sim_id)
    except Exception as exc:
        log.error("DB error on WS connect", sim_id=str(sim_id), error=str(exc))
        seq += 1
        await _send_error(ws, sim_id, seq, "DB_ERROR", "Failed to load simulation.", recoverable=False)
        await ws.close(code=1011)
        return

    if simulation is None:
        log.info("WS sim not found", sim_id=str(sim_id))
        seq += 1
        await _send_error(
            ws, sim_id, seq, "NOT_FOUND", "Simulation not found.", recoverable=False
        )
        await ws.close(code=1008)
        return

    # -- Fetch the scenario's country list for the connected payload --
    countries: list[str] = []
    try:
        async with AsyncSessionLocal() as db:
            sc_result = await db.execute(
                select(Scenario).where(Scenario.id == simulation.scenario_id)
            )
            sc = sc_result.scalar_one_or_none()
            if sc:
                countries = sc.country_ids
    except Exception:
        pass  # non-fatal; countries list is advisory

    # -- Send connected frame --
    seq += 1
    await _send_json(
        ws,
        {
            "frame_type": "connected",
            "sim_id": str(sim_id),
            "seq": seq,
            "ts": _now_iso(),
            "payload": {
                "frame_type": "connected",
                "sim_id": str(sim_id),
                "status": simulation.status.value,
                "current_turn": simulation.current_turn,
                "max_turns": simulation.max_turns,
                "countries": countries,
            },
        },
    )
    log.info("WS client connected", sim_id=str(sim_id))

    # -- Acquire Redis client from app.state --
    redis = ws.app.state.redis  # type: ignore[attr-defined]

    # -- Set up PubSub → asyncio.Queue pipeline --
    # We use a queue so the pubsub reader (background task) and the main loop
    # can be properly decoupled without recreating the listen() generator.
    message_queue: asyncio.Queue[dict | object] = asyncio.Queue()

    pubsub = redis.pubsub()
    await pubsub.subscribe(events_channel(sim_id))

    async def _pubsub_reader() -> None:
        """Background task: read Redis messages into the queue."""
        try:
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    raw = message.get("data", "")
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    try:
                        frame = json.loads(raw)
                        await message_queue.put(frame)
                    except json.JSONDecodeError:
                        log.warning("Bad JSON from Redis PubSub", raw=raw[:200])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("PubSub reader error", sim_id=str(sim_id), error=str(exc))
        finally:
            await message_queue.put(_PUBSUB_CLOSED)

    pubsub_task = asyncio.create_task(_pubsub_reader(), name=f"ws-pubsub-{sim_id}")

    # -- Heartbeat task --
    async def _heartbeat_loop() -> None:
        nonlocal seq
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            try:
                async with AsyncSessionLocal() as db:
                    sim = await _fetch_simulation(db, sim_id)
                status_val = sim.status.value if sim else "unknown"
                turn_val = sim.current_turn if sim else 0
            except Exception:
                status_val, turn_val = "unknown", 0
            seq += 1
            await _send_json(
                ws,
                {
                    "frame_type": "heartbeat",
                    "sim_id": str(sim_id),
                    "seq": seq,
                    "ts": _now_iso(),
                    "payload": {
                        "frame_type": "heartbeat",
                        "status": status_val,
                        "current_turn": turn_val,
                    },
                },
            )

    heartbeat_task: asyncio.Task[None] = asyncio.create_task(
        _heartbeat_loop(), name=f"ws-hb-{sim_id}"
    )

    # -- Main fan-out loop --
    try:
        while True:
            queue_get_task = asyncio.create_task(
                message_queue.get(), name=f"ws-queue-{sim_id}"
            )
            client_recv_task = asyncio.create_task(
                ws.receive_text(), name=f"ws-client-{sim_id}"
            )

            done, pending = await asyncio.wait(
                {queue_get_task, client_recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            # -- Handle Redis event from queue --
            if queue_get_task in done:
                item = queue_get_task.result()
                if item is _PUBSUB_CLOSED:
                    log.info("PubSub closed, ending WS loop", sim_id=str(sim_id))
                    break
                frame: dict = item  # type: ignore[assignment]
                seq += 1
                frame["seq"] = seq
                await _send_json(ws, frame)

                if frame.get("frame_type") == "sim_complete":
                    log.info("WS closing on sim_complete", sim_id=str(sim_id))
                    try:
                        await ws.close(code=1000)
                    except Exception:
                        pass
                    return

            # -- Handle client control frame --
            if client_recv_task in done:
                raw_text: str = client_recv_task.result()
                try:
                    ctrl = json.loads(raw_text)
                except json.JSONDecodeError:
                    log.warning("Invalid JSON from WS client", sim_id=str(sim_id))
                    continue

                if ctrl.get("frame_type") == "control":
                    action = ctrl.get("payload", {}).get("action", "")
                    log.info("WS control frame received", sim_id=str(sim_id), action=action)
                    await redis.publish(control_channel(sim_id), json.dumps(ctrl))
                else:
                    log.debug("Unknown client frame type", frame_type=ctrl.get("frame_type"))

    except WebSocketDisconnect:
        log.info("WS client disconnected", sim_id=str(sim_id))
    except Exception as exc:
        log.error("WS loop error", sim_id=str(sim_id), error=str(exc))
        seq += 1
        await _send_error(ws, sim_id, seq, "WS_ERROR", "An internal error occurred.", recoverable=False)
        try:
            await ws.close(code=1011)
        except Exception:
            pass
    finally:
        # Clean up background tasks
        for task in (pubsub_task, heartbeat_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        try:
            await pubsub.unsubscribe(events_channel(sim_id))
            await pubsub.aclose()
        except Exception:
            pass

        log.info("WS handler cleanup complete", sim_id=str(sim_id))
