"""MQTT notifier: one QoS-1 JSON message per truck, lazy connect."""

from __future__ import annotations

import json
import threading
from typing import Callable

from engine.alerts.core import AlertBatch, AlertError

_PUBLISH_TIMEOUT_S = 5.0


def _default_factory(**kwargs):
    import paho.mqtt.client as mqtt

    return mqtt.Client(**kwargs)


class MqttNotifier:
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
    ) -> None:
        self._host, self._port, self._topic = host, port, topic
        self._username, self._password, self._tls = username, password, tls
        self._factory = client_factory or _default_factory
        self._client = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._client is None:
            from paho.mqtt.enums import CallbackAPIVersion

            client = self._factory(callback_api_version=CallbackAPIVersion.VERSION2)
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
                try:
                    info = client.publish(self._topic, json.dumps(msg), qos=1)
                    info.wait_for_publish(timeout=_PUBLISH_TIMEOUT_S)
                    ok = info.is_published()
                except Exception as exc:
                    raise AlertError(f"mqtt publish failed: {type(exc).__name__}") from None
                if not ok:
                    raise AlertError("mqtt publish not acknowledged (broker unreachable?)")

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
