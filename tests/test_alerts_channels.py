import json
import logging
import socket
import threading
import time
from datetime import datetime

import httpx
import pytest

import engine.alerts.mqtt as mqtt_mod
from engine.alerts.core import AlertBatch
from engine.alerts.desktop import DesktopNotifier
from engine.alerts.mqtt import MqttNotifier
from engine.alerts.telegram import AlertError, TelegramNotifier

TOKEN = "123456:SECRET-token_XYZ"


def truck(i=5, crop="/media/2026-11-06/5_3_crop.jpg?v=5398", score=0.824):
    return {
        "id": i,
        "first_seen": "2026-11-06T10:00:00Z",
        "last_seen": "2026-11-06T10:00:05Z",
        "best_score": score,
        "crop_url": crop,
        "snapshot_url": "/media/2026-11-06/5_3.jpg?v=1",
    }


def local_hms(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")


# ---------------- desktop ----------------
def test_desktop_title_and_message():
    calls, beeps = [], []
    n = DesktopNotifier(notify=lambda **kw: calls.append(kw), beep=lambda: beeps.append(1))
    n.send(AlertBatch((truck(),)))
    assert calls == [
        {
            "title": "New truck #5",
            "message": f"Seen at {local_hms('2026-11-06T10:00:05Z')}, score 82%",
            "app_name": "sky-eye",
            "timeout": 5,
        }
    ]
    assert beeps == [1]
    n.send(AlertBatch((truck(1), truck(2))))
    assert calls[1]["title"] == "2 new trucks"
    assert calls[1]["message"].startswith("Latest at ")


def test_desktop_notify_error_propagates():
    def boom(**kw):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        DesktopNotifier(notify=boom, beep=lambda: None).send(AlertBatch((truck(),)))


# ---------------- telegram ----------------
def make_tg(tmp_path, handler, dashboard_url=None):
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    return TelegramNotifier(TOKEN, "42", tmp_path, dashboard_url=dashboard_url, client=client)


def write_crop(tmp_path):
    d = tmp_path / "2026-11-06"
    d.mkdir()
    (d / "5_3_crop.jpg").write_bytes(b"\xff\xd8JPEGDATA")


def recorder():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return seen, handler


def test_telegram_single_truck_sends_photo(tmp_path):
    write_crop(tmp_path)
    seen, handler = recorder()
    make_tg(tmp_path, handler, "http://dash:8000").send(AlertBatch((truck(),)))
    (req,) = seen
    assert str(req.url) == f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    body = req.content
    assert b'name="chat_id"' in body and b"42" in body
    assert b"JPEGDATA" in body and b'name="photo"' in body
    assert b"New truck #5" in body
    assert b"http://dash:8000/#/trucks/5" in body
    assert b"score 82%" in body


def test_telegram_group_sends_message(tmp_path):
    write_crop(tmp_path)
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((truck(5), truck(6))))
    assert seen[0].url.path.endswith("/sendMessage")
    data = json.loads(seen[0].content)
    assert data["chat_id"] == "42" and "2 new trucks" in data["text"]


def test_telegram_missing_crop_falls_back_to_message(tmp_path):
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((truck(),)))
    assert seen[0].url.path.endswith("/sendMessage")


def test_telegram_path_traversal_falls_back(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    (tmp_path / "secret.jpg").write_bytes(b"nope")
    seen, handler = recorder()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = TelegramNotifier(TOKEN, "42", root, client=client)
    n.send(AlertBatch((truck(crop="/media/../secret.jpg?v=1"),)))
    n.send(AlertBatch((truck(crop="/media/%2e%2e/secret.jpg"),)))
    assert len(seen) == 2 and all(r.url.path.endswith("/sendMessage") for r in seen)


@pytest.mark.parametrize(
    "crop",
    [
        "/media//etc/passwd",
        "/media/%2Fetc%2Fpasswd",
        "/media/C:/Windows/win.ini",
        "/media/C:secret.jpg",
        "/media/%5C%5Cserver%5Cshare%5Cx.jpg",
        "/media///server/share/x.jpg",
        "/media/%5Croot.jpg",
        "/media/2026-11-06/..%5C..%5Csecret.jpg",
        "/media/a/../../secret.jpg",
        "/media/a/..",
    ],
)
def test_telegram_crop_path_checked_before_touching_the_disk(tmp_path, monkeypatch, crop):
    from pathlib import Path

    resolved: list[str] = []
    real = Path.resolve

    def spy(self, *a, **k):
        resolved.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", spy)
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((truck(crop=crop),)))
    assert seen[0].url.path.endswith("/sendMessage")
    assert resolved == []  # rejected lexically: no UNC/drive lookup, no resolve()


