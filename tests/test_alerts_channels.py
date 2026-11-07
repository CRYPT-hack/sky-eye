import json
import logging
from datetime import datetime

import httpx
import pytest

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


# ---------------- mqtt ----------------
class FakeInfo:
    def __init__(self, published=True):
        self._p = published

    def wait_for_publish(self, timeout=None):
        pass

    def is_published(self):
        return self._p


class FakeClient:
    def __init__(self, **kw):
        self.kw = kw
        self.calls = []
        self.published = True

    def username_pw_set(self, u, p=None):
        self.calls.append(("auth", u, p))

    def tls_set(self):
        self.calls.append(("tls",))

    def connect_async(self, host, port):
        self.calls.append(("connect", host, port))

    def loop_start(self):
        self.calls.append(("loop_start",))

    def loop_stop(self):
        self.calls.append(("loop_stop",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    def publish(self, topic, payload, qos=0, retain=False):
        self.calls.append(("publish", topic, json.loads(payload), qos))
        return FakeInfo(self.published)


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
    assert ("auth", "u", "pw") in c.calls and ("tls",) in c.calls
    pubs = [x for x in c.calls if x[0] == "publish"]
    assert [p[1] for p in pubs] == ["t/e"] * 3 and all(p[3] == 1 for p in pubs)
    assert pubs[0][2] == {"api": 1, "type": "truck.new", "truck": truck(1)}
    n.close()
    n.close()
    assert [x[0] for x in c.calls].count("disconnect") == 1


def test_mqtt_test_batch():
    c = FakeClient()
    n = MqttNotifier("b", client_factory=lambda **kw: c)
    n.send(AlertBatch((), is_test=True))
    assert c.calls[-1][2] == {"api": 1, "type": "alert.test"}


def test_mqtt_publish_failure_raises():
    c = FakeClient()
    c.published = False
    n = MqttNotifier("b", client_factory=lambda **kw: c)
    with pytest.raises(AlertError):
        n.send(AlertBatch((truck(),)))
