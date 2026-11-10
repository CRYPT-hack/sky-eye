from pathlib import Path

import pytest

from engine.config import ConfigError, load_config


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_defaults_match_spec(tmp_path):
    cfg = load_config(_write(tmp_path, "{}"))
    assert cfg.detector.confidence == 0.4
    assert cfg.tracker.confirm_hits == 3 and cfg.tracker.confirm_window == 5
    assert cfg.tracker.lost_timeout_s == 2.0
    assert cfg.evidence.clip_before_s == 3 and cfg.evidence.clip_after_s == 5
    assert cfg.api.host == "0.0.0.0" and cfg.api.port == 8000


def test_empty_file_is_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.detector.size == "small"


def test_bad_value_names_field(tmp_path):
    p = _write(tmp_path, "detector:\n  confidence: 1.5\n")
    with pytest.raises(ConfigError, match=r"detector\.confidence"):
        load_config(p)


def test_unknown_key_rejected(tmp_path):
    p = _write(tmp_path, "detector:\n  colour: red\n")
    with pytest.raises(ConfigError, match=r"detector\.colour"):
        load_config(p)


def test_confirm_hits_cannot_exceed_window(tmp_path):
    p = _write(tmp_path, "tracker:\n  confirm_hits: 6\n  confirm_window: 5\n")
    with pytest.raises(ConfigError, match=r"tracker"):
        load_config(p)


def test_file_source_requires_existing_path(tmp_path):
    p = _write(tmp_path, "source:\n  type: file\n  path: missing.mp4\n")
    with pytest.raises(ConfigError, match=r"source"):
        load_config(p)


def test_relative_paths_resolve_against_config_dir(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    p = _write(tmp_path, "source:\n  path: v.mp4\ndetector:\n  weights: w.pt\n")
    cfg = load_config(p)
    assert cfg.source.path == tmp_path / "v.mp4"
    assert cfg.detector.weights == tmp_path / "w.pt"
    assert cfg.evidence.data_dir == tmp_path / "data"


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_token_mode_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYEYE_API_TOKEN", raising=False)
    p = _write(tmp_path, "api:\n  auth:\n    mode: token\n")
    with pytest.raises(ConfigError, match="SKYEYE_API_TOKEN"):
        load_config(p)


def test_token_mode_ok_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "secret")
    cfg = load_config(_write(tmp_path, "api:\n  auth:\n    mode: token\n"))
    assert cfg.api.auth.mode == "token"


def test_example_config_loads(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "drone.mp4").write_bytes(b"x")
    (tmp_path / "config.yaml").write_text((repo / "config.example.yaml").read_text())
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.detector.classes == ["truck"]
    assert cfg.source.path == tmp_path / "samples" / "drone.mp4"


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("tracker", "confirm_hits", 0),
        ("tracker", "confirm_window", 0),
        ("tracker", "lost_timeout_s", 0),
        ("tracker", "lost_timeout_s", -1),
        ("evidence", "clip_before_s", -0.5),
        ("evidence", "clip_after_s", 0),
        ("evidence", "snapshot_window_s", -1),
        ("evidence", "max_disk_gb", 0),
        ("api", "port", 0),
        ("api", "port", 65536),
        ("api", "stream_fps", 0),
        ("api", "stream_max_width", 15),
        ("source", "device", -1),
    ],
)
def test_out_of_range_value_refused(tmp_path, section, key, value):
    p = _write(tmp_path, f"{section}:\n  {key}: {value}\n")
    with pytest.raises(ConfigError, match=rf"{section}\.{key}"):
        load_config(p)


def test_boundary_values_accepted(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            "tracker:\n  confirm_hits: 1\n  confirm_window: 1\n"
            "evidence:\n  clip_before_s: 0\n  snapshot_window_s: 0\n"
            "api:\n  port: 65535\n  stream_fps: 1\n  stream_max_width: 16\n"
            "source:\n  device: 0\n",
        )
    )
    assert cfg.api.port == 65535 and cfg.tracker.confirm_hits == 1


