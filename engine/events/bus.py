from __future__ import annotations

import queue
import threading


class EventBus:
    """Fan-out of dict messages to subscriber queues; publish never blocks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict]] = []

    def subscribe(self, maxsize: int = 100) -> queue.Queue[dict]:
        q: queue.Queue[dict] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, msg: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(msg)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()  # drop the oldest message
                    except queue.Empty:
                        pass