def test_telegram_nested_crop_path_still_sent(tmp_path):
    d = tmp_path / "2026-11-06" / "sub"
    d.mkdir(parents=True)
    (d / "x_crop.jpg").write_bytes(b"\xff\xd8JPEG")
    seen, handler = recorder()
    crop = "/media/2026-11-06/sub/x_crop.jpg?v=1"
    make_tg(tmp_path, handler).send(AlertBatch((truck(crop=crop),)))
    assert seen[0].url.path.endswith("/sendPhoto")


def test_telegram_nul_in_crop_url_falls_back(tmp_path):
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((truck(crop="/media/a%00b.jpg"),)))
    assert seen[0].url.path.endswith("/sendMessage")


def test_telegram_test_batch(tmp_path):
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((), is_test=True))
    assert json.loads(seen[0].content)["text"] == "Test alert from sky-eye ✅"


def test_token_redacted_in_logs_and_errors(tmp_path, caplog):
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    n = make_tg(tmp_path, handler)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(AlertError) as ei:
            n.send(AlertBatch((truck(),)))
    text = str(ei.value) + repr(ei.value) + "".join(str(a) for a in ei.value.args)
    assert TOKEN not in text and "SECRET-token" not in text
    assert "bot***" in text
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    assert TOKEN not in caplog.text


class SkyLogs:
    def __init__(self, path, console):
        self.path, self._console = path, console

    def texts(self) -> list[str]:
        """What reached the console and the rotating log file."""
        for handler in logging.getLogger().handlers:
            handler.flush()
        return [self._console.getvalue(), self.path.read_text(encoding="utf-8")]


@pytest.fixture
def sky_logging(tmp_path):
    """The engine's own logging setup into tmp, console captured; undone afterwards."""
    import io

    from engine.logging_setup import setup_logging

    root = logging.getLogger()
    saved = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    root_level = root.level
    path = setup_logging(tmp_path / "data")
    console = io.StringIO()
    for handler in root.handlers:
        if getattr(handler, "_sky_eye", False) and not isinstance(handler, logging.FileHandler):
            handler.setStream(console)
    yield SkyLogs(path, console)
    for handler in list(root.handlers):
        if getattr(handler, "_sky_eye", False):
            root.removeHandler(handler)
            handler.close()
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)
    root.setLevel(root_level)


def test_successful_send_never_logs_token(tmp_path, sky_logging, caplog):
    caplog.set_level(logging.INFO)
    seen, handler = recorder()
    make_tg(tmp_path, handler).send(AlertBatch((truck(),)))
    assert len(seen) == 1
    for text in [caplog.text, *sky_logging.texts()]:
        assert TOKEN not in text and "SECRET-token" not in text


def test_log_handlers_redact_bot_tokens(sky_logging):
    logging.getLogger("x").info("https://api.telegram.org/bot123:ABC/x")
    logging.getLogger("x").warning("failed: %s", "https://api.telegram.org/bot99:A-b_c/sendPhoto")
    try:
        raise RuntimeError("POST https://api.telegram.org/bot7:Zz9/sendMessage")
    except RuntimeError:
        logging.getLogger("x").exception("boom")
    for text in sky_logging.texts():
        assert "https://api.telegram.org/bot***/x" in text
        assert "https://api.telegram.org/bot***/sendPhoto" in text
        assert "bot***/sendMessage" in text
        assert "123:ABC" not in text and "99:A-b_c" not in text and "7:Zz9" not in text


def test_telegram_http_error_redacted(tmp_path):
    def handler(request):
        return httpx.Response(401, text=f"bad bot{TOKEN}")

    with pytest.raises(AlertError) as ei:
        make_tg(tmp_path, handler).send(AlertBatch((truck(),)))
    assert TOKEN not in str(ei.value) and "401" in str(ei.value)


