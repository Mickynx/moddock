import socket
import sys
import types
import zipfile
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer


def free_port() -> int:
    """Reserve-then-release an ephemeral port for a real bind."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _install_fake_decky(tmp_path):
    fake = types.ModuleType("decky")
    fake.DECKY_PLUGIN_SETTINGS_DIR = str(tmp_path / "settings")
    fake.DECKY_PLUGIN_RUNTIME_DIR = str(tmp_path / "runtime")
    fake.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, error=lambda *a, **k: None
    )

    async def emit(event, *args):
        return None

    fake.emit = emit
    sys.modules["decky"] = fake


def test_plugin_imports_and_has_callables(tmp_path):
    _install_fake_decky(tmp_path)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import importlib

    main = importlib.import_module("main")
    plugin = main.Plugin()
    for method in (
        "scan_games",
        "get_managed_games",
        "add_game",
        "remove_game",
        "list_mods",
        "set_mod_enabled",
        "delete_mod",
        "set_uploader",
        "get_uploader_status",
        "set_upload_port",
        "list_recipes",
        "delete_recipe",
        "_main",
        "_unload",
    ):
        assert hasattr(plugin, method), method
    # The inbox era is over: uploads install directly.
    for gone in ("list_inbox", "assign_inbox_entry", "delete_inbox_entry"):
        assert not hasattr(plugin, gone), gone


def _import_main(tmp_path):
    _install_fake_decky(tmp_path)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import importlib

    return importlib.import_module("main")


def _ue_tree(tmp_path) -> Path:
    """Minimal Unreal install detect_ue_game() accepts."""
    install = tmp_path / "game"
    (install / "Engine").mkdir(parents=True)
    (install / "SB" / "Content" / "Paks").mkdir(parents=True)
    # Present so the win64_dir anchor resolves; recipes that target it are
    # otherwise refused for this game.
    (install / "SB" / "Binaries" / "Win64").mkdir(parents=True)
    return install


ALL_ANCHORS = ["game_root", "paks_dir", "win64_dir"]
PAKS_RECIPE = "ue-paks-mods"


def _mod_zip(path: Path, stem: str = "scarlet") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"{stem}.{ext}", "x")
    return path


def _wired_plugin(main, tmp_path, monkeypatch):
    """A Plugin with settings/store wired to tmp dirs, as _main would do."""
    from moddock.recipes import RecipeStore
    from moddock.settings import Settings
    from moddock.store import ModStore

    base = tmp_path / "base"
    staging = base / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr("main.BASE_DIR", base)
    monkeypatch.setattr("main.STAGING_DIR", staging)
    plugin = main.Plugin()
    plugin.settings = Settings(tmp_path / "settings.json")
    plugin.store = ModStore(base)
    plugin.recipes = RecipeStore(tmp_path / "recipes.json")
    plugin.uploader = None
    return plugin, staging


async def test_upload_games_lists_only_installed(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))
    await plugin.add_game("2", "Gone Game", str(tmp_path / "nowhere"))

    assert await plugin._upload_games() == [
        {"appid": "1", "name": "Stellar Blade", "anchors": ALL_ANCHORS}
    ]


async def test_upload_games_omits_anchors_the_game_lacks(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    (install / "SB" / "Binaries" / "Win64").rmdir()
    await plugin.add_game("1", "Stellar Blade", str(install))

    assert await plugin._upload_games() == [
        {
            "appid": "1",
            "name": "Stellar Blade",
            "anchors": ["game_root", "paks_dir"],
        }
    ]


async def test_install_upload_end_to_end(tmp_path, monkeypatch):
    """The whole flow: upload file -> import -> enabled -> toggle -> delete."""
    main = _import_main(tmp_path)
    plugin, staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))
    upload = _mod_zip(staging / "ScarletHead.zip")

    ok, detail = await plugin._install_upload(upload, "1", PAKS_RECIPE)
    assert (ok, detail) == (True, "ScarletHead")

    mods = await plugin.list_mods("1")
    assert mods["installed"] is True
    assert mods["mods"] == [
        {
            "name": "ScarletHead",
            "state": "enabled",
            "recipe_name": "UE ~mods (pak)",
        }
    ]
    mods_dir = install / "SB" / "Content" / "Paks" / "~mods"
    assert (mods_dir / "scarlet.pak").is_file()
    # The repository keeps its copy while the mod is enabled.
    repo_pak = tmp_path / "base" / "mods" / "1" / "ScarletHead" / "scarlet.pak"
    assert repo_pak.is_file()

    assert (await plugin.set_mod_enabled("1", "ScarletHead", False))["ok"] is True
    assert not (mods_dir / "scarlet.pak").exists()
    assert repo_pak.is_file()

    assert (await plugin.delete_mod("1", "ScarletHead"))["ok"] is True
    assert (await plugin.list_mods("1"))["mods"] == []


async def test_install_upload_reports_validation_failure(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))
    bad = staging / "broken.zip"
    bad.write_bytes(b"this is not a zip")

    ok, reason = await plugin._install_upload(bad, "1", PAKS_RECIPE)
    assert ok is False
    assert "zip" in reason.lower()
    assert (await plugin.list_mods("1"))["mods"] == []


async def test_install_upload_rejects_missing_game(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, staging = _wired_plugin(main, tmp_path, monkeypatch)
    upload = _mod_zip(staging / "mod.zip")

    ok, reason = await plugin._install_upload(upload, "999", PAKS_RECIPE)
    assert (ok, reason) == (False, "game is not installed")


async def test_install_upload_with_unknown_recipe_fails(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))
    upload = _mod_zip(staging / "mod.zip")

    ok, reason = await plugin._install_upload(upload, "1", "custom-gone")
    assert ok is False
    assert "install method" in reason
    assert (await plugin.list_mods("1"))["mods"] == []


async def test_list_and_delete_recipes(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)

    builtins = {r["id"]: r for r in await plugin.list_recipes()}
    assert builtins[PAKS_RECIPE]["builtin"] is True
    assert builtins[PAKS_RECIPE]["name"] == "UE ~mods (pak)"
    assert builtins[PAKS_RECIPE]["rules"] == 1

    created = await plugin._create_recipe(
        {
            "name": "Movies folder",
            "rules": [
                {"match": ["*.mp4"], "anchor": "game_root", "subpath": "Movies"}
            ],
        }
    )
    assert created["builtin"] is False
    assert created["name"] == "Movies folder"
    assert created["id"]
    listed = {r["id"]: r for r in await plugin.list_recipes()}
    assert listed[created["id"]]["builtin"] is False

    assert await plugin.delete_recipe(created["id"]) == {
        "ok": True,
        "error": None,
    }
    assert created["id"] not in {r["id"] for r in await plugin.list_recipes()}

    refused = await plugin.delete_recipe(PAKS_RECIPE)
    assert refused["ok"] is False
    assert "built-in" in refused["error"]
    assert PAKS_RECIPE in {r["id"] for r in await plugin.list_recipes()}


async def test_create_recipe_rejects_invalid(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)

    # RecipeError is not a ValueError, so this only holds because the wrapper
    # translates it — which is what makes the uploader answer 400, not 500.
    with pytest.raises(ValueError):
        await plugin._create_recipe({"name": ""})
    assert len(await plugin.list_recipes()) == 4


async def test_install_upload_multi_destination(tmp_path, monkeypatch):
    """One upload, two anchors: paks land in ~mods, scripts under Win64."""
    main = _import_main(tmp_path)
    plugin, staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))

    combo = await plugin._create_recipe(
        {
            "name": "Paks + UE4SS scripts",
            "rules": [
                {
                    "match": ["*.pak", "*.utoc", "*.ucas"],
                    "anchor": "paks_dir",
                    "subpath": "~mods",
                },
                {
                    "match": ["*.lua"],
                    "anchor": "win64_dir",
                    "subpath": "ue4ss/Mods",
                    "mapping": "preserve_tree",
                },
            ],
        }
    )

    upload = staging / "ComboMod.zip"
    with zipfile.ZipFile(upload, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"scarlet.{ext}", "x")
        zf.writestr("MyMod/scripts/main.lua", "-- hi")

    ok, detail = await plugin._install_upload(upload, "1", combo["id"])
    assert (ok, detail) == (True, "ComboMod")

    win64 = install / "SB" / "Binaries" / "Win64"
    assert (win64 / "ue4ss/Mods/MyMod/scripts/main.lua").is_file()
    assert (install / "SB/Content/Paks/~mods/scarlet.pak").is_file()
    assert (await plugin.list_mods("1"))["mods"] == [
        {
            "name": "ComboMod",
            "state": "enabled",
            "recipe_name": "Paks + UE4SS scripts",
        }
    ]


async def test_http_upload_installs_end_to_end(tmp_path, monkeypatch):
    """Full stack: HTTP multipart -> uploader -> installer -> store -> ~mods."""
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "Stellar Blade", str(install))

    server = plugin._start_uploader()
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        games = await (await client.get("/u/testtoken/games")).json()
        assert games["games"] == [
            {"appid": "1", "name": "Stellar Blade", "anchors": ALL_ANCHORS}
        ]
        assert {
            "id": PAKS_RECIPE,
            "name": "UE ~mods (pak)",
            "builtin": True,
        } in games["recipes"]

        archive = _mod_zip(tmp_path / "ScarletHead.zip")
        form = aiohttp.FormData()
        form.add_field("appid", "1")
        form.add_field("recipe", PAKS_RECIPE)
        form.add_field("file", archive.read_bytes(), filename="ScarletHead.zip")
        resp = await client.post("/u/testtoken", data=form)
        assert resp.status == 200
        body = await resp.json()
        assert body["installed"] == [
            {"name": "ScarletHead.zip", "mod": "ScarletHead"}
        ]
    finally:
        await client.close()

    mods_dir = install / "SB" / "Content" / "Paks" / "~mods"
    assert (mods_dir / "scarlet.pak").is_file()
    assert (await plugin.list_mods("1"))["mods"] == [
        {
            "name": "ScarletHead",
            "state": "enabled",
            "recipe_name": "UE ~mods (pak)",
        }
    ]


async def test_uploader_status_reports_port_and_survives_qr_failure(
    tmp_path, monkeypatch
):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)

    status = await plugin.get_uploader_status()
    assert status["running"] is False
    assert status["port"] == plugin.settings.upload_port

    class FakeUploader:
        def status(self):
            return {"running": True, "url": "http://10.0.0.5:8765/u/tok"}

    plugin.uploader = FakeUploader()

    def no_segno(_text):
        raise ModuleNotFoundError("No module named 'segno'")

    monkeypatch.setattr("main.qr_svg", no_segno)
    status = await plugin.get_uploader_status()
    assert status["running"] is True
    assert status["qr_svg"] is None
    assert status["url"] == "http://10.0.0.5:8765/u/tok"
    plugin.uploader = None


async def test_set_upload_port_validates_and_persists(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)

    status = await plugin.set_upload_port(9100)
    assert status["port"] == 9100
    assert plugin.settings.upload_port == 9100

    rejected = await plugin.set_upload_port(80)
    assert "1024" in rejected["error"]
    assert plugin.settings.upload_port == 9100
    assert rejected["port"] == 9100


async def test_set_upload_port_restarts_a_running_uploader(
    tmp_path, monkeypatch
):
    main = _import_main(tmp_path)
    plugin, _staging = _wired_plugin(main, tmp_path, monkeypatch)

    started = await plugin.set_uploader(True)
    assert started["running"] is True
    old_port = plugin.uploader.port

    new_port = free_port()
    status = await plugin.set_upload_port(new_port)
    assert status["running"] is True
    assert status["port"] == new_port
    assert plugin.uploader.port == new_port != old_port

    await plugin.set_uploader(False)
