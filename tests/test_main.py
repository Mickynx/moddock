import sys
import types
from pathlib import Path


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
        "_main",
        "_unload",
    ):
        assert hasattr(plugin, method), method
