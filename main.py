import asyncio
import os
import sys
from pathlib import Path

# Make the bundled package importable regardless of loader cwd.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import decky  # noqa: E402  (provided by Decky Loader)

from moddock.adapters.unreal import UEGameInfo, detect_ue_game  # noqa: E402
from moddock.importer import inspect_upload  # noqa: E402
from moddock.settings import Settings  # noqa: E402
from moddock.steam import (  # noqa: E402
    discover_libraries,
    find_steam_root,
    list_installed_games,
)
from moddock.store import ModStore, StoreError, sanitize_mod_name  # noqa: E402
from moddock.uploader import UploadServer, qr_svg  # noqa: E402

BASE_DIR = Path.home() / ".local/share/moddock"
INBOX_DIR = BASE_DIR / "inbox"


class Plugin:
    settings: Settings
    store: ModStore
    uploader: UploadServer | None = None

    # -- lifecycle -------------------------------------------------------

    async def _main(self):
        self.settings = Settings(
            Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "settings.json"
        )
        self.store = ModStore(BASE_DIR)
        INBOX_DIR.mkdir(parents=True, exist_ok=True)
        self.uploader = None
        decky.logger.info("ModDock loaded")

    async def _unload(self):
        if self.uploader is not None:
            await self.uploader.stop()
        decky.logger.info("ModDock unloaded")

    # -- helpers ---------------------------------------------------------

    def _detect(self, install_dir: str) -> UEGameInfo | None:
        return detect_ue_game(Path(install_dir))

    def _managed_game(self, appid: str) -> dict | None:
        for game in self.settings.managed_games:
            if game["appid"] == appid:
                return game
        return None

    # -- games -----------------------------------------------------------

    async def scan_games(self) -> list[dict]:
        root = find_steam_root()
        if root is None:
            return []
        managed = {g["appid"] for g in self.settings.managed_games}
        results = []
        for game in list_installed_games(discover_libraries(root)):
            if game.appid in managed:
                continue
            info = self._detect(str(game.install_dir))
            if info is None:
                continue
            results.append(
                {
                    "appid": game.appid,
                    "name": game.name,
                    "install_dir": str(game.install_dir),
                    "is_iostore": info.is_iostore,
                }
            )
        return results

    async def get_managed_games(self) -> list[dict]:
        results = []
        for game in self.settings.managed_games:
            info = self._detect(game["install_dir"])
            results.append(
                {
                    "appid": game["appid"],
                    "name": game["name"],
                    "installed": info is not None,
                    "is_iostore": info.is_iostore if info else False,
                }
            )
        return results

    async def add_game(self, appid: str, name: str, install_dir: str) -> None:
        self.settings.add_game(appid, name, install_dir)

    async def remove_game(self, appid: str) -> None:
        self.settings.remove_game(appid)

    # -- mods --------------------------------------------------------------

    async def list_mods(self, appid: str) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        mods = self.store.list_mods(appid, info)
        return {
            "installed": info is not None,
            "running_hint": False,
            "mods": [{"name": m["name"], "state": m["state"]} for m in mods],
        }

    async def set_mod_enabled(
        self, appid: str, mod_name: str, enabled: bool
    ) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        if info is None:
            return {"ok": False, "error": "game is not installed"}
        try:
            self.store.set_enabled(appid, info, mod_name, enabled)
            return {"ok": True, "error": None}
        except StoreError as exc:
            return {"ok": False, "error": str(exc)}

    async def delete_mod(self, appid: str, mod_name: str) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        # A missing game is not short-circuited here: the store decides, and it
        # refuses with a StoreError rather than orphaning installed files.
        try:
            self.store.delete_mod(appid, info, mod_name)
            return {"ok": True, "error": None}
        except StoreError as exc:
            return {"ok": False, "error": str(exc)}

    # -- inbox -------------------------------------------------------------

    async def list_inbox(self) -> list[dict]:
        entries = []
        loop = asyncio.get_running_loop()
        for path in sorted(INBOX_DIR.iterdir()):
            if not path.is_file():
                continue
            # inspect_upload extracts archives (and shells out for .7z), which
            # can take seconds per file. This is the refresh path — panel open
            # plus every "moddock_upload" event — so it must not run on the
            # event loop: doing so would stall Decky and our own aiohttp
            # uploader, which share that loop.
            status, detail = await loop.run_in_executor(None, inspect_upload, path)
            entries.append(
                {"filename": path.name, "status": status, "detail": detail}
            )
        return entries

    async def assign_inbox_entry(
        self, filename: str, appid: str, mod_name: str
    ) -> dict:
        path = INBOX_DIR / Path(filename).name
        if not path.is_file():
            return {"ok": False, "error": "file no longer in the inbox"}
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        if info is None:
            return {"ok": False, "error": "game is not installed"}
        name = sanitize_mod_name(mod_name or path.stem)
        try:
            self.store.import_mod(appid, info, name, path)
            self.store.set_enabled(appid, info, name, True)
        except StoreError as exc:
            return {"ok": False, "error": str(exc)}
        path.unlink(missing_ok=True)
        return {"ok": True, "error": None}

    async def delete_inbox_entry(self, filename: str) -> dict:
        (INBOX_DIR / Path(filename).name).unlink(missing_ok=True)
        return {"ok": True, "error": None}

    # -- uploader ------------------------------------------------------------

    async def set_uploader(self, enabled: bool) -> dict:
        if enabled and self.uploader is None:
            async def notify(_filename: str) -> None:
                await decky.emit("moddock_upload")

            self.uploader = UploadServer(
                inbox=INBOX_DIR,
                port=self.settings.upload_port,
                on_upload=notify,
            )
            try:
                await self.uploader.start()
            except OSError as exc:
                # A failed bind (port already in use) leaves the server cleanly
                # stopped, so the half-built instance is dropped and the error
                # is reported instead of advertising a dead URL.
                self.uploader = None
                decky.logger.error(f"uploader failed to start: {exc}")
                return {
                    "running": False,
                    "url": None,
                    "qr_svg": None,
                    "error": str(exc),
                }
        elif not enabled and self.uploader is not None:
            await self.uploader.stop()
            self.uploader = None
        return await self.get_uploader_status()

    async def get_uploader_status(self) -> dict:
        if self.uploader is None:
            return {"running": False, "url": None, "qr_svg": None}
        status = self.uploader.status()
        status["qr_svg"] = qr_svg(status["url"]) if status["url"] else None
        return status