def test_undecodable_config_names_path(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes(b"detector:\n  size: \xff\xfe\x80\n")
    with pytest.raises(ConfigError, match="c.yaml"):
        load_config(p)


def test_unreadable_config_names_path(tmp_path, monkeypatch):
    p = _write(tmp_path, "{}")

    def deny(self, *a, **k):
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(ConfigError, match="c.yaml"):
        load_config(p)


def test_config_with_bom_loads(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes(b"\xef\xbb\xbfdetector:\n  size: nano\n")
    assert load_config(p).detector.size == "nano"


def test_dashboard_dir_default_and_relative(tmp_path):
    assert load_config(_write(tmp_path, "{}")).api.dashboard_dir is None
    cfg = load_config(_write(tmp_path, "api:\n  dashboard_dir: web\n"))
    assert cfg.api.dashboard_dir == tmp_path / "web"


def test_source_config_stream_and_go2rtc(tmp_path):
    from engine.config import SourceConfig
    from engine.sources import StreamSource, build_source

    with pytest.raises(ConfigError, match=r"source\.url: required for type: stream"):
        load_config(_write(tmp_path, "source:\n  type: stream\n"))
    with pytest.raises(ConfigError, match=r"source\.url: .*scheme"):
        load_config(_write(tmp_path, "source:\n  type: stream\n  url: drone-feed\n"))
    with pytest.raises(ConfigError, match=r"source\.url: .*'ftp'"):
        load_config(_write(tmp_path, "source:\n  type: stream\n  url: ftp://cam/live\n"))
    with pytest.raises(ConfigError, match=r"source\.url") as err:  # typo in the scheme
        load_config(_write(tmp_path, "source:\n  type: stream\n  url: rtps://pilot:s3cret@cam/live\n"))
    assert "s3cret" not in str(err.value)  # the URL (it may hold a password) is not echoed
    with pytest.raises(ConfigError, match=r"source\.go2rtc\.name: required for type: go2rtc"):
        load_config(_write(tmp_path, "source:\n  type: go2rtc\n"))
    with pytest.raises(ConfigError, match=r"source\.go2rtc\.port"):
        load_config(_write(tmp_path, "source:\n  type: go2rtc\n  go2rtc:\n    port: 1\n"))

    for scheme in ("rtsp", "rtsps", "rtmp", "rtmps", "udp", "http", "https"):
        assert SourceConfig(type="stream", url=f"{scheme}://10.0.0.5:9000/x").url
    cfg = load_config(_write(tmp_path, "source:\n  type: stream\n  url: udp://@:9000\n"))
    assert cfg.source.url == "udp://@:9000"
    src = build_source(cfg.source)
    assert isinstance(src, StreamSource) and src.url == "udp://@:9000"

    cfg = load_config(_write(tmp_path, "source:\n  type: go2rtc\n  go2rtc:\n    name: drone\n"))
    assert cfg.source.go2rtc.rtsp_base == "rtsp://127.0.0.1:8554"
    src = build_source(cfg.source)
    assert isinstance(src, StreamSource) and src.url == "rtsp://127.0.0.1:8554/drone"
    cfg = load_config(
        _write(tmp_path, "source:\n  type: go2rtc\n  go2rtc:\n    rtsp_base: rtsp://10.0.0.2:8554/\n    name: dji\n")
    )
    assert build_source(cfg.source).url == "rtsp://10.0.0.2:8554/dji"
    # a file source ignores the stream fields
    assert load_config(_write(tmp_path, "{}")).source.url is None
    # build_source checks too, for a config that skipped validation
    with pytest.raises(ConfigError, match=r"source\.go2rtc\.name"):
        build_source(SourceConfig.model_construct(type="go2rtc"))


def test_stream_srt_points_to_a_relay(tmp_path):
    # the PyAV wheels have no libsrt
    with pytest.raises(
        ConfigError,
        match=r"source\.url: SRT is not supported directly; "
        r"let a relay such as MediaMTX ingest it and read its RTSP output",
    ):
        load_config(_write(tmp_path, "source:\n  type: stream\n  url: srt://10.0.0.5:9000\n"))


@pytest.mark.parametrize("password", ["pa#ss", "pa/ss", "pa?ss"])
def test_stream_url_reserved_chars_in_password_refused(tmp_path, password):
    # urlsplit and FFmpeg would cut the authority at the raw character
    url = f"rtsp://pilot:{password}@cam/live"
    p = _write(tmp_path, f"source:\n  type: stream\n  url: '{url}'\n")
    with pytest.raises(ConfigError, match=r"source\.url: .*percent-encode") as err:
        load_config(p)
    assert password not in str(err.value) and "pilot" not in str(err.value)
    p = _write(
        tmp_path,
        "source:\n  type: go2rtc\n  go2rtc:\n    name: drone\n"
        f"    rtsp_base: 'rtsp://admin:{password}@127.0.0.1:8554'\n",
    )
    with pytest.raises(ConfigError, match=r"source\.go2rtc\.rtsp_base: .*percent-encode") as err:
        load_config(p)
    assert password not in str(err.value)


def test_stream_url_encoded_or_raw_at_in_password_accepted(tmp_path):
    cfg = load_config(
        _write(tmp_path, "source:\n  type: stream\n  url: rtsp://pilot:pa%23ss@cam/live\n")
    )
    assert cfg.source.url == "rtsp://pilot:pa%23ss@cam/live"
    # a raw @ is fine: urlsplit and FFmpeg both split the authority at the last one
    cfg = load_config(_write(tmp_path, "source:\n  type: stream\n  url: rtsp://u:p@ss@cam/live\n"))
    assert cfg.source.url == "rtsp://u:p@ss@cam/live"


def test_alerts_config_defaults_and_bounds(tmp_path):
    cfg = load_config(_write(tmp_path, "{}"))
    a = cfg.alerts
    assert a.cooldown_s == 10 and a.desktop is True
    assert a.telegram.enabled is False and a.telegram.dashboard_url is None
    assert a.mqtt.enabled is False and a.mqtt.host == "127.0.0.1" and a.mqtt.port == 1883
    assert a.mqtt.topic == "sky-eye/events" and a.mqtt.username is None and a.mqtt.tls is False
    for bad in ("alerts:\n  cooldown_s: 0", "alerts:\n  mqtt:\n    port: 0",
                "alerts:\n  mqtt:\n    port: 65536", "alerts:\n  sms: true"):
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, bad))
    ok = load_config(_write(tmp_path, "alerts:\n  mqtt:\n    port: 65535"))
    assert ok.alerts.mqtt.port == 65535


