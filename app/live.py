"""In-process publish/subscribe used for live updates over Server-Sent Events.

Channels are plain strings: ``r:<restaurant_id>`` for staff dashboards and ``t:<table_id>`` for guests at a table.
Handlers run in a thread pool, so ``publish`` is thread-safe and never blocks. This suits a single app process;
for several instances, replace the broker with Redis pub/sub (the interface is deliberately tiny).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

KEEPALIVE_SECONDS = 20
QUEUE_SIZE = 100


class Broker:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.channels: dict[str, set[asyncio.Queue]] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, channel: str, limit: int | None = None) -> asyncio.Queue | None:
        subscribers = self.channels.setdefault(channel, set())
        if limit is not None and len(subscribers) >= limit:
            return None
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        subscribers.add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        subscribers = self.channels.get(channel)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self.channels.pop(channel, None)

    def publish(self, channel: str, event: str, data: Any = None) -> None:
        if self.loop is None or channel not in self.channels:
            return
        try:
            self.loop.call_soon_threadsafe(self._deliver, channel, (event, data))
        except RuntimeError:  # loop already closed during shutdown
            pass

    def _deliver(self, channel: str, message: tuple[str, Any]) -> None:
        for queue in list(self.channels.get(channel, ())):
            if queue.full():  # a stalled client: drop its backlog and tell it to resync on the next read
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(("resync", None))
            else:
                queue.put_nowait(message)


broker = Broker()


def _frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def sse_response(channel: str, hello: Any = None, limit: int | None = None) -> StreamingResponse:
    queue = broker.subscribe(channel, limit)
    if queue is None:
        raise HTTPException(status_code=429, detail="Too many open connections for this table.")

    async def stream() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n" + _frame("hello", hello)
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _frame(event, data)
        finally:
            broker.unsubscribe(channel, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def staff_channel(restaurant_id: int) -> str:
    return f"r:{restaurant_id}"


def table_channel(table_id: int) -> str:
    return f"t:{table_id}"
