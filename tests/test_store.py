import json
import shutil
import zipfile
from pathlib import Path

import pytest

from moddock.adapters.unreal import MODS_DIR_NAME, UEGameInfo, detect_ue_game
from moddock.recipes import BUILTIN_RECIPES, recipe_from_dict
from moddock.store import ModStore, StoreError, sanitize_mod_name

PAK_RECIPE = BUILTIN_RECIPES[0]  # ue-paks-mods


def _game(tmp_path: Path) -> UEGameInfo:
    install = tmp_path / "game"
    (install / "Engine").mkdir(parents=True)
    paks = install / "SB" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "SB-Windows.pak").touch()
    (install / "SB" / "Binaries" / "Win64").mkdir(parents=True)
    return detect_ue_game(install)


def _archive(tmp_path: Path, name: str = "ScarletHead.zip", stem: str = "scarlet") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"{stem}.{ext}", "x")
    return archive


def _combo_recipe():
    return recipe_from_dict(
        {
            "name": "pak + lua",
            "rules": [
                {"match": ["*.pak", "*.utoc", "*.ucas"], "anchor": "paks_dir",
                 "subpath": "~mods"},
                {"match": ["*.lua"], "anchor": "win64_dir",
                 "subpath": "ue4ss/Mods", "mapping": "preserve_tree"},
            ],
        },
        recipe_id="combo",
    )


def test_sanitize_mod_name():
    assert sanitize_mod_name("Seamless Scarlet Head v2!") == "Seamless Scarlet Head v2"
    assert sanitize_mod_name("../evil") == "evil"
    assert sanitize_mod_name("...") == "mod"