def test_telegram_enabled_requires_env(tmp_path, monkeypatch):
    text = "alerts:\n  telegram:\n    enabled: true"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(_write(tmp_path, text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        load_config(_write(tmp_path, text))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert load_config(_write(tmp_path, text)).alerts.telegram.enabled is True


# --- telemetry / camera / map tiles ---

def test_telemetry_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, "{}"))
    assert cfg.telemetry.type == "none"
    assert cfg.telemetry.connection == "udpin:0.0.0.0:14550"
    assert cfg.telemetry.srt_path is None
    assert cfg.telemetry.max_age_s == 0.5 and cfg.telemetry.stale_after_s == 2.0
    assert cfg.camera.hfov_deg == 82.0
    assert cfg.camera.pitch_deg is None
    assert cfg.api.map_tile_url == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


@pytest.mark.parametrize(
    "body, field",
    [
        ("telemetry:\n  max_age_s: 0\n", r"telemetry\.max_age_s"),
        ("telemetry:\n  stale_after_s: -1\n", r"telemetry\.stale_after_s"),
        ("camera:\n  hfov_deg: 5\n", r"camera\.hfov_deg"),
        ("camera:\n  hfov_deg: 171\n", r"camera\.hfov_deg"),
        ("telemetry:\n  type: gps\n", r"telemetry\.type"),
        ("camera:\n  pitch_deg: -91\n", r"camera\.pitch_deg"),
        ("camera:\n  pitch_deg: 1\n", r"camera\.pitch_deg"),
    ],
)
def test_telemetry_camera_bounds(tmp_path, body, field):
    with pytest.raises(ConfigError, match=field):
        load_config(_write(tmp_path, body))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://t/{z}/{x}/{y}.png",
        "tile.example.com/{z}/{x}/{y}.png",
        "https://t.example.com/{z}/{x}.png",
        "https://t.example.com/{x}/{y}.png",
    ],
)
def test_map_tile_url_rejected(tmp_path, url):
    with pytest.raises(ConfigError, match=r"api\.map_tile_url"):
        load_config(_write(tmp_path, f'api:\n  map_tile_url: "{url}"\n'))


@pytest.mark.parametrize("value", [-90, -45.5, 0])
def test_camera_pitch_accepted(tmp_path, value):
    cfg = load_config(_write(tmp_path, f"camera:\n  pitch_deg: {value}\n"))
    assert cfg.camera.pitch_deg == value


def test_map_tile_url_accepts_http(tmp_path):
    cfg = load_config(_write(tmp_path, 'api:\n  map_tile_url: "http://localhost/{z}/{x}/{y}.png"\n'))
    assert cfg.api.map_tile_url.startswith("http://localhost")


