import asyncio
import json
import threading
from typing import AsyncIterator, Callable, Iterable

_END = object()


async def iterate_in_thread(make_iterable: Callable[[], Iterable], cancel: threading.Event) -> AsyncIterator:
    """Drive a blocking generator on its own thread.

    The generator owns the model lock, so it must always run to completion: when the
    client goes away this generator is cancelled, which sets `cancel` and lets the
    worker wind down by itself instead of being abandoned while holding the lock.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def put(item) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        try:
            for item in make_iterable():
                put(item)
        except Exception as err:
            put(err)
        finally:
            put(_END)

    threading.Thread(target=worker, daemon=True).start()
    try:
        while True:
            item = await queue.get()
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        cancel.set()


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
