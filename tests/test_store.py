import json
import shutil
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


def _archive(tmp_path: Path, name: str = "ScarletHead.zip", stem: str = "scarlet") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"{stem}.{ext}", "x")
    return archive


def test_sanitize_mod_name():
    assert sanitize_mod_name("Seamless Scarlet Head v2!") == "Seamless Scarlet Head v2"
    assert sanitize_mod_name("../evil") == "evil"
    assert sanitize_mod_name("...") == "mod"


def test_import_enable_disable_cycle(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    repo_pak = tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.pak"
    assert repo_pak.is_file()

    [mod] = store.list_mods("1", game)
    assert mod["name"] == "Scarlet"
    assert mod["state"] == "disabled"

    store.set_enabled("1", game, "Scarlet", True)
    assert (game.mods_dir / "scarlet.pak").is_file()
    # Copy semantics: the repository keeps the full copy while enabled.
    assert repo_pak.is_file()
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    store.set_enabled("1", game, "Scarlet", False)
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert repo_pak.is_file()
    assert store.list_mods("1", game)[0]["state"] == "disabled"


def test_partial_state_detected_and_repaired_by_reenabling(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    store.set_enabled("1", game, "Scarlet", True)
    (game.mods_dir / "scarlet.ucas").unlink()

    assert store.list_mods("1", game)[0]["state"] == "partial"
    # Enable is idempotent repair: the repository copy is re-copied in.
    store.set_enabled("1", game, "Scarlet", True)
    assert store.list_mods("1", game)[0]["state"] == "enabled"


def test_enable_overwrites_stale_copy(tmp_path):
    """Import-time checks guarantee same-named files in ~mods are ModDock's
    own, so enable may overwrite freely — that is what makes it idempotent."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    game.mods_dir.mkdir(parents=True)
    (game.mods_dir / "scarlet.pak").write_bytes(b"stale")

    store.set_enabled("1", game, "Scarlet", True)
    assert (game.mods_dir / "scarlet.pak").read_bytes() == b"x"
    assert store.list_mods("1", game)[0]["state"] == "enabled"


def test_enable_with_missing_store_copy_raises(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    (tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.ucas").unlink()

    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Scarlet", True)
    assert "scarlet.ucas" in str(excinfo.value)
    # Nothing was half-copied before the check.
    assert not (game.mods_dir / "scarlet.pak").exists()


def test_duplicate_mod_name_rejected(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    with pytest.raises(StoreError):
        store.import_mod("1", game, "Scarlet", _archive(tmp_path, "other.zip"))


def test_file_basename_conflict_between_mods_rejected(tmp_path):
    """Two mods for one game may not claim the same internal file name: the
    manifest's basenames are what delete/toggle act on, so a collision would
    make one mod destroy the other's files."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet v1", _archive(tmp_path))

    with pytest.raises(StoreError) as excinfo:
        store.import_mod("1", game, "Scarlet v2", _archive(tmp_path, "other.zip"))
    message = str(excinfo.value)
    assert "scarlet.pak" in message
    assert "Scarlet v1" in message

    # The rejected mod leaves nothing behind: no repo dir, no manifest entry.
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet v2").exists()
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet v1"]

    # A mod with distinct file names still imports fine.
    store.import_mod("1", game, "Other", _archive(tmp_path, "o.zip", stem="other"))
    assert len(store.list_mods("1", game)) == 2


def test_uninstall_then_reinstall_keeps_mods_disabled(tmp_path):
    """Steam uninstalling a game removes its install dir, ~mods included.
    Under copy semantics that merely disables everything: the repository is
    intact, mods list as disabled, and can be re-enabled after a reinstall."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    store.set_enabled("1", game, "Scarlet", True)

    shutil.rmtree(tmp_path / "game")  # Steam uninstall
    assert store.list_mods("1", None)[0]["state"] == "disabled"

    reinstalled = _game(tmp_path)  # Steam reinstall (fresh dirs)
    assert store.list_mods("1", reinstalled)[0]["state"] == "disabled"
    store.set_enabled("1", reinstalled, "Scarlet", True)
    assert store.list_mods("1", reinstalled)[0]["state"] == "enabled"


def test_delete_without_game_cleans_repository(tmp_path):
    """With copy semantics a delete needs no ~mods access: the game dir (and
    any enabled copies in it) is gone with the game, so deleting while the
    game is uninstalled just clears the repository and the manifest."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    shutil.rmtree(tmp_path / "game")

    store.delete_mod("1", None, "Scarlet")
    assert store.list_mods("1", None) == []
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


def test_set_enabled_wraps_os_error(tmp_path, monkeypatch):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))

    def boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("moddock.store.shutil.copy2", boom)
    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Scarlet", True)
    assert "No space left on device" in str(excinfo.value)


def test_delete_mod_wraps_os_error(tmp_path, monkeypatch):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))

    real_unlink = Path.unlink

    def boom(self, *args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "unlink", boom)
    with pytest.raises(StoreError) as excinfo:
        store.delete_mod("1", game, "Scarlet")
    assert "Permission denied" in str(excinfo.value)
    monkeypatch.setattr(Path, "unlink", real_unlink)
    # The manifest entry survives a failed delete so the user can retry.
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet"]


def test_delete_enabled_mod_removes_files_everywhere(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path))
    store.set_enabled("1", game, "Scarlet", True)
    store.delete_mod("1", game, "Scarlet")
    assert store.list_mods("1", game) == []
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


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


def test_import_rejects_file_already_unmanaged_in_mods_dir(tmp_path):
    """A hand-installed file in ~mods must never become ModDock-managed.

    Otherwise enable would overwrite, and delete would unlink, a file ModDock
    never put there.
    """
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    game.mods_dir.mkdir(parents=True)
    (game.mods_dir / "scarlet.pak").write_bytes(b"hand-installed")

    with pytest.raises(StoreError) as exc:
        store.import_mod("1", game, "Scarlet", _archive(tmp_path))

    message = str(exc.value)
    assert "scarlet.pak" in message
    assert "~mods" in message
    # Nothing half-imported: no repo directory, no manifest entry.
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()
    assert not (tmp_path / "base" / "manifest" / "1.json").exists()
    assert (game.mods_dir / "scarlet.pak").read_bytes() == b"hand-installed"
