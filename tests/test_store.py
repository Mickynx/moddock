import json
import shutil
import zipfile
from pathlib import Path

import pytest

from moddock.adapters.unreal import MODS_DIR_NAME, UEGameInfo, detect_ue_game
from moddock.recipes import BUILTIN_RECIPES, recipe_from_dict
from moddock.store import ModStore, StoreError, sanitize_mod_name

PAK_RECIPE = BUILTIN_RECIPES[0]  # ue-paks-mods


def _game(tmp_path: Path, sub: str = "game") -> UEGameInfo:
    install = tmp_path / sub
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


def test_claim_map_folds_case(tmp_path):
    """Steam libraries on exFAT/NTFS fold case, so "Mod.pak" and "mod.pak"
    are one file there — the claim map must see them as one destination."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Upper",
                     _archive(tmp_path, "first.zip", stem="Mod"), PAK_RECIPE)

    with pytest.raises(StoreError) as excinfo:
        store.import_mod("1", game, "Lower",
                         _archive(tmp_path, "second.zip", stem="mod"),
                         PAK_RECIPE)
    message = str(excinfo.value)
    assert "Upper" in message
    assert "mod.pak" in message  # the rejected item keeps its own spelling
    assert [m["name"] for m in store.list_mods("1", game)] == ["Upper"]


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


def _backup_recipe():
    return recipe_from_dict(
        {"name": "replace", "rules": [
            {"match": ["*.dll"], "anchor": "game_root", "subpath": "SB",
             "mapping": "flatten", "overwrite": "backup"}]},
        recipe_id="rep",
    )


def _dll_archive(tmp_path: Path, member: str = "original.dll") -> Path:
    archive = tmp_path / f"{member}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member, "modded")
    return archive


def _replacer(tmp_path: Path, *, vanilla: bool = True):
    """Store + game with a "Replacer" mod that overwrites SB/original.dll."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    original = game.install_dir / "SB" / "original.dll"
    if vanilla:
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b"vanilla")
    store.import_mod("1", game, "Replacer", _dll_archive(tmp_path),
                     _backup_recipe())
    backup = tmp_path / "base" / "backup" / "1" / "SB" / "original.dll"
    return store, game, original, backup


def test_backup_rule_backs_up_and_restores(tmp_path):
    store, game, original, backup = _replacer(tmp_path)

    store.set_enabled("1", game, "Replacer", True)
    assert original.read_bytes() == b"modded"
    assert backup.read_bytes() == b"vanilla"

    # Re-enabling must NOT re-backup (the true original is preserved).
    store.set_enabled("1", game, "Replacer", True)
    assert backup.read_bytes() == b"vanilla"

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"
    assert not backup.exists()


def test_disabling_twice_keeps_the_restored_original(tmp_path):
    """The second disable must not unlink the file the first one restored.

    Presence at the destination cannot tell "our copy" from "the game's own
    file we put back", so a naive recall would delete the vanilla file on the
    next toggle. The persisted `displaced` flag is what distinguishes them.
    """
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"

    # The mod is off even though its destination is occupied (by the game).
    assert store.list_mods("1", game)[0]["state"] == "disabled"

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"
    assert not backup.exists()


def test_delete_after_disable_keeps_the_restored_original(tmp_path):
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    store.set_enabled("1", game, "Replacer", False)

    store.delete_mod("1", game, "Replacer")
    assert original.read_bytes() == b"vanilla"
    assert store.list_mods("1", game) == []
    assert not (tmp_path / "base" / "mods" / "1" / "Replacer").exists()


def test_reenable_after_disable_backs_the_original_up_again(tmp_path):
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    store.set_enabled("1", game, "Replacer", False)

    store.set_enabled("1", game, "Replacer", True)
    assert original.read_bytes() == b"modded"
    assert backup.read_bytes() == b"vanilla"
    assert store.list_mods("1", game)[0]["state"] == "enabled"


