"""Wheel installs (uvx --from git+...) must carry the catalogue and seed the user data dir once."""

import tomllib
from pathlib import Path

from ema_rwe import config

ROOT = Path(__file__).resolve().parents[1]


def test_wheel_bundles_every_seeded_file():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        include = tomllib.load(fh)["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    for name in config.SEEDED_FILES:
        assert include[f"data/{name}"] == f"ema_rwe/data/{name}"
        assert (ROOT / "data" / name).is_file()


def test_checkout_uses_repo_data_dir():
    assert config.default_db_path() == ROOT / "data" / "ema.sqlite3"
    assert config.default_terminology_path() == ROOT / "data" / "terminology.json"


def test_wheel_install_seeds_user_data_dir_once(tmp_path, monkeypatch):
    bundled, user_dir = tmp_path / "bundled", tmp_path / "user"
    bundled.mkdir()
    for name in config.SEEDED_FILES:
        (bundled / name).write_text(f"bundled {name}")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "no-checkout")
    monkeypatch.setattr(config, "BUNDLED_DATA", bundled)
    monkeypatch.setattr(config, "user_data_path", lambda _app: user_dir)

    assert config.default_db_path() == user_dir / "ema.sqlite3"
    assert config.default_terminology_path() == user_dir / "terminology.json"
    assert (user_dir / "ema-medicines.json").read_text() == "bundled ema-medicines.json"

    (user_dir / "ema.sqlite3").write_text("user edited")
    config.default_data_dir()
    assert (user_dir / "ema.sqlite3").read_text() == "user edited"
