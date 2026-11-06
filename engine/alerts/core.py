"""Alert dispatcher: bus -> per-channel cooldown/grouping -> notifiers with retries.

Detection never waits on alerts: the dispatcher only reads its own bounded bus
subscription, and each channel has its own worker thread and bounded inbox, so a
slow or failing channel cannot delay the bus or other channels.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from engine.events.bus import EventBus

log = logging.getLogger("engine.alerts")

_POLL_S = 0.1


@dataclass(frozen=True)
class AlertBatch:
    trucks: tuple[dict, ...]
    is_test: bool = False

    def title(self) -> str:
        if self.is_test:
            return "Test alert from sky-eye"
        if len(self.trucks) == 1:
            return f"New truck #{self.trucks[0].get('id')}"
        return f"{len(self.trucks)} new trucks"


class Notifier(Protocol):
    name: str

    def send(self, batch: AlertBatch) -> None: ...


_TEST = object()  # inbox marker for send_test()


class _Channel:
    def __init__(self, notifier: Notifier, queue_max: int) -> None:
        self.notifier = notifier
        self.name = notifier.name
        self.inbox: queue.Queue = queue.Queue(maxsize=queue_max)
        self.queue_max = queue_max
        self.thread: threading.Thread | None = None
        # worker-thread-only state
        self.last_sent: float | None = None
        self.pending: list[dict] = []
        self.deadline = 0.0

    def offer(self, item: object) -> None:
        """Non-blocking put; drops the oldest item (with a WARNING) when full."""
        while True:
            try:
                self.inbox.put_nowait(item)
                return
            except queue.Full:
                try:
                    self.inbox.get_nowait()
                except queue.Empty:
                    continue
                log.warning("alert queue full for channel %s; dropped oldest", self.name)


class AlertDispatcher:
    def __init__(
        self,
        bus: EventBus,
        notifiers: list[Notifier],
        enabled: Callable[[str], bool],
        cooldown_s: float = 10.0,
        retries: tuple[float, ...] = (1.0, 2.0, 4.0),
        queue_max: int = 100,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._bus = bus
        self._enabled = enabled
        self._cooldown = cooldown_s
        self._retries = tuple(retries)
        self._queue_max = queue_max
        self._clock = clock
        self._stop = threading.Event()
        self._sleep = sleep if sleep is not None else self._interruptible_sleep
        self._channels = [_Channel(n, queue_max) for n in notifiers]
        self._sub: queue.Queue[dict] | None = None
        self._reader: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._reader is not None:
            return
        self._stop.clear()
        self._sub = self._bus.subscribe(maxsize=self._queue_max)
        for ch in self._channels:
            ch.thread = threading.Thread(
                target=self._run_channel, args=(ch,), name=f"alerts-{ch.name}", daemon=True
            )
            ch.thread.start()
        self._reader = threading.Thread(target=self._read_bus, name="alerts-reader", daemon=True)
        self._reader.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._sub is not None:
            self._bus.unsubscribe(self._sub)
        end = time.monotonic() + timeout
        threads = [self._reader] + [ch.thread for ch in self._channels]
        for t in threads:
            if t is not None:
                t.join(max(0.0, end - time.monotonic()))
        self._reader = None
        self._sub = None

    def send_test(self) -> None:
        for ch in self._channels:
            ch.offer(_TEST)

    # -- internals ---------------------------------------------------------
    def _interruptible_sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _read_bus(self) -> None:
        assert self._sub is not None
        sub = self._sub
        while not self._stop.is_set():
            try:
                msg = sub.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            if msg.get("type") != "truck.new":
                continue
            t = msg.get("truck")
            if not isinstance(t, dict):
                continue
            for ch in self._channels:
                ch.offer(t)

    def _run_channel(self, ch: _Channel) -> None:
        while not self._stop.is_set():
            if ch.pending:
                timeout = max(0.0, ch.deadline - self._clock())
            else:
                timeout = _POLL_S
            try:
                item = ch.inbox.get(timeout=min(timeout, _POLL_S) if timeout else 0)
            except queue.Empty:
                item = None
            if item is _TEST:
                if self._enabled(ch.name):
                    self._deliver(ch, AlertBatch((), is_test=True))
            elif item is not None:
                self._on_truck(ch, item)
            if ch.pending and self._clock() >= ch.deadline:
                self._flush(ch)

    def _on_truck(self, ch: _Channel, truck: dict) -> None:
        ch.pending.append(truck)
        if len(ch.pending) > ch.queue_max:
            del ch.pending[0]
            log.warning("alert queue full for channel %s; dropped oldest", ch.name)
        if len(ch.pending) == 1:
            ch.deadline = (
                ch.last_sent + self._cooldown if ch.last_sent is not None else self._clock()
            )

    def _flush(self, ch: _Channel) -> None:
        trucks, ch.pending = tuple(ch.pending), []
        if not self._enabled(ch.name):
            return
        ch.last_sent = self._clock()
        self._deliver(ch, AlertBatch(trucks))

    def _deliver(self, ch: _Channel, batch: AlertBatch) -> None:
        attempts = 1 + len(self._retries)
        for i in range(attempts):
            if self._stop.is_set():
                return
            try:
                ch.notifier.send(batch)
                return
            except Exception as exc:  # message may carry secrets: log type only
                log.warning(
                    "alert send failed on channel %s (attempt %d/%d): %s",
                    ch.name, i + 1, attempts, type(exc).__name__,
                )
            if i < len(self._retries):
                self._sleep(self._retries[i])
        log.error(
            "alert channel %s failed after %d attempts; dropping batch of %d",
            ch.name, attempts, len(batch.trucks),
        )