def test_displaced_backup_item_is_disabled_after_game_reinstall(tmp_path):
    """A parked backup outlives the game; the deployed file does not."""
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    shutil.rmtree(game.install_dir)
    reinstalled = _game(tmp_path)
    assert backup.read_bytes() == b"vanilla"  # the backup survived
    assert store.list_mods("1", reinstalled)[0]["state"] == "disabled"

    store.set_enabled("1", reinstalled, "Replacer", True)
    deployed = reinstalled.install_dir / "SB" / "original.dll"
    assert deployed.read_bytes() == b"modded"
    # The parked original is never re-backed-up over by our own copy.
    assert backup.read_bytes() == b"vanilla"
    assert store.list_mods("1", reinstalled)[0]["state"] == "enabled"


def _deploy_items(tmp_path: Path, mod_name: str = "Replacer") -> list[dict]:
    path = tmp_path / "base" / "manifest" / "1.json"
    return json.loads(path.read_text())["mods"][mod_name]["deploy"]


def test_stranded_backup_is_restored_even_with_the_flag_lost(tmp_path):
    """A parked backup file outranks the flag that should have recorded it.

    Enable moves the original aside before it can persist `displaced`, so a
    crash in that window (ENOSPC on the copy, a later item, the manifest
    write) leaves the vanilla file parked with nothing pointing at it.
    Recall must still put it back rather than unlink the destination.
    """
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    assert backup.read_bytes() == b"vanilla"

    path = tmp_path / "base" / "manifest" / "1.json"
    manifest = json.loads(path.read_text())
    for item in manifest["mods"]["Replacer"]["deploy"]:
        item.pop("displaced", None)
    path.write_text(json.dumps(manifest))

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"  # restored, not unlinked
    assert not backup.exists()

    store.delete_mod("1", game, "Replacer")
    assert original.read_bytes() == b"vanilla"
    assert store.list_mods("1", game) == []


def test_reenable_after_the_original_vanished_clears_the_flag(tmp_path):
    """With nothing left to protect, the item degrades to fresh-path semantics."""
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    store.set_enabled("1", game, "Replacer", False)
    original.unlink()  # the user removed the restored original by hand

    store.set_enabled("1", game, "Replacer", True)
    [item] = _deploy_items(tmp_path)
    assert item.get("displaced", False) is False
    assert original.read_bytes() == b"modded"
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    # Our own copy is now unlinked normally instead of being protected.
    store.set_enabled("1", game, "Replacer", False)
    assert not original.exists()


def test_backup_item_that_displaced_nothing_toggles_normally(tmp_path):
    """A backup-mode file with no pre-existing original behaves like any other."""
    store, game, original, backup = _replacer(tmp_path, vanilla=False)

    store.set_enabled("1", game, "Replacer", True)
    assert original.read_bytes() == b"modded"
    assert not backup.exists()
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    store.set_enabled("1", game, "Replacer", False)
    assert not original.exists()
    assert store.list_mods("1", game)[0]["state"] == "disabled"


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


def test_delete_without_game_keeps_a_parked_backup(tmp_path):
    """An offline library must not cost the user the original file.

    `game is None` means "the install dir is not visible" — an unmounted SD
    card as much as an uninstalled game. Reaping the parked backup there would
    destroy the game's own file while our copy still sits in the (currently
    invisible) game directory, leaving nothing to restore.
    """
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    assert backup.read_bytes() == b"vanilla"

    store.delete_mod("1", None, "Replacer")
    assert backup.read_bytes() == b"vanilla"  # the parked original survives
    assert store.list_mods("1", None) == []
    assert not (tmp_path / "base" / "mods" / "1" / "Replacer").exists()


def test_backup_item_refuses_a_destination_that_is_a_directory(tmp_path):
    """A directory at the destination cannot be parked as a backup.

    Everything downstream (deployed-state, recall, delete) tests the backup
    with `is_file()`, so moving a directory there would wedge the mod: enable
    reports partial forever, disable no-ops, delete cannot unlink it. The
    honest failure is at enable, before anything moves.
    """
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    target = game.install_dir / "SB" / "original.dll"
    target.mkdir(parents=True)
    (target / "keep.txt").write_bytes(b"inside")
    store.import_mod("1", game, "Replacer", _dll_archive(tmp_path),
                     _backup_recipe())

    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Replacer", True)
    assert "directory" in str(excinfo.value)
    assert (target / "keep.txt").read_bytes() == b"inside"
    assert not (tmp_path / "base" / "backup" / "1" / "SB").exists()


