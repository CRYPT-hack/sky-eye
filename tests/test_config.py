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
