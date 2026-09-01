import json
import zipfile
from pathlib import Path

import pytest

from moddock.adapters.unreal import MODS_DIR_NAME, UEGameInfo
from moddock.store import ModStore, StoreError, sanitize_mod_name


def _game(tmp_path: Path) -> UEGameInfo:
    paks = tmp_path / "game" / "SB" / "Content" / "Paks"
    paks.mkdir(parents=True)
    return UEGameInfo(
        project_name="SB",
        paks_dir=paks,
        mods_dir=paks / MODS_DIR_NAME,
        is_iostore=True,
        has_shipping_exe=True,
    )


def _archive(tmp_path: Path, name: str = "ScarletHead.zip") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"scarlet.{ext}", "x")
    return archive


def test_sanitize_mod_name():
    assert sanitize_mod_name("Seamless Scarlet Head v2!") == "Seamless Scarlet Head v2"
    assert sanitize_mod_name("../evil") == "evil"
    assert sanitize_mod_name("...") == "mod"


def test_import_enable_disable_cycle(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))

    [mod] = store.list_mods("1", game)
    assert mod["name"] == "Scarlet"
    assert mod["state"] == "disabled"

    store.set_enabled("1", game, "Scarlet", True)
    assert (game.mods_dir / "scarlet.pak").is_file()
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    store.set_enabled("1", game, "Scarlet", False)
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert store.list_mods("1", game)[0]["state"] == "disabled"


def test_partial_state_detected_and_repairable(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    store.set_enabled("1", game, "Scarlet", True)
    (game.mods_dir / "scarlet.ucas").unlink()

    assert store.list_mods("1", game)[0]["state"] == "partial"
    store.set_enabled("1", game, "Scarlet", False)  # repair by disabling
    assert store.list_mods("1", game)[0]["state"] == "partial"  # ucas is gone for good


def test_enable_collision_raises(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    game.mods_dir.mkdir(parents=True)
    (game.mods_dir / "scarlet.pak").write_bytes(b"other")
    with pytest.raises(StoreError):
        store.set_enabled("1", game, "Scarlet", True)


def test_duplicate_mod_name_rejected(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    with pytest.raises(StoreError):
        store.import_mod("1", game, "Scarlet", _archive(tmp_path, "other.zip"))


def test_delete_enabled_mod_removes_files_everywhere(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    store.set_enabled("1", game, "Scarlet", True)
    store.delete_mod("1", game, "Scarlet")
    assert store.list_mods("1", game) == []
    assert not (game.mods_dir / "scarlet.pak").exists()


def test_manifest_written(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    manifest = json.loads((tmp_path / "base" / "manifest" / "1.json").read_text())
    assert "Scarlet" in manifest["mods"]
    assert sorted(manifest["mods"]["Scarlet"]["files"]) == [
        "scarlet.pak",
        "scarlet.ucas",
        "scarlet.utoc",
    ]


def test_cross_partition_repo_fallback(tmp_path, monkeypatch):
    """When the game sits on another filesystem, the repo must be placed
    under <library-root>/.moddock/<appid> on that same filesystem."""
    lib = tmp_path / "sdcard"
    paks = lib / "steamapps" / "common" / "Game" / "SB" / "Content" / "Paks"
    paks.mkdir(parents=True)
    game = UEGameInfo(
        project_name="SB",
        paks_dir=paks,
        mods_dir=paks / MODS_DIR_NAME,
        is_iostore=False,
        has_shipping_exe=True,
    )
    store = ModStore(tmp_path / "base")

    real_stat_dev = {str(tmp_path / "base"): 1, str(paks): 2}

    def fake_dev(path: Path) -> int:
        for prefix, dev in real_stat_dev.items():
            if str(path).startswith(prefix):
                return dev
        return 1

    monkeypatch.setattr("moddock.store._device_of", fake_dev)
    store.import_mod("7", game, "M", _archive(tmp_path))
    assert (lib / ".moddock" / "7" / "M").is_dir()
