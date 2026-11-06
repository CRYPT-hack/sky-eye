from __future__ import annotations

import logging
import threading
import time

from engine.alerts.core import AlertBatch, AlertDispatcher
from engine.events.bus import EventBus


def truck(i: int) -> dict:
    return {"id": i, "media": {"snapshot": f"/m/{i}?token=SECRET"}}


def new(i: int) -> dict:
    return {"api": 1, "type": "truck.new", "truck": truck(i)}


def wait_until(pred, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


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


def make(notifiers, **kw):
    bus = EventBus()
    kw.setdefault("enabled", lambda n: True)
    d = AlertDispatcher(bus, notifiers, **kw)
    d.start()
    return bus, d


def test_batch_titles():
    assert AlertBatch((truck(12),)).title() == "New truck #12"
    assert AlertBatch((truck(1), truck(2), truck(3))).title() == "3 new trucks"
    assert AlertBatch((), is_test=True).title() == "Test alert from sky-eye"


def test_first_truck_sent_immediately():
    n = Fake("desktop")
    bus, d = make([n], cooldown_s=10.0)
    try:
        bus.publish(new(1))
        assert wait_until(lambda: len(n.batches) == 1)
        assert [t["id"] for t in n.batches[0].trucks] == [1]
    finally:
        d.stop()


def test_ignores_other_event_types():
    n = Fake("desktop")
    bus, d = make([n])
    try:
        bus.publish({"api": 1, "type": "truck.updated", "truck": truck(1)})
        time.sleep(0.2)
        assert n.batches == []
    finally:
        d.stop()


def test_burst_is_grouped_per_cooldown():
    n = Fake("desktop")
    bus, d = make([n], cooldown_s=0.4)
    try:
        for i in range(20):
            bus.publish(new(i))
        assert wait_until(lambda: len(n.batches) == 2)
        assert [len(b.trucks) for b in n.batches] == [1, 19]
        assert n.batches[1].title() == "19 new trucks"
    finally:
        d.stop()


def test_cooldown_is_per_channel():
    desk, tele = Fake("desktop"), Fake("telegram")
    on = {"desktop": False, "telegram": True}
    bus, d = make([desk, tele], enabled=lambda n: on[n], cooldown_s=0.5)
    try:
        bus.publish(new(1))
        assert wait_until(lambda: len(tele.batches) == 1)
        assert desk.batches == []
        on["desktop"] = True
        bus.publish(new(2))
        assert wait_until(lambda: len(desk.batches) == 1)  # not delayed by telegram
        assert [t["id"] for t in desk.batches[0].trucks] == [2]
        assert len(tele.batches) == 1  # telegram still cooling down
        assert wait_until(lambda: len(tele.batches) == 2)
    finally:
        d.stop()


def test_disabled_channel_skipped():
    n = Fake("mqtt")
    bus, d = make([n], enabled=lambda name: False, cooldown_s=0.05)
    try:
        bus.publish(new(1))
        time.sleep(0.3)
        assert n.batches == []
        assert n.attempts == 0
    finally:
        d.stop()


def test_retries_then_drops(caplog):
    caplog.set_level(logging.INFO)
    n = Fake("telegram", fail=4)
    sleeps: list[float] = []
    bus, d = make([n], cooldown_s=0.05, sleep=sleeps.append)
    try:
        bus.publish(new(1))
        assert wait_until(lambda: n.attempts == 4)
        assert wait_until(lambda: any(r.levelno == logging.ERROR for r in caplog.records))
        assert sleeps == [1.0, 2.0, 4.0]
        assert n.batches == []
        bus.publish(new(2))
        assert wait_until(lambda: len(n.batches) == 1)
        assert [t["id"] for t in n.batches[0].trucks] == [2]
        err = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert "telegram" in err[0].getMessage()
        text = caplog.text
        assert "SECRET" not in text and "token=" not in text
    finally:
        d.stop()


def test_failing_channel_never_blocks_publish():
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = make([n])
    try:
        t0 = time.monotonic()
        for i in range(50):
            bus.publish(new(i))
        assert time.monotonic() - t0 < 0.05
    finally:
        gate.set()
        d.stop()


def test_queue_full_drops_oldest(caplog):
    caplog.set_level(logging.WARNING)
    gate = threading.Event()
    n = Fake("desktop", block=gate)
    bus, d = make([n], queue_max=3, cooldown_s=0.01)
    try:
        bus.publish(new(0))
        assert wait_until(lambda: n.attempts == 1)  # worker stalled in send
        for i in range(1, 6):
            bus.publish(new(i))
            time.sleep(0.02)
        assert wait_until(lambda: any(r.levelno == logging.WARNING for r in caplog.records))
        gate.set()
        assert wait_until(lambda: sum(len(b.trucks) for b in n.batches) >= 4)
        time.sleep(0.2)
        ids = [t["id"] for b in n.batches for t in b.trucks]
        assert 1 not in ids and 0 in ids and 5 in ids
        assert "SECRET" not in caplog.text
    finally:
        gate.set()
        d.stop()


def test_send_test_ignores_cooldown():
    n = Fake("desktop")
    off = Fake("mqtt")
    bus, d = make([n, off], enabled=lambda name: name == "desktop", cooldown_s=60.0)
    try:
        bus.publish(new(1))
        assert wait_until(lambda: len(n.batches) == 1)
        d.send_test()
        assert wait_until(lambda: len(n.batches) == 2)
        assert n.batches[1].is_test and n.batches[1].trucks == ()
        assert off.batches == []
    finally:
        d.stop()


def test_stop_is_prompt():
    n = Fake("desktop", fail=100)
    bus, d = make([n])  # default interruptible sleep, retry sleeps 1s,2s,4s
    bus.publish(new(1))
    assert wait_until(lambda: n.attempts >= 1)
    t0 = time.monotonic()
    d.stop()
    assert time.monotonic() - t0 < 1.0
    attempts = n.attempts
    time.sleep(0.3)
    assert n.attempts == attempts