def test_import_enable_disable_cycle(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    repo_pak = tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.pak"
    assert repo_pak.is_file()

    [mod] = store.list_mods("1", game)
    assert (mod["name"], mod["state"]) == ("Scarlet", "disabled")
    assert mod["recipe_name"] == "UE ~mods (pak)"

    store.set_enabled("1", game, "Scarlet", True)
    assert (game.mods_dir / "scarlet.pak").is_file()
    assert repo_pak.is_file()  # copy semantics: repo keeps the full copy
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    store.set_enabled("1", game, "Scarlet", False)
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert repo_pak.is_file()
    assert store.list_mods("1", game)[0]["state"] == "disabled"


def test_multi_destination_recipe(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    archive = tmp_path / "combo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"scarlet.{ext}", "x")
        zf.writestr("MyMod/scripts/main.lua", "x")
    store.import_mod("1", game, "Combo", archive, _combo_recipe())
    store.set_enabled("1", game, "Combo", True)

    assert (game.mods_dir / "scarlet.pak").is_file()
    lua = (game.install_dir / "SB/Binaries/Win64/ue4ss/Mods"
           / "MyMod/scripts/main.lua")
    assert lua.is_file()

    store.set_enabled("1", game, "Combo", False)
    assert not lua.exists()
    # Shared directories are never deleted, only our files.
    assert lua.parent.parent.parent.is_dir()


def test_partial_state_detected_and_repaired_by_reenabling(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)
    (game.mods_dir / "scarlet.ucas").unlink()

    assert store.list_mods("1", game)[0]["state"] == "partial"
    store.set_enabled("1", game, "Scarlet", True)
    assert store.list_mods("1", game)[0]["state"] == "enabled"


def test_enable_with_missing_store_copy_raises(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    (tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.ucas").unlink()

    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Scarlet", True)
    assert "scarlet.ucas" in str(excinfo.value)
    assert not (game.mods_dir / "scarlet.pak").exists()


def test_duplicate_mod_name_rejected(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    with pytest.raises(StoreError):
        store.import_mod("1", game, "Scarlet", _archive(tmp_path, "o.zip"),
                         PAK_RECIPE)


def test_claim_map_rejects_dst_collision_across_recipes(tmp_path):
    """Two mods may not deploy to the same destination path, even via
    different recipes."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet v1", _archive(tmp_path), PAK_RECIPE)

    with pytest.raises(StoreError) as excinfo:
        store.import_mod("1", game, "Scarlet v2",
                         _archive(tmp_path, "other.zip"), PAK_RECIPE)
    message = str(excinfo.value)
    assert "scarlet.pak" in message
    assert "Scarlet v1" in message
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet v2").exists()
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet v1"]

    store.import_mod("1", game, "Other",
                     _archive(tmp_path, "o.zip", stem="other"), PAK_RECIPE)
    assert len(store.list_mods("1", game)) == 2


def test_refuse_rule_rejects_unmanaged_file_at_import(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    game.mods_dir.mkdir(parents=True)
    (game.mods_dir / "scarlet.pak").write_bytes(b"hand-installed")

    with pytest.raises(StoreError) as exc:
        store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    assert "scarlet.pak" in str(exc.value)
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()
    assert (game.mods_dir / "scarlet.pak").read_bytes() == b"hand-installed"


def test_backup_rule_backs_up_and_restores(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    original = game.install_dir / "SB" / "original.dll"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"vanilla")

    recipe = recipe_from_dict(
        {"name": "replace", "rules": [
            {"match": ["*.dll"], "anchor": "game_root", "subpath": "SB",
             "mapping": "flatten", "overwrite": "backup"}]},
        recipe_id="rep",
    )
    archive = tmp_path / "r.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("original.dll", "modded")
    store.import_mod("1", game, "Replacer", archive, recipe)

    store.set_enabled("1", game, "Replacer", True)
    assert original.read_bytes() == b"modded"
    backup = tmp_path / "base" / "backup" / "1" / "SB" / "original.dll"
    assert backup.read_bytes() == b"vanilla"

    # Re-enabling must NOT re-backup (the true original is preserved).
    store.set_enabled("1", game, "Replacer", True)
    assert backup.read_bytes() == b"vanilla"

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"
    assert not backup.exists()


def test_delete_restores_backup(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    original = game.install_dir / "SB" / "original.dll"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"vanilla")
    recipe = recipe_from_dict(
        {"name": "replace", "rules": [
            {"match": ["*.dll"], "anchor": "game_root", "subpath": "SB",
             "overwrite": "backup"}]},
        recipe_id="rep",
    )
    archive = tmp_path / "r.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("original.dll", "modded")
    store.import_mod("1", game, "Replacer", archive, recipe)
    store.set_enabled("1", game, "Replacer", True)

    store.delete_mod("1", game, "Replacer")
    assert original.read_bytes() == b"vanilla"
    assert store.list_mods("1", game) == []


def test_uninstall_then_reinstall_keeps_mods_disabled(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)

    shutil.rmtree(game.install_dir)
    assert store.list_mods("1", None)[0]["state"] == "disabled"

    reinstalled = _game(tmp_path)
    assert store.list_mods("1", reinstalled)[0]["state"] == "disabled"
    store.set_enabled("1", reinstalled, "Scarlet", True)
    assert store.list_mods("1", reinstalled)[0]["state"] == "enabled"


def test_delete_without_game_cleans_repository(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    shutil.rmtree(game.install_dir)

    store.delete_mod("1", None, "Scarlet")
    assert store.list_mods("1", None) == []
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


def test_set_enabled_wraps_os_error(tmp_path, monkeypatch):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)

    def boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("moddock.store.shutil.copy2", boom)
    with pytest.raises(StoreError):
        store.set_enabled("1", game, "Scarlet", True)


def test_delete_enabled_mod_removes_files_everywhere(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)
    store.delete_mod("1", game, "Scarlet")
    assert store.list_mods("1", game) == []
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


def test_manifest_written_with_deploy_list(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    manifest = json.loads((tmp_path / "base" / "manifest" / "1.json").read_text())
    entry = manifest["mods"]["Scarlet"]
    assert entry["recipe"] == "ue-paks-mods"
    assert sorted(i["dst"] for i in entry["deploy"]) == [
        "SB/Content/Paks/~mods/scarlet.pak",
        "SB/Content/Paks/~mods/scarlet.ucas",
        "SB/Content/Paks/~mods/scarlet.utoc",
    ]


def test_legacy_v1_manifest_still_works(tmp_path):
    """A pre-recipe manifest (flat "files" list) keeps functioning."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    repo = tmp_path / "base" / "mods" / "1" / "Old"
    repo.mkdir(parents=True)
    for ext in ("pak", "utoc", "ucas"):
        (repo / f"old.{ext}").write_bytes(b"x")
    (tmp_path / "base" / "manifest").mkdir(parents=True)
    (tmp_path / "base" / "manifest" / "1.json").write_text(json.dumps({
        "mods": {"Old": {
            "files": ["old.pak", "old.utoc", "old.ucas"],
            "source": "old.zip", "imported_at": "2026-09-01T00:00:00+00:00",
            "repo": str(repo),
        }}
    }))

    [mod] = store.list_mods("1", game)
    assert mod["state"] == "disabled"
    store.set_enabled("1", game, "Old", True)
    assert (game.mods_dir / "old.pak").is_file()
    store.set_enabled("1", game, "Old", False)
    store.delete_mod("1", game, "Old")
    assert store.list_mods("1", game) == []