def test_disable_removes_our_copy_when_the_backup_was_lost(tmp_path):
    """With the backup gone, byte-identity to the repository identifies ours.

    A hand-cleared backup tree leaves the flag saying "displaced" with no
    original to restore. The destination then holds either our copy or a file
    the user put back; comparing it with the stored copy tells them apart.
    """
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    backup.unlink()

    store.set_enabled("1", game, "Replacer", False)
    assert not original.exists()


def test_disable_leaves_a_foreign_file_when_the_backup_was_lost(tmp_path):
    store, game, original, backup = _replacer(tmp_path)
    store.set_enabled("1", game, "Replacer", True)
    backup.unlink()
    original.write_bytes(b"restored by hand")

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"restored by hand"


def _corrupted(tmp_path, dst: str = "../../evil"):
    """Store + game with a hand-written manifest entry holding a bad dst."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path, "lib/steamapps/common/Game")
    repo = tmp_path / "base" / "mods" / "1" / "Evil"
    repo.mkdir(parents=True)
    (repo / "evil.pak").write_bytes(b"x")
    (tmp_path / "base" / "manifest").mkdir(parents=True)
    (tmp_path / "base" / "manifest" / "1.json").write_text(json.dumps({
        "mods": {"Evil": {
            "recipe": "hand", "recipe_name": "Handmade",
            "deploy": [{"src": "evil.pak", "dst": dst, "overwrite": "refuse"}],
            "source": "e.zip", "imported_at": "2026-09-02T00:00:00+00:00",
        }}
    }))
    return store, game, repo


def test_corrupted_manifest_entry_is_listed_as_corrupted(tmp_path):
    """A dst read back from disk is not trusted: it escapes the game root."""
    store, game, _repo = _corrupted(tmp_path)
    assert store.list_mods("1", game) == [
        {"name": "Evil", "state": "disabled", "recipe_name": "corrupted entry"}
    ]


def test_corrupted_manifest_entry_cannot_be_enabled(tmp_path):
    store, game, _repo = _corrupted(tmp_path)
    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Evil", True)
    assert "Evil" in str(excinfo.value)
    assert "corrupted" in str(excinfo.value)


def test_corrupted_manifest_entry_deletes_repo_and_entry_only(tmp_path):
    """Delete must stay possible — that is how the user gets rid of it."""
    store, game, repo = _corrupted(tmp_path)
    outside = (game.install_dir / "../../evil").resolve()
    outside.write_bytes(b"someone else's file")

    store.delete_mod("1", game, "Evil")
    assert outside.read_bytes() == b"someone else's file"
    assert store.list_mods("1", game) == []
    assert not repo.exists()
    assert not (tmp_path / "base" / "backup").exists()


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


def test_delete_wraps_os_error_and_keeps_the_entry(tmp_path, monkeypatch):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)

    def boom(self, missing_ok=False):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)
    with pytest.raises(StoreError):
        store.delete_mod("1", game, "Scarlet")
    monkeypatch.undo()
    # The mod stays known so the user can retry the delete.
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet"]


def test_delete_wraps_manifest_write_failure_and_keeps_the_entry(
    tmp_path, monkeypatch
):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(ModStore, "_save_manifest", boom)
    with pytest.raises(StoreError):
        store.delete_mod("1", game, "Scarlet")
    monkeypatch.undo()
    # The repository is gone by then, but the entry must survive so the mod
    # stays visible and the retry can finish the job.
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet"]


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


def _legacy_entry(files, repo):
    return {
        "files": files,
        "source": "old.zip",
        "imported_at": "2026-09-01T00:00:00+00:00",
        "repo": str(repo),
    }


def _write_manifest(base: Path, appid: str, mods: dict) -> None:
    (base / "manifest").mkdir(parents=True, exist_ok=True)
    (base / "manifest" / f"{appid}.json").write_text(json.dumps({"mods": mods}))


def test_migration_rescues_move_era_enabled_mod(tmp_path):
    """v1.0 (move semantics) kept an ENABLED mod's files only in ~mods, with
    an empty repository. Migration must copy them back into the store before
    any disable can unlink the only copy."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    game.mods_dir.mkdir(parents=True)
    for ext in ("pak", "utoc", "ucas"):
        (game.mods_dir / f"old.{ext}").write_bytes(b"movedata")
    repo = tmp_path / "base" / "mods" / "1" / "Old"
    repo.mkdir(parents=True)  # v1.0 move semantics: enabled -> repo is empty
    _write_manifest(tmp_path / "base", "1", {"Old": _legacy_entry(
        ["old.pak", "old.utoc", "old.ucas"], repo)})

    [mod] = store.list_mods("1", game)
    assert mod["state"] == "enabled"
    # The store now holds the full copy, harvested from ~mods.
    assert (repo / "old.pak").read_bytes() == b"movedata"
    # The entry was rewritten to the v2 deploy format on disk.
    on_disk = json.loads(
        (tmp_path / "base" / "manifest" / "1.json").read_text()
    )
    assert "deploy" in on_disk["mods"]["Old"]
    assert "files" not in on_disk["mods"]["Old"]

    # The formerly lethal sequence: disable no longer destroys the only copy.
    store.set_enabled("1", game, "Old", False)
    assert not (game.mods_dir / "old.pak").exists()
    assert (repo / "old.pak").read_bytes() == b"movedata"
    store.set_enabled("1", game, "Old", True)
    assert (game.mods_dir / "old.pak").read_bytes() == b"movedata"


