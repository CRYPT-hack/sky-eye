from __future__ import annotations

import queue
import threading


class Subscription(queue.Queue):
    """Subscriber queue that counts messages the bus dropped because it was full."""

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self._dropped = 0
        self._dropped_lock = threading.Lock()

    @property
    def dropped(self) -> int:
        with self._dropped_lock:
            return self._dropped

    def _count_drop(self) -> None:
        with self._dropped_lock:
            self._dropped += 1


class EventBus:
    """Fan-out of dict messages to subscriber queues; publish never blocks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Subscription] = []

    def subscribe(self, maxsize: int = 100) -> Subscription:
        q = Subscription(maxsize=maxsize)
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
                        q._count_drop()
                    except queue.Empty:
                        pass