def test_telegram_not_ok_raises(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    with pytest.raises(AlertError, match="chat not found"):
        make_tg(tmp_path, handler).send(AlertBatch((truck(),)))


def test_telegram_default_client_timeout(tmp_path):
    n = TelegramNotifier(TOKEN, "42", tmp_path)
    assert n._client.timeout.read == 10.0
    n._client.close()


def test_telegram_close_closes_only_its_own_client(tmp_path):
    own = TelegramNotifier(TOKEN, "42", tmp_path)
    own.close()
    own.close()  # idempotent
    assert own._client.is_closed
    seen, handler = recorder()
    shared = httpx.Client(transport=httpx.MockTransport(handler))
    TelegramNotifier(TOKEN, "42", tmp_path, client=shared).close()
    assert not shared.is_closed
    shared.close()


# ---------------- mqtt ----------------
class FakeInfo:
    def __init__(self, rc):
        self.rc = rc

    def wait_for_publish(self, timeout=None):
        if self.rc != 0:
            raise RuntimeError("Message is not queued due to ERROR_QUEUE_SIZE")

    def is_published(self):
        return self.rc == 0


class FakeClient:
    """Faithful to paho 2.x: no publish success while disconnected; connect via on_connect."""

    def __init__(self, auto_connect=True, **kw):
        self.kw = kw
        self.calls = []
        self.auto_connect = auto_connect
        self.connected = False
        self.on_connect = None
        self.on_disconnect = None
        self.ack = True

    def username_pw_set(self, u, p=None):
        self.calls.append(("auth", u, p))

    def tls_set(self):
        self.calls.append(("tls",))

    def max_queued_messages_set(self, n):
        self.calls.append(("maxq", n))

    def connect_async(self, host, port):
        self.calls.append(("connect", host, port))

    def loop_start(self):
        self.calls.append(("loop_start",))
        if self.auto_connect:
            self.do_connect()

    def do_connect(self):
        self.connected = True
        self.on_connect(self, None, {}, 0, None)

    def do_disconnect(self):
        self.connected = False
        self.on_disconnect(self, None, {}, 0, None)

    def is_connected(self):
        return self.connected

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def publish(self, topic, payload, qos=0, retain=False):
        self.calls.append(("publish", topic, json.loads(payload), qos))
        return FakeInfo(0 if (self.connected and self.ack) else 4)


def mk(client, **kw):
    kw.setdefault("connect_timeout_s", 0.2)
    return MqttNotifier("b", client_factory=lambda **k: client, **kw)


def test_mqtt_lazy_connect_and_publish():
    made = []

    def factory(**kw):
        c = FakeClient(**kw)
        made.append(c)
        return c

    n = MqttNotifier(
        "broker", 1884, topic="t/e", username="u", password="pw", tls=True, client_factory=factory
    )
    assert made == []
    n.send(AlertBatch((truck(1), truck(2))))
    n.send(AlertBatch((truck(3),)))
    (c,) = made
    assert c.kw["callback_api_version"].name == "VERSION2"
    kinds = [x[0] for x in c.calls]
    assert kinds.count("connect") == 1 and kinds.count("loop_start") == 1
    assert ("auth", "u", "pw") in c.calls and ("tls",) in c.calls and ("maxq", 100) in c.calls
    assert kinds.index("loop_start") < kinds.index("publish")
    pubs = [x for x in c.calls if x[0] == "publish"]
    assert [p[1] for p in pubs] == ["t/e"] * 3 and all(p[3] == 1 for p in pubs)
    assert pubs[0][2] == {"api": 1, "type": "truck.new", "truck": truck(1)}
    n.close()
    n.close()
    assert [x[0] for x in c.calls].count("disconnect") == 1


def test_mqtt_first_send_waits_for_async_connect():
    import threading

    c = FakeClient(auto_connect=False)
    threading.Timer(0.1, c.do_connect).start()
    mk(c, connect_timeout_s=2.0).send(AlertBatch((truck(),)))
    assert [x[0] for x in c.calls].count("publish") == 1


def test_mqtt_never_connects_raises_without_publish():
    c = FakeClient(auto_connect=False)
    n = mk(c)
    for _ in range(3):
        with pytest.raises(AlertError, match="not connected"):
            n.send(AlertBatch((truck(),)))
    kinds = [x[0] for x in c.calls]
    assert "publish" not in kinds and kinds.count("loop_start") == 1


def test_mqtt_disconnected_queues_nothing_then_recovers():
    c = FakeClient()
    n = mk(c)
    n.send(AlertBatch((truck(1),)))
    c.do_disconnect()
    with pytest.raises(AlertError):
        n.send(AlertBatch((truck(2),)))
    assert [x[0] for x in c.calls].count("publish") == 1
    c.do_connect()
    n.send(AlertBatch((truck(3),)))
    assert [x[0] for x in c.calls].count("publish") == 2
    assert [x[0] for x in c.calls].count("loop_start") == 1


def test_mqtt_test_batch():
    c = FakeClient()
    mk(c).send(AlertBatch((), is_test=True))
    assert c.calls[-1][2] == {"api": 1, "type": "alert.test"}


def test_mqtt_publish_failure_raises():
    c = FakeClient()
    c.ack = False  # connected but publish rejected (rc != 0)
    with pytest.raises(AlertError):
        mk(c).send(AlertBatch((truck(),)))


def test_mqtt_close_disconnects_first_and_is_idempotent():
    c = FakeClient()
    n = mk(c)
    n.send(AlertBatch((truck(),)))
    n.close()
    n.close()
    kinds = [x[0] for x in c.calls]
    assert kinds.count("disconnect") == 1 and kinds.count("loop_stop") == 1
    # paho keeps looping while QoS-1 messages are unacked unless it is disconnected first
    assert kinds.index("disconnect") < kinds.index("loop_stop")
    with pytest.raises(AlertError, match="closed"):
        n.send(AlertBatch((truck(),)))
    assert [x[0] for x in c.calls].count("loop_start") == 1  # no new client after close


class HalfOpenBroker:
    """Loopback MQTT 'broker' on an ephemeral port: CONNACK, then it swallows
    everything and never sends a PUBACK (a half-open link). Hard timeouts throughout."""

    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self._srv.settimeout(10)
        self.port = self._srv.getsockname()[1]
        self.got_publish = threading.Event()
        self._conns: list[socket.socket] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        self._conns.append(conn)
        conn.settimeout(10)
        try:
            conn.recv(4096)  # CONNECT
            conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))  # CONNACK: accepted
            while conn.recv(4096):  # PUBLISH (and anything else): never acknowledged
                self.got_publish.set()
        except OSError:
            return

    def close(self) -> None:
        for conn in self._conns:
            conn.close()
        self._srv.close()
        self._thread.join(5)