def test_migration_recovers_files_from_v1_relocated_repo(tmp_path):
    """v1.0 relocated a game-on-another-drive repo to <library>/.moddock/...;
    migration honors the entry's stored repo path and copies the files into
    the v2 store location."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    old_repo = tmp_path / "sdcard" / ".moddock" / "1" / "Old"
    old_repo.mkdir(parents=True)
    for ext in ("pak", "utoc", "ucas"):
        (old_repo / f"old.{ext}").write_bytes(b"sdcarddata")
    _write_manifest(tmp_path / "base", "1", {"Old": _legacy_entry(
        ["old.pak", "old.utoc", "old.ucas"], old_repo)})

    [mod] = store.list_mods("1", game)
    assert mod["state"] == "disabled"
    new_repo = tmp_path / "base" / "mods" / "1" / "Old"
    assert (new_repo / "old.pak").read_bytes() == b"sdcarddata"
    # Copied, not moved: the old location is left for the user to clean.
    assert (old_repo / "old.pak").is_file()

    store.set_enabled("1", game, "Old", True)
    assert (game.mods_dir / "old.pak").read_bytes() == b"sdcarddata"


def test_migration_with_missing_files_degrades_loudly(tmp_path):
    """A legacy file found nowhere migrates as-is; enable then fails with the
    explicit missing-store-copy message instead of half-deploying."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    _write_manifest(tmp_path / "base", "1", {"Old": _legacy_entry(
        ["gone.pak"], tmp_path / "base" / "mods" / "1" / "Old")})

    [mod] = store.list_mods("1", game)
    assert mod["state"] == "disabled"
    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Old", True)
    assert "gone.pak" in str(excinfo.value)
    store.delete_mod("1", game, "Old")
    assert store.list_mods("1", game) == []


def test_migration_is_idempotent_and_skipped_without_game(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    repo = tmp_path / "base" / "mods" / "1" / "Old"
    repo.mkdir(parents=True)
    (repo / "old.pak").write_bytes(b"x")
    _write_manifest(tmp_path / "base", "1", {"Old": _legacy_entry(
        ["old.pak"], repo)})

    # Without a detected game nothing is rewritten.
    store.list_mods("1", None)
    on_disk = json.loads((tmp_path / "base" / "manifest" / "1.json").read_text())
    assert "files" in on_disk["mods"]["Old"]

    store.list_mods("1", game)
    first = (tmp_path / "base" / "manifest" / "1.json").read_text()
    store.list_mods("1", game)
    assert (tmp_path / "base" / "manifest" / "1.json").read_text() == first
