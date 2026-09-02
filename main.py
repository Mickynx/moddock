import asyncio
import functools
import os
import sys
from pathlib import Path

# Make the bundled package importable regardless of loader cwd.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import decky  # noqa: E402  (provided by Decky Loader)

from moddock import importer  # noqa: E402
from moddock.adapters.unreal import UEGameInfo, detect_ue_game  # noqa: E402
from moddock.recipes import RecipeError, RecipeStore  # noqa: E402
from moddock.settings import Settings  # noqa: E402
from moddock.steam import (  # noqa: E402
    discover_libraries,
    find_steam_root,
    list_installed_games,
)
from moddock.store import ModStore, StoreError, sanitize_mod_name  # noqa: E402
from moddock.uploader import UploadServer, qr_svg  # noqa: E402

BASE_DIR = Path.home() / ".local/share/moddock"
STAGING_DIR = BASE_DIR / "staging"
MIN_PORT, MAX_PORT = 1024, 65535


class Plugin:
    settings: Settings
    store: ModStore
    recipes: RecipeStore
    uploader: UploadServer | None = None

    # -- lifecycle -------------------------------------------------------

    async def _main(self):
        self.settings = Settings(
            Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "settings.json"
        )
        self.store = ModStore(BASE_DIR)
        self.recipes = RecipeStore(BASE_DIR / "recipes.json")
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        # Staging files are per-upload scratch; anything still here is debris
        # from an interrupted transfer in a previous session.
        for stale in STAGING_DIR.iterdir():
            if stale.is_file():
                stale.unlink(missing_ok=True)
        # Extract under our own data directory instead of /tmp: on SteamOS and
        # Bazzite /tmp is tmpfs, so a multi-gigabyte archive would be unpacked
        # into RAM.
        temp_root = BASE_DIR / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        importer.TEMP_ROOT = temp_root
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

    @staticmethod
    async def _off_loop(func, *args, **kwargs):
        """Run a blocking store/import call off the shared event loop.

        Decky's event loop also serves our aiohttp uploader, so filesystem work
        that can take seconds (extraction, copying gigabytes into ~mods) must
        never run on it.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(func, *args, **kwargs)
        )

    async def list_mods(self, appid: str) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        mods = self.store.list_mods(appid, info)
        return {
            "installed": info is not None,
            "running_hint": False,
            "mods": [
                {
                    "name": m["name"],
                    "state": m["state"],
                    # Which install method placed this mod; the panel shows it
                    # so a mod's destinations are explainable after the fact.
                    "recipe_name": m["recipe_name"],
                }
                for m in mods
            ],
        }

    async def set_mod_enabled(
        self, appid: str, mod_name: str, enabled: bool
    ) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        if info is None:
            return {"ok": False, "error": "game is not installed"}
        try:
            await self._off_loop(
                self.store.set_enabled, appid, info, mod_name, enabled
            )
            return {"ok": True, "error": None}
        except StoreError as exc:
            return {"ok": False, "error": str(exc)}

    async def delete_mod(self, appid: str, mod_name: str) -> dict:
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        # A missing game is fine under the copy model: the store then cleans
        # only its repository, since ~mods vanished with the install dir.
        try:
            await self._off_loop(self.store.delete_mod, appid, info, mod_name)
            return {"ok": True, "error": None}
        except StoreError as exc:
            return {"ok": False, "error": str(exc)}

    # -- install methods (recipes) -------------------------------------------

    async def list_recipes(self) -> list[dict]:
        return [
            {
                "id": recipe.id,
                "name": recipe.name,
                "builtin": recipe.builtin,
                # A count, not the rules themselves: the panel only needs to
                # hint at how elaborate a method is.
                "rules": len(recipe.rules),
            }
            for recipe in self.recipes.list()
        ]

    async def delete_recipe(self, recipe_id: str) -> dict:
        try:
            self.recipes.delete(recipe_id)
            return {"ok": True, "error": None}
        except RecipeError as exc:
            return {"ok": False, "error": str(exc)}

    async def _recipes_payload(self) -> list[dict]:
        """Install methods as the upload page's picker needs them."""
        return [
            {"id": recipe.id, "name": recipe.name, "builtin": recipe.builtin}
            for recipe in self.recipes.list()
        ]

    async def _create_recipe(self, body: dict) -> dict:
        """Create a custom install method from the upload page's JSON body."""
        try:
            recipe = self.recipes.create(body)
        except RecipeError as exc:
            # RecipeError is not a ValueError, and the uploader only turns a
            # ValueError into a 400. Without this translation a malformed
            # recipe would surface as a 500 with no reason for the user.
            raise ValueError(str(exc)) from exc
        return {"id": recipe.id, "name": recipe.name, "builtin": False}

    # -- upload install pipeline ---------------------------------------------

    async def _upload_games(self) -> list[dict]:
        """Games offered by the upload page: managed AND currently installed."""
        games = []
        for game in self.settings.managed_games:
            info = self._detect(game["install_dir"])
            if info is None:
                continue
            games.append(
                {
                    "appid": game["appid"],
                    "name": game["name"],
                    # Anchors this install actually has, so the page can grey
                    # out locations a rule could never resolve against.
                    "anchors": [
                        anchor
                        for anchor, path in info.anchor_map().items()
                        if path is not None
                    ],
                }
            )
        return games

    async def _install_upload(
        self, path: Path, appid: str, recipe_id: str
    ) -> tuple[bool, str]:
        """Validate, import and enable one uploaded file for the given game.

        Returns (ok, detail): the mod name on success, a user-facing reason on
        failure. The uploader shows the detail verbatim on the upload page.
        """
        game = self._managed_game(appid)
        info = self._detect(game["install_dir"]) if game else None
        if info is None:
            return False, "game is not installed"
        recipe = self.recipes.get(recipe_id)
        if recipe is None:
            # The page was loaded before the method was deleted, or against a
            # different plugin instance.
            return False, "unknown install method — refresh the page"
        name = sanitize_mod_name(path.stem)
        try:
            await self._off_loop(
                self.store.import_mod, appid, info, name, path, recipe=recipe
            )
        except StoreError as exc:
            return False, str(exc)
        try:
            await self._off_loop(self.store.set_enabled, appid, info, name, True)
        except StoreError as exc:
            # The mod is imported and listed in the panel, only the enable
            # failed; the toggle can be retried from the game's mod list.
            return False, f'imported as "{name}" but could not be enabled: {exc}'
        return True, name

    # -- uploader ------------------------------------------------------------

    def _start_uploader(self) -> UploadServer:
        async def notify(_filename: str) -> None:
            await decky.emit("moddock_upload")

        return UploadServer(
            staging=STAGING_DIR,
            port=self.settings.upload_port,
            installer=self._install_upload,
            games_provider=self._upload_games,
            recipes_provider=self._recipes_payload,
            recipe_creator=self._create_recipe,
            on_upload=notify,
        )

    async def set_uploader(self, enabled: bool) -> dict:
        if enabled and self.uploader is None:
            self.uploader = self._start_uploader()
            try:
                await self.uploader.start()
            except OSError as exc:
                # A failed bind (port already in use) leaves the server cleanly
                # stopped, so the half-built instance is dropped and the error
                # is reported instead of advertising a dead URL.
                self.uploader = None
                decky.logger.error(f"uploader failed to start: {exc}")
                status = await self.get_uploader_status()
                status["error"] = str(exc)
                return status
        elif not enabled and self.uploader is not None:
            await self.uploader.stop()
            self.uploader = None
        return await self.get_uploader_status()

    async def set_upload_port(self, port: int) -> dict:
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = -1
        if not MIN_PORT <= port <= MAX_PORT:
            status = await self.get_uploader_status()
            status["error"] = (
                f"port must be between {MIN_PORT} and {MAX_PORT}"
            )
            return status
        self.settings.set_upload_port(port)
        if self.uploader is None:
            return await self.get_uploader_status()
        # A running server is bound to the old port, so it is recreated. The
        # token changes with the restart, which is fine: the panel shows the
        # new URL and QR code.
        await self.uploader.stop()
        self.uploader = self._start_uploader()
        try:
            await self.uploader.start()
        except OSError as exc:
            self.uploader = None
            decky.logger.error(f"uploader failed to restart: {exc}")
            status = await self.get_uploader_status()
            status["error"] = str(exc)
            return status
        return await self.get_uploader_status()

    async def get_uploader_status(self) -> dict:
        port = self.settings.upload_port
        if self.uploader is None:
            return {"running": False, "url": None, "qr_svg": None, "port": port}
        status = self.uploader.status()
        status["port"] = port
        try:
            status["qr_svg"] = qr_svg(status["url"]) if status["url"] else None
        except Exception as exc:  # noqa: BLE001 - QR is a convenience only
            # segno may be missing from a store/CI build. The URL alone is
            # still enough to reach the upload page, so degrade instead of
            # failing the whole Settings view.
            decky.logger.error(f"QR code generation failed: {exc}")
            status["qr_svg"] = None
        return status
