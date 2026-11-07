"""MQTT notifier: one QoS-1 JSON message per truck, lazy connect."""

from __future__ import annotations

import json
import threading
from typing import Callable

from engine.alerts.core import AlertBatch, AlertError

_PUBLISH_TIMEOUT_S = 5.0
_CLOSE_LOCK_S = 0.5  # close() waits at most this long for a send() in progress
_CLOSE_JOIN_S = 1.5  # ... and this long for paho's network thread to stop


def _default_factory(**kwargs):
    import paho.mqtt.client as mqtt

    return mqtt.Client(**kwargs)


class MqttNotifier:
    """Publishes one QoS-1 JSON message per truck.

    Delivery is at-least-once: a batch that fails part-way (or whose ack is lost) may be
    republished by the dispatcher's retry, so subscribers can see a truck twice.
    Nothing is published while the broker is disconnected, so nothing piles up in paho.
    close() returns within about 2 s, even on a half-open link or with a send() in
    progress (which then fails); after close() every send() fails.
    """

    name = "mqtt"

    def __init__(
        self,
        host: str,
        port: int = 1883,
        topic: str = "sky-eye/events",
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        client_factory: Callable[..., object] | None = None,
        connect_timeout_s: float = _PUBLISH_TIMEOUT_S,
    ) -> None:
        self._connect_timeout = connect_timeout_s
        self._connected = threading.Event()
        self._host, self._port, self._topic = host, port, topic
        self._username, self._password, self._tls = username, password, tls
        self._factory = client_factory or _default_factory
        self._client = None
        self._closed = False
        self._lock = threading.Lock()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if getattr(reason_code, "is_failure", False):
            self._connected.clear()
        else:
            self._connected.set()

    def _on_disconnect(self, client, userdata, *args) -> None:
        self._connected.clear()

    def _ensure(self):
        if self._closed:
            raise AlertError("mqtt notifier is closed")
        if self._client is None:
            from paho.mqtt.enums import CallbackAPIVersion

            client = self._factory(callback_api_version=CallbackAPIVersion.VERSION2)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.max_queued_messages_set(100)
            if self._username is not None:
                client.username_pw_set(self._username, self._password)
            if self._tls:
                client.tls_set()
            client.connect_async(self._host, self._port)
            client.loop_start()
            self._client = client
        return self._client

    def send(self, batch: AlertBatch) -> None:
        if batch.is_test:
            messages = [{"api": 1, "type": "alert.test"}]
        else:
            messages = [{"api": 1, "type": "truck.new", "truck": t} for t in batch.trucks]
        with self._lock:
            client = self._ensure()
            for msg in messages:
                if self._closed:
                    raise AlertError("mqtt notifier is closed")
                if not self._connected.wait(self._connect_timeout):
                    raise AlertError("mqtt broker not connected")
                try:
                    info = client.publish(self._topic, json.dumps(msg), qos=1)
                    info.wait_for_publish(timeout=_PUBLISH_TIMEOUT_S)
                    ok = info.is_published()
                except Exception as exc:
                    raise AlertError(f"mqtt publish failed: {type(exc).__name__}") from None
                if not ok:
                    raise AlertError("mqtt publish not acknowledged (broker unreachable?)")

    def close(self) -> None:
        """Disconnect, then stop paho's network thread; idempotent. Does not wait
        for a send() in progress beyond _CLOSE_LOCK_S, nor for paho beyond
        _CLOSE_JOIN_S (a thread stuck in a TCP connect finishes on its own)."""
        locked = self._lock.acquire(timeout=_CLOSE_LOCK_S)
        try:
            client, self._client = self._client, None
            self._closed = True
            self._connected.clear()
        finally:
            if locked:
                self._lock.release()
        if client is None:
            return
        # disconnect first: with loop_stop() first, paho keeps its loop running for as
        # long as QoS-1 messages are unacknowledged (minutes on a half-open link)
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - closing anyway
            pass
        stopper = threading.Thread(
            target=_quiet(client.loop_stop), name="mqtt-close", daemon=True
        )
        stopper.start()
        stopper.join(_CLOSE_JOIN_S)


def _quiet(fn: Callable[[], object]) -> Callable[[], None]:
    def run() -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 - closing anyway
            pass

    return run