def _video(tmp_path: Path, name: str = "flight.mp4") -> Path:
    v = tmp_path / name
    v.write_bytes(b"x")
    return v


def test_dji_srt_requires_file_source(tmp_path):
    p = _write(tmp_path, "source:\n  type: capture\ntelemetry:\n  type: dji_srt\n")
    with pytest.raises(ConfigError, match=r"telemetry.*file"):
        load_config(p)


@pytest.mark.parametrize("suffix", [".SRT", ".srt", ".Srt"])
def test_dji_srt_default_finds_sibling(tmp_path, suffix):
    _video(tmp_path)
    srt = tmp_path / f"flight{suffix}"
    srt.write_text("x")
    p = _write(tmp_path, "source:\n  path: flight.mp4\ntelemetry:\n  type: dji_srt\n")
    cfg = load_config(p)
    assert cfg.telemetry.srt_path is not None
    assert cfg.telemetry.srt_path.name == srt.name


def test_dji_srt_default_missing_names_field(tmp_path):
    _video(tmp_path)
    p = _write(tmp_path, "source:\n  path: flight.mp4\ntelemetry:\n  type: dji_srt\n")
    with pytest.raises(ConfigError, match=r"telemetry\.srt_path"):
        load_config(p)


def test_dji_srt_without_source_path_names_field(tmp_path):
    p = _write(tmp_path, "telemetry:\n  type: dji_srt\n")
    with pytest.raises(ConfigError, match=r"telemetry\.srt_path"):
        load_config(p)


def test_dji_srt_explicit_relative_path_resolves_to_config_dir(tmp_path):
    _video(tmp_path)
    (tmp_path / "other.srt").write_text("x")
    p = _write(
        tmp_path,
        "source:\n  path: flight.mp4\ntelemetry:\n  type: dji_srt\n  srt_path: other.srt\n",
    )
    cfg = load_config(p)
    assert cfg.telemetry.srt_path == tmp_path.resolve() / "other.srt"


def test_dji_srt_explicit_missing_rejected(tmp_path):
    _video(tmp_path)
    p = _write(
        tmp_path,
        "source:\n  path: flight.mp4\ntelemetry:\n  type: dji_srt\n  srt_path: nope.srt\n",
    )
    with pytest.raises(ConfigError, match=r"telemetry\.srt_path"):
        load_config(p)


def test_srt_path_ignored_for_other_types(tmp_path):
    cfg = load_config(_write(tmp_path, "telemetry:\n  type: mavlink\n  srt_path: nope.srt\n"))
    assert cfg.telemetry.type == "mavlink"


@pytest.mark.parametrize("conn", ["tcp:127.0.0.1:5760", "tcpin:0.0.0.0:5760", "ws://x:1", ""])
def test_telemetry_connection_rejects_non_udp_serial(tmp_path, conn):
    p = _write(tmp_path, f"telemetry:\n  type: mavlink\n  connection: '{conn}'\n")
    with pytest.raises(ConfigError, match=r"telemetry\.connection.*UDP"):
        load_config(p)


@pytest.mark.parametrize("ttype", ["none", "dji_srt"])
def test_telemetry_connection_ignored_unless_mavlink(tmp_path, ttype):
    # a leftover link under another type must not stop the engine from starting
    _video(tmp_path)
    (tmp_path / "flight.srt").write_text("x")
    p = _write(tmp_path, "source:\n  path: flight.mp4\n"
               f"telemetry:\n  type: {ttype}\n  connection: 'tcp:127.0.0.1:5760'\n")
    assert load_config(p).telemetry.type == ttype


@pytest.mark.parametrize("conn", ["udpout:10.0.0.2:14550", "udpbcast:192.168.1.255:14550"])
def test_telemetry_connection_rejects_sending_udp(tmp_path, conn):
    p = _write(tmp_path, f"telemetry:\n  type: mavlink\n  connection: '{conn}'\n")
    with pytest.raises(ConfigError, match=r"telemetry\.connection.*only listens"):
        load_config(p)


@pytest.mark.parametrize("conn", ["udpin:0.0.0.0:14550", "COM5,57600", "/dev/ttyACM0"])
def test_telemetry_connection_accepts_udp_serial(tmp_path, conn):
    p = _write(tmp_path, f"telemetry:\n  type: mavlink\n  connection: '{conn}'\n")
    assert load_config(p).telemetry.connection == conn
