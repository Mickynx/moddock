import socket
import sys
import types
import zipfile
from pathlib import Path


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
        "list_inbox",
        "assign_inbox_entry",
        "delete_inbox_entry",
        "set_uploader",
        "get_uploader_status",
        "set_upload_port",
        "_main",
        "_unload",
    ):
        assert hasattr(plugin, method), method


def _import_main(tmp_path):
    _install_fake_decky(tmp_path)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import importlib

    return importlib.import_module("main")


async def test_list_inbox_reports_per_file_status(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # A bare .pak is a valid classic mod; .rar is accepted into the inbox only
    # so the inbox can explain that it is unsupported.
    (inbox / "good.pak").write_bytes(b"pak")
    (inbox / "nope.rar").write_bytes(b"rar")
    monkeypatch.setattr("main.INBOX_DIR", inbox)

    entries = await main.Plugin().list_inbox()

    assert [e["filename"] for e in entries] == ["good.pak", "nope.rar"]
    assert entries[0]["status"] == "ready"
    assert "1 mod file(s)" in entries[0]["detail"]
    assert entries[1]["status"] == "error"
    assert "unsupported format" in entries[1]["detail"]


def _ue_tree(tmp_path) -> Path:
    """Minimal Unreal install detect_ue_game() accepts."""
    install = tmp_path / "game"
    (install / "Engine").mkdir(parents=True)
    (install / "SB" / "Content" / "Paks").mkdir(parents=True)
    return install


def _mod_zip(path: Path, stem: str = "scarlet") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"{stem}.{ext}", "x")
    return path


def _wired_plugin(main, tmp_path, monkeypatch):
    """A Plugin with settings/store wired to tmp dirs, as _main would do."""
    from moddock.settings import Settings
    from moddock.store import ModStore

    base = tmp_path / "base"
    inbox = base / "inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setattr("main.BASE_DIR", base)
    monkeypatch.setattr("main.INBOX_DIR", inbox)
    plugin = main.Plugin()
    plugin.settings = Settings(tmp_path / "settings.json")
    plugin.store = ModStore(base)
    plugin.uploader = None
    return plugin, inbox


async def test_list_inbox_survives_undecryptable_entry(tmp_path, monkeypatch):
    """One unreadable upload must not take down the whole listing."""
    from tests.test_importer import _make_encrypted_zip

    main = _import_main(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _make_encrypted_zip(inbox / "locked.zip")
    _mod_zip(inbox / "ok.zip")
    monkeypatch.setattr("main.INBOX_DIR", inbox)

    entries = {e["filename"]: e for e in await main.Plugin().list_inbox()}

    assert entries["locked.zip"]["status"] == "error"
    assert entries["ok.zip"]["status"] == "ready"


async def test_list_inbox_isolates_an_inspection_crash(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "boom.pak").write_bytes(b"pak")
    monkeypatch.setattr("main.INBOX_DIR", inbox)

    def explode(_path):
        raise RuntimeError("inspector exploded")

    monkeypatch.setattr("main.inspect_upload", explode)
    [entry] = await main.Plugin().list_inbox()
    assert entry["status"] == "error"
    assert "could not be inspected" in entry["detail"]


async def test_list_inbox_skips_staging_files(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "good.pak").write_bytes(b"pak")
    (inbox / "half.zip.part").write_bytes(b"partial")
    monkeypatch.setattr("main.INBOX_DIR", inbox)

    entries = await main.Plugin().list_inbox()
    assert [e["filename"] for e in entries] == ["good.pak"]


async def test_delete_inbox_entry_rejects_empty_name(tmp_path, monkeypatch):
    main = _import_main(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setattr("main.INBOX_DIR", inbox)

    for bad in ("", "/", "."):
        result = await main.Plugin().delete_inbox_entry(bad)
        assert result == {"ok": False, "error": "invalid filename"}, bad
    assert inbox.is_dir()


async def test_assign_reports_enable_failure_but_clears_inbox(
    tmp_path, monkeypatch
):
    main = _import_main(tmp_path)
    plugin, inbox = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    await plugin.add_game("1", "SB", str(install))
    _mod_zip(inbox / "mod.zip")

    from moddock.store import StoreError

    def refuse(*args, **kwargs):
        raise StoreError("no space left on device")

    monkeypatch.setattr(plugin.store, "set_enabled", refuse)
    result = await plugin.assign_inbox_entry("mod.zip", "1", "Scarlet")

    assert result["ok"] is False
    assert "could not be enabled" in result["error"]
    assert not (inbox / "mod.zip").exists()
    assert [m["name"] for m in await_mods(plugin)] == ["Scarlet"]


def await_mods(plugin):
    """Synchronous peek at the store, avoiding another await in assertions."""
    return plugin.store.list_mods("1", None)


async def test_end_to_end_inbox_to_delete(tmp_path, monkeypatch):
    """The whole v1 happy path: upload -> assign -> toggle -> delete."""
    main = _import_main(tmp_path)
    plugin, inbox = _wired_plugin(main, tmp_path, monkeypatch)
    install = _ue_tree(tmp_path)
    _mod_zip(inbox / "ScarletHead.zip")

    [entry] = await plugin.list_inbox()
    assert (entry["filename"], entry["status"]) == ("ScarletHead.zip", "ready")

    await plugin.add_game("1", "Stellar Blade", str(install))
    assert [g["appid"] for g in await plugin.get_managed_games()] == ["1"]

    assert await plugin.assign_inbox_entry("ScarletHead.zip", "1", "Scarlet") == {
        "ok": True,
        "error": None,
    }
    assert not (inbox / "ScarletHead.zip").exists()

    mods = await plugin.list_mods("1")
    assert mods["installed"] is True
    assert mods["mods"] == [{"name": "Scarlet", "state": "enabled"}]
    mods_dir = install / "SB" / "Content" / "Paks" / "~mods"
    assert (mods_dir / "scarlet.pak").is_file()

    assert (await plugin.set_mod_enabled("1", "Scarlet", False))["ok"] is True
    assert (await plugin.list_mods("1"))["mods"] == [
        {"name": "Scarlet", "state": "disabled"}
    ]
    assert not (mods_dir / "scarlet.pak").exists()

    assert (await plugin.delete_mod("1", "Scarlet"))["ok"] is True
    assert (await plugin.list_mods("1"))["mods"] == []


async def test_uploader_status_reports_port_and_survives_qr_failure(
    tmp_path, monkeypatch
):
    main = _import_main(tmp_path)
    plugin, _inbox = _wired_plugin(main, tmp_path, monkeypatch)

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
    plugin, _inbox = _wired_plugin(main, tmp_path, monkeypatch)

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
    plugin, inbox = _wired_plugin(main, tmp_path, monkeypatch)

    started = await plugin.set_uploader(True)
    assert started["running"] is True
    old_port = plugin.uploader.port

    new_port = free_port()
    status = await plugin.set_upload_port(new_port)
    assert status["running"] is True
    assert status["port"] == new_port
    assert plugin.uploader.port == new_port != old_port

    await plugin.set_uploader(False)
