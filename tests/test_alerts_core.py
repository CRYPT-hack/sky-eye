from __future__ import annotations

import logging
import threading
import time

import pytest

from engine.alerts.core import AlertBatch, AlertDispatcher, AlertError
from engine.events.bus import EventBus


def truck(i: int) -> dict:
    return {"id": i, "media": {"snapshot": f"/m/{i}?token=SECRET"}}


def new(i: int) -> dict:
    return {"api": 1, "type": "truck.new", "truck": truck(i)}


def wait_until(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.t

    def advance(self, s: float) -> None:
        with self._lock:
            self.t += s


class Fake:
    def __init__(self, name: str, fail: int = 0, block: threading.Event | None = None):
        self.name = name
        self.batches: list[AlertBatch] = []
        self.attempts = 0
        self._fail = fail
        self._block = block

    def send(self, batch: AlertBatch) -> None:
        self.attempts += 1
        if self._block is not None:
            self._block.wait(10)
        if self.attempts <= self._fail:
            raise RuntimeError("boom https://api.example/bot123:SECRET")
        self.batches.append(batch)


def ids(n: Fake) -> list[list[int]]:
    return [[t["id"] for t in b.trucks] for b in n.batches]


def pending(d: AlertDispatcher, i: int = 0) -> int:
    return len(d._channels[i].pending)


def drop_msgs(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "events dropped" in r.getMessage()]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def running():
    made: list[AlertDispatcher] = []

    def make(notifiers, **kw):
        bus = EventBus()
        kw.setdefault("enabled", lambda n: True)
        kw.setdefault("cooldown_s", 10.0)
        d = AlertDispatcher(bus, notifiers, **kw)
        d.start()
        made.append(d)
        return bus, d

    yield make
    for d in made:
        d.stop(timeout=1.0)


def test_batch_titles():
    assert AlertBatch((truck(12),)).title() == "New truck #12"
    assert AlertBatch((truck(1), truck(2), truck(3))).title() == "3 new trucks"
    assert AlertBatch((), is_test=True).title() == "Test alert from sky-eye"


def test_queue_max_must_be_positive():
    with pytest.raises(ValueError):
        AlertDispatcher(EventBus(), [], enabled=lambda n: True, queue_max=0)


def test_first_truck_sent_immediately(running, clock):
    n = Fake("desktop")
    bus, d = running([n], clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: ids(n) == [[1]])


def test_ignores_other_event_types(running, clock):
    n = Fake("desktop")
    bus, d = running([n], clock=clock)
    bus.publish({"api": 1, "type": "truck.updated", "truck": truck(1)})
    bus.publish(new(2))
    assert wait_until(lambda: ids(n) == [[2]])


def test_burst_is_grouped_per_cooldown(running, clock):
    n = Fake("desktop")
    bus, d = running([n], clock=clock)
    for i in range(20):
        bus.publish(new(i))
    assert wait_until(lambda: len(n.batches) == 1 and pending(d) == 19)
    clock.advance(10)
    assert wait_until(lambda: len(n.batches) == 2)
    assert [len(b.trucks) for b in n.batches] == [1, 19]
    assert n.batches[1].title() == "19 new trucks"


def test_cooldown_is_per_channel(running, clock):
    desk, tele = Fake("desktop"), Fake("telegram")
    on = {"desktop": False, "telegram": True}
    seen: list[str] = []

    def enabled(name: str) -> bool:
        seen.append(name)
        return on[name]

    bus, d = running([desk, tele], enabled=enabled, clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: ids(tele) == [[1]] and seen.count("desktop") == 1)
    on["desktop"] = True
    bus.publish(new(2))
    assert wait_until(lambda: ids(desk) == [[2]] and pending(d, 1) == 1)
    assert len(tele.batches) == 1  # telegram still cooling down
    clock.advance(10)
    assert wait_until(lambda: ids(tele) == [[1], [2]])
    assert ids(desk) == [[2]]


def test_disabled_channel_skipped(running, clock):
    n = Fake("mqtt")
    calls: list[str] = []

    def enabled(name: str) -> bool:
        calls.append(name)
        return False

    bus, d = running([n], enabled=enabled, clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: calls == ["mqtt"])
    assert n.batches == [] and n.attempts == 0


def test_retries_then_drops(running, clock, caplog):
    caplog.set_level(logging.INFO)
    n = Fake("telegram", fail=4)
    sleeps: list[float] = []
    bus, d = running([n], clock=clock, sleep=sleeps.append)
    bus.publish(new(1))
    assert wait_until(lambda: any(r.levelno == logging.ERROR for r in caplog.records))
    assert n.attempts == 4 and sleeps == [1.0, 2.0, 4.0] and n.batches == []
    bus.publish(new(2))
    assert wait_until(lambda: pending(d) == 1)
    clock.advance(10)
    assert wait_until(lambda: ids(n) == [[2]])
    err = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "telegram" in err[0].getMessage()
    assert "SECRET" not in caplog.text and "token=" not in caplog.text


class FailWith:
    def __init__(self, name: str, exc: Exception) -> None:
        self.name = name
        self.attempts = 0
        self._exc = exc

    def send(self, batch: AlertBatch) -> None:
        self.attempts += 1
        raise self._exc


def test_alert_error_text_is_logged_redacted(running, clock, caplog):
    caplog.set_level(logging.WARNING)
    exc = AlertError(
        "telegram sendMessage failed (HTTP 400): chat not found "
        "(https://api.telegram.org/bot123:SECRET-x/sendMessage)"
    )
    n = FailWith("telegram", exc)
    bus, d = running([n], clock=clock, sleep=lambda s: None)
    bus.publish(new(1))
    assert wait_until(lambda: any(r.levelno == logging.ERROR for r in caplog.records))
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 4 and all("HTTP 400): chat not found" in w for w in warns)
    assert "SECRET" not in caplog.text and "bot***" in caplog.text
    err = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert "dropping batch of 1" in err[0]


def test_failed_test_alert_is_named_in_the_log(running, clock, caplog):
    caplog.set_level(logging.WARNING)
    n = FailWith("mqtt", AlertError("mqtt broker not connected"))
    bus, d = running([n], clock=clock, sleep=lambda s: None)
    d.send_test()
    assert wait_until(lambda: any(r.levelno == logging.ERROR for r in caplog.records))
    err = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert "mqtt" in err[0] and "test alert" in err[0] and "batch of 0" not in caplog.text
    assert "mqtt broker not connected" in caplog.text


def test_failing_channel_never_blocks_publish(running, clock):
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = running([n], clock=clock)
    try:
        t0 = time.monotonic()
        for i in range(50):
            bus.publish(new(i))
        assert time.monotonic() - t0 < 0.05
    finally:
        gate.set()


def test_queue_full_drops_oldest(running, clock, caplog):
    caplog.set_level(logging.WARNING)
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = running([n], queue_max=3, clock=clock)
    try:
        bus.publish(new(0))
        assert wait_until(lambda: n.attempts == 1)  # worker stalled in send
        for i in range(1, 6):
            bus.publish(new(i))
        assert wait_until(lambda: any(r.levelno == logging.WARNING for r in caplog.records))
    finally:
        gate.set()
    clock.advance(10)
    assert wait_until(lambda: 5 in [i for b in ids(n) for i in b])
    delivered = [i for b in ids(n) for i in b]
    assert 0 in delivered and 1 not in delivered  # oldest dropped, newest kept
    assert "SECRET" not in caplog.text


def test_bus_overflow_warns_with_count_rate_limited(running, clock, caplog):
    caplog.set_level(logging.WARNING)
    bus, d = running([Fake("desktop")], clock=clock)
    ch = d._channels[0]
    orig = ch.offer
    entered, gate = threading.Event(), threading.Event()

    calls = {"n": 0, "done": 0}

    def blocking_offer(item):
        calls["n"] += 1
        entered.set()
        gate.wait(5)
        orig(item)
        calls["done"] += 1

    ch.offer = blocking_offer
    # stall the reader inside offer, then burst with no sleeps (sub queue holds 100)
    bus.publish(new(0))
    assert entered.wait(3)
    for i in range(1, 111):
        bus.publish(new(i))
    gate.set()
    assert wait_until(lambda: len(drop_msgs(caplog)) == 1)
    assert drop_msgs(caplog) == ["alert subscription full; 10 events dropped"]
    # second burst inside the 5 s window: no new warning until the window passes
    # every event of burst 1 (1 held + 100 queued) has passed through offer
    assert wait_until(lambda: d._sub.qsize() == 0 and calls["done"] == 101)
    gate.clear()
    entered.clear()
    bus.publish(new(200))
    assert entered.wait(3)
    for i in range(201, 312):
        bus.publish(new(i))
    gate.set()
    assert wait_until(lambda: d._sub.qsize() == 0)
    time.sleep(0.3)  # let the reader loop a few times; the fake clock has not moved
    assert len(drop_msgs(caplog)) == 1
    clock.advance(5)
    assert wait_until(lambda: len(drop_msgs(caplog)) == 2)
    assert drop_msgs(caplog)[1] == "alert subscription full; 11 events dropped"


def test_pile_up_during_slow_delivery_is_grouped(running, clock):
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = running([n], clock=clock)
    try:
        bus.publish(new(0))
        assert wait_until(lambda: n.attempts == 1)
        for i in range(1, 6):
            bus.publish(new(i))
        assert wait_until(lambda: d._channels[0].inbox.qsize() == 5)
        clock.advance(11)  # cooldown elapsed while the send was stuck
    finally:
        gate.set()
    assert wait_until(lambda: len(n.batches) == 2)
    assert ids(n) == [[0], [1, 2, 3, 4, 5]]


@pytest.mark.parametrize("arrive_at", [2.0, 15.0])
def test_trucks_during_stuck_send_go_out_as_one_batch(running, clock, arrive_at):
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = running([n], clock=clock)  # cooldown 10 s
    try:
        bus.publish(new(0))
        assert wait_until(lambda: n.attempts == 1)  # send stuck, last_sent = t0
        clock.advance(arrive_at)
        for i in range(1, 6):
            bus.publish(new(i))
        assert wait_until(lambda: d._channels[0].inbox.qsize() == 5)
        clock.advance(30 - arrive_at)  # send stays stuck for 30 s in total
    finally:
        gate.set()
    assert wait_until(lambda: len(n.batches) == 2)
    assert ids(n) == [[0], [1, 2, 3, 4, 5]]
    assert pending(d) == 0


def test_truck_after_idle_period_goes_out_immediately(running, clock):
    n = Fake("desktop")
    bus, d = running([n], clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: ids(n) == [[1]])
    clock.advance(60)
    bus.publish(new(2))
    assert wait_until(lambda: ids(n) == [[1], [2]])


def test_send_test_ignores_cooldown(running, clock):
    n, off = Fake("desktop"), Fake("mqtt")
    calls: list[str] = []

    def enabled(name: str) -> bool:
        calls.append(name)
        return name == "desktop"

    bus, d = running([n, off], enabled=enabled, clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: len(n.batches) == 1 and calls.count("mqtt") == 1)
    d.send_test()
    assert wait_until(lambda: len(n.batches) == 2 and calls.count("mqtt") == 2)
    assert n.batches[1].is_test and n.batches[1].trucks == ()
    assert off.batches == []


def test_stop_is_prompt():
    n = Fake("desktop", fail=100)
    bus = EventBus()
    d = AlertDispatcher(bus, [n], enabled=lambda x: True)  # default interruptible sleep
    d.start()
    try:
        bus.publish(new(1))
        assert wait_until(lambda: n.attempts >= 1)
        t0 = time.monotonic()
        d.stop()
        assert time.monotonic() - t0 < 1.0
        attempts = n.attempts
        time.sleep(0.3)
        assert n.attempts == attempts
    finally:
        d.stop(timeout=1.0)


def test_stop_warns_when_notifier_stuck(caplog):
    caplog.set_level(logging.WARNING)
    gate = threading.Event()
    n = Fake("telegram", block=gate)
    bus = EventBus()
    d = AlertDispatcher(bus, [n], enabled=lambda x: True)
    d.start()
    try:
        bus.publish(new(1))
        assert wait_until(lambda: n.attempts == 1)
        t0 = time.monotonic()
        d.stop(timeout=0.5)
        assert time.monotonic() - t0 < 1.0
        assert any(
            "telegram" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )
        with pytest.raises(RuntimeError):  # old worker still alive: refuse restart
            d.start()
    finally:
        gate.set()
        d.stop(timeout=1.0)


def test_stop_reports_dropped_held_alerts(clock, caplog):
    caplog.set_level(logging.WARNING)
    n = Fake("desktop")
    bus = EventBus()
    d = AlertDispatcher(bus, [n], enabled=lambda x: True, cooldown_s=10.0, clock=clock)
    d.start()
    try:
        bus.publish(new(1))
        assert wait_until(lambda: ids(n) == [[1]])
        bus.publish(new(2))
        bus.publish(new(3))
        assert wait_until(lambda: pending(d) == 2)  # held by the cooldown
        d.send_test()  # a test marker is not a held truck
        assert d.stop() == 2
        assert "dropped 2 held alert(s) (desktop: 2)" in caplog.text
        assert d.stop() == 0  # nothing more to report
    finally:
        d.stop(timeout=1.0)


def test_stop_without_held_alerts_logs_nothing(clock, caplog):
    caplog.set_level(logging.WARNING)
    n = Fake("desktop")
    bus = EventBus()
    d = AlertDispatcher(bus, [n], enabled=lambda x: True, clock=clock)
    d.start()
    bus.publish(new(1))
    assert wait_until(lambda: ids(n) == [[1]])
    assert d.stop() == 0
    assert "held alert" not in caplog.text


def test_restart_after_stop(clock):
    n = Fake("desktop")
    bus = EventBus()
    d = AlertDispatcher(bus, [n], enabled=lambda x: True, cooldown_s=10.0, clock=clock)
    d.start()
    try:
        bus.publish(new(1))
        assert wait_until(lambda: ids(n) == [[1]])
        d.stop()
        d.start()
        bus.publish(new(2))
        assert wait_until(lambda: ids(n) == [[1], [2]])
    finally:
        d.stop(timeout=1.0)


def test_worker_survives_raising_enabled(running, clock, caplog):
    caplog.set_level(logging.ERROR)
    n = Fake("desktop")
    state = {"calls": 0}

    def enabled(name: str) -> bool:
        state["calls"] += 1
        if state["calls"] == 1:
            raise ValueError("secret-detail")
        return True

    bus, d = running([n], enabled=enabled, clock=clock)
    bus.publish(new(1))
    assert wait_until(lambda: any(r.levelno == logging.ERROR for r in caplog.records))
    bus.publish(new(2))
    assert wait_until(lambda: ids(n) == [[2]])
    assert "secret-detail" not in caplog.text and "SECRET" not in caplog.text
    assert "desktop" in caplog.text and "ValueError" in caplog.text
