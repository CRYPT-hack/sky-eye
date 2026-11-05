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