def timed(fn, limit: float = 10.0) -> float:
    """Seconds fn() took in a helper thread; inf if it was still running after `limit`."""
    done = threading.Event()

    def run():
        fn()
        done.set()

    t0 = time.monotonic()
    threading.Thread(target=run, daemon=True).start()
    return time.monotonic() - t0 if done.wait(limit) else float("inf")


def test_mqtt_close_is_prompt_on_half_open_broker(monkeypatch):
    monkeypatch.setattr(mqtt_mod, "_PUBLISH_TIMEOUT_S", 0.5)
    broker = HalfOpenBroker()
    n = MqttNotifier("127.0.0.1", broker.port, connect_timeout_s=5.0)
    try:
        with pytest.raises(AlertError, match="not acknowledged"):
            n.send(AlertBatch((truck(),)))
        assert timed(n.close) < 2.0
    finally:
        broker.close()


def test_mqtt_close_does_not_wait_for_a_send_in_progress(monkeypatch):
    monkeypatch.setattr(mqtt_mod, "_PUBLISH_TIMEOUT_S", 3.0)
    broker = HalfOpenBroker()
    n = MqttNotifier("127.0.0.1", broker.port, connect_timeout_s=5.0)
    errors: list[BaseException] = []

    def send():
        try:
            n.send(AlertBatch((truck(),)))
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)

    sender = threading.Thread(target=send, daemon=True)
    sender.start()
    try:
        assert broker.got_publish.wait(5)  # send() now holds the lock, waiting for a PUBACK
        assert timed(n.close) < 2.0
        sender.join(6)
        assert not sender.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], AlertError)
    finally:
        broker.close()


def test_real_paho_client_constructs():
    import paho.mqtt.client as pm

    c = pm.Client(callback_api_version=pm.CallbackAPIVersion.VERSION2)
    c.max_queued_messages_set(100)
    assert c.is_connected() is False


def test_telegram_caption_has_map_link(tmp_path):
    seen, handler = recorder()
    tg = make_tg(tmp_path, handler)
    ok = {**truck(), "lat": 48.1234567, "lon": 11.5, "geo_status": "ok"}
    tg.send(AlertBatch((ok,)))
    (req,) = seen
    text = json.loads(req.content)["text"]
    assert ("https://www.openstreetmap.org/?mlat=48.123457&mlon=11.500000"
            "#map=17/48.123457/11.500000") in text
    for status in ("unreliable", "unknown"):
        seen.clear()
        tg.send(AlertBatch(({**truck(), "lat": 1.0, "lon": 2.0, "geo_status": status},)))
        assert "openstreetmap" not in json.loads(seen[0].content)["text"]
    seen.clear()
    tg.send(AlertBatch(({**truck(), "lat": None, "lon": None, "geo_status": "ok"},)))
    assert "openstreetmap" not in json.loads(seen[0].content)["text"]
