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

from engine.events.bus import EventBus, Subscription

log = logging.getLogger("engine.alerts")

_POLL_S = 0.1
_DROP_WARN_S = 5.0


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


class AlertError(Exception):
    """A notifier failed to deliver; the text is safe to log (no secrets)."""


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
        self.idle_since: float | None = None  # when the last delivery ended
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
        if queue_max < 1:
            raise ValueError("queue_max must be >= 1")
        self._bus = bus
        self._notifiers = list(notifiers)
        self._enabled = enabled
        self._cooldown = cooldown_s
        self._retries = tuple(retries)
        self._queue_max = queue_max
        self._clock = clock
        self._custom_sleep = sleep
        self._stop = threading.Event()
        self._channels: list[_Channel] = [_Channel(n, queue_max) for n in self._notifiers]
        self._sub: Subscription | None = None
        self._reader: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------
    def _alive(self) -> bool:
        threads = [self._reader] + [ch.thread for ch in self._channels]
        return any(t is not None and t.is_alive() for t in threads)

    def start(self) -> None:
        if self._alive():
            raise RuntimeError("alert dispatcher threads still running; cannot (re)start yet")
        stop = threading.Event()  # fresh per start: a stuck old worker keeps its own
        self._stop = stop
        self._channels = [_Channel(n, self._queue_max) for n in self._notifiers]
        self._sub = self._bus.subscribe(maxsize=self._queue_max)
        for ch in self._channels:
            ch.thread = threading.Thread(
                target=self._run_channel, args=(ch, stop), name=f"alerts-{ch.name}", daemon=True
            )
            ch.thread.start()
        self._reader = threading.Thread(
            target=self._read_bus, args=(self._sub, stop), name="alerts-reader", daemon=True
        )
        self._reader.start()

    def stop(self, timeout: float = 5.0) -> int:
        """Stop the reader and the channel workers (waiting at most `timeout` in
        total). Trucks still held for a cooldown are dropped: returns (and logs)
        how many."""
        self._stop.set()
        if self._sub is not None:
            self._bus.unsubscribe(self._sub)
        end = time.monotonic() + timeout
        named = [("reader", self._reader)] + [(ch.name, ch.thread) for ch in self._channels]
        for name, t in named:
            if t is None:
                continue
            t.join(max(0.0, end - time.monotonic()))
            if t.is_alive():
                log.warning("alert worker for %s did not stop within timeout (stuck in I/O?)", name)
        return self._drop_held()

    def _drop_held(self) -> int:
        """After stop: forget the trucks no channel will send now; log how many."""
        held: list[str] = []
        total = 0
        for ch in self._channels:
            with ch.inbox.mutex:
                waiting = sum(1 for item in ch.inbox.queue if item is not _TEST)
                ch.inbox.queue.clear()
            count = len(ch.pending) + waiting
            ch.pending = []
            if count:
                held.append(f"{ch.name}: {count}")
                total += count
        if total:
            log.warning(
                "alerts stopping: dropped %d held alert(s) (%s)", total, ", ".join(held)
            )
        return total

    def send_test(self) -> None:
        for ch in self._channels:
            ch.offer(_TEST)

    # -- internals ---------------------------------------------------------
    def _sleep(self, seconds: float, stop: threading.Event) -> None:
        if self._custom_sleep is not None:
            self._custom_sleep(seconds)
        else:
            stop.wait(seconds)

    def _read_bus(self, sub: Subscription, stop: threading.Event) -> None:
        last_seen = 0
        unreported = 0
        last_warn: float | None = None
        while not stop.is_set():
            dropped = sub.dropped
            unreported += dropped - last_seen
            last_seen = dropped
            if unreported and (last_warn is None or self._clock() - last_warn >= _DROP_WARN_S):
                log.warning("alert subscription full; %d events dropped", unreported)
                unreported = 0
                last_warn = self._clock()
            try:
                msg = sub.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            if msg.get("type") != "truck.new":
                continue
            t = msg.get("truck")
            if not isinstance(t, dict):
                continue
            stamp = self._clock()
            for ch in self._channels:
                ch.offer((stamp, t))

    def _run_channel(self, ch: _Channel, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self._step(ch, stop)
            except Exception as exc:  # never let the worker die; type only, no data
                log.error("alert worker error on channel %s: %s", ch.name, type(exc).__name__)
                stop.wait(_POLL_S)

    def _step(self, ch: _Channel, stop: threading.Event) -> None:
        if ch.pending:
            timeout = min(max(0.0, ch.deadline - self._clock()), _POLL_S)
        else:
            timeout = _POLL_S
        try:
            items = [ch.inbox.get(timeout=timeout) if timeout else ch.inbox.get_nowait()]
        except queue.Empty:
            items = []
        while items:  # drain whatever piled up so it is grouped into one batch
            try:
                items.append(ch.inbox.get_nowait())
            except queue.Empty:
                break
        for item in items:
            if item is _TEST:
                if self._enabled(ch.name):
                    self._deliver(ch, AlertBatch((), is_test=True), stop)
            else:
                self._on_truck(ch, item[0], item[1], stop)
        if ch.pending and self._clock() >= ch.deadline:
            self._flush(ch, stop)

    def _on_truck(self, ch: _Channel, stamp: float, truck: dict, stop: threading.Event) -> None:
        """Group by *arrival* time (stamp). A truck goes out alone only if it arrived after
        the last delivery ended (channel idle) and the cooldown has elapsed; anything that
        arrived while a send was in progress joins the pile-up and leaves as one batch."""
        idle = ch.idle_since is None or stamp >= ch.idle_since
        cooled = ch.last_sent is None or stamp - ch.last_sent >= self._cooldown
        if not ch.pending and idle and cooled:
            ch.pending.append(truck)
            self._flush(ch, stop)
            return
        ch.pending.append(truck)
        if len(ch.pending) > ch.queue_max:
            del ch.pending[0]
            log.warning("alert queue full for channel %s; dropped oldest", ch.name)
        if len(ch.pending) == 1:
            ch.deadline = (
                ch.last_sent + self._cooldown if ch.last_sent is not None else self._clock()
            )

    def _flush(self, ch: _Channel, stop: threading.Event) -> None:
        trucks, ch.pending = tuple(ch.pending), []
        if not self._enabled(ch.name):
            return
        ch.last_sent = self._clock()
        self._deliver(ch, AlertBatch(trucks), stop)

    def _deliver(self, ch: _Channel, batch: AlertBatch, stop: threading.Event) -> None:
        try:
            self._deliver_attempts(ch, batch, stop)
        finally:
            ch.idle_since = self._clock()

    def _deliver_attempts(
        self, ch: _Channel, batch: AlertBatch, stop: threading.Event
    ) -> None:
        attempts = 1 + len(self._retries)
        for i in range(attempts):
            if stop.is_set():
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
                self._sleep(self._retries[i], stop)
        log.error(
            "alert channel %s failed after %d attempts; dropping batch of %d",
            ch.name, attempts, len(batch.trucks),
        )
