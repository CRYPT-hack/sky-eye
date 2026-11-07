"""Telegram notifier: sendPhoto for a single truck with a crop, else sendMessage."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

import httpx

from engine.alerts._fmt import local_hms, score_pct
from engine.alerts.core import AlertBatch, AlertError

__all__ = ["AlertError", "TelegramNotifier"]

_BASE = "https://api.telegram.org"


def _plain_relative(rel: str) -> bool:
    """True for a plain relative path: no drive, UNC share, root, ".." part or NUL.
    Checked as text before anything touches the disk (resolving a UNC path would
    reach out to the network share). Both / and \\ count as separators."""
    if not rel or "\x00" in rel:
        return False
    win = PureWindowsPath(rel)
    if win.drive or win.root or rel.startswith(("/", "\\")):
        return False
    return ".." not in win.parts


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        media_root: Path,
        dashboard_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token
        self._chat_id = str(chat_id)
        self._media_root = Path(media_root)
        self._dashboard = dashboard_url.rstrip("/") if dashboard_url else None
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=10.0)

    def close(self) -> None:
        """Close the HTTP client if this notifier created it; idempotent."""
        if self._owns_client:
            self._client.close()

    # -- helpers -----------------------------------------------------------
    def _redact(self, text: str) -> str:
        if self._token:
            text = text.replace(f"bot{self._token}", "bot***").replace(self._token, "***")
        return text

    def _crop_path(self, truck: dict) -> Path | None:
        url = truck.get("crop_url")
        if not isinstance(url, str) or not url.startswith("/media/"):
            return None
        rel = unquote(urlsplit(url).path[len("/media/"):])
        if not _plain_relative(rel):
            return None
        try:
            root = self._media_root.resolve()
            path = (root / rel).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
        except (OSError, ValueError):  # e.g. an embedded NUL from %00
            return None
        return path

    def _caption(self, t: dict) -> str:
        lines = [
            f"\U0001F69A New truck #{t.get('id')}",
            f"Seen at {local_hms(t.get('last_seen'))}, score {score_pct(t)}",
        ]
        lat, lon = t.get("lat"), t.get("lon")
        if t.get("geo_status") == "ok" and lat is not None and lon is not None:
            lines.append(
                f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}"
                f"#map=17/{lat:.6f}/{lon:.6f}"
            )
        if self._dashboard:
            lines.append(f"{self._dashboard}/#/trucks/{t.get('id')}")
        return "\n".join(lines)

    def _text(self, batch: AlertBatch) -> str:
        if batch.is_test:
            return "Test alert from sky-eye ✅"
        if len(batch.trucks) == 1:
            return self._caption(batch.trucks[0])
        lines = [f"\U0001F69A {batch.title()}"]
        for t in batch.trucks:
            lines.append(
                f"#{t.get('id')} at {local_hms(t.get('last_seen'))}, score {score_pct(t)}"
            )
        if self._dashboard:
            lines.append(f"{self._dashboard}/")
        return "\n".join(lines)

    # -- send --------------------------------------------------------------
    def send(self, batch: AlertBatch) -> None:
        crop = None
        data = b""
        if not batch.is_test and len(batch.trucks) == 1:
            crop = self._crop_path(batch.trucks[0])
        if crop is not None:
            try:
                data = crop.read_bytes()
            except OSError:
                crop = None
        if crop is not None:
            self._post(
                "sendPhoto",
                data={"chat_id": self._chat_id, "caption": self._caption(batch.trucks[0])},
                files={"photo": (crop.name, data, "image/jpeg")},
            )
        else:
            self._post("sendMessage", json={"chat_id": self._chat_id, "text": self._text(batch)})

    def _post(self, method: str, **kwargs) -> None:
        url = f"{_BASE}/bot{self._token}/{method}"
        try:
            resp = self._client.post(url, **kwargs)
        except Exception as exc:
            msg = self._redact(f"{type(exc).__name__}: {exc}")
            raise AlertError(f"telegram {method} request failed: {msg}") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code // 100 != 2 or not isinstance(body, dict) or not body.get("ok"):
            desc = body.get("description") if isinstance(body, dict) else None
            detail = self._redact(str(desc or resp.text[:200]))
            raise AlertError(f"telegram {method} failed (HTTP {resp.status_code}): {detail}")
