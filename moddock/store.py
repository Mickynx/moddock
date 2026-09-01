"""Central mod repository and enable/disable state machine.

Enabled state is never persisted: a mod file living in the game's ~mods
directory means enabled, living in the repository means disabled. Enable
and disable are same-filesystem renames whenever possible; when a game
sits on a different filesystem than the default repository base, that
game's repository is relocated next to the game's Steam library so the
rename stays atomic and instant.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .adapters.unreal import UEGameInfo
from .importer import ImportProblem, ingest


class StoreError(Exception):
    """User-facing store failure; str(exc) is shown in the panel."""


_NAME_RE = re.compile(r"[^A-Za-z0-9 ._()\[\]-]+")


def sanitize_mod_name(name: str) -> str:
    cleaned = _NAME_RE.sub("", name).strip().strip(".")
    return cleaned or "mod"


def _device_of(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return os.stat(probe).st_dev


def _library_root(paks_dir: Path) -> Path | None:
    for ancestor in paks_dir.parents:
        if ancestor.name == "steamapps":
            return ancestor.parent
    return None


class ModStore:
    def __init__(self, base: Path):
        self.base = base
        self.manifest_dir = base / "manifest"

    # -- manifest ---------------------------------------------------------

    def _manifest_path(self, appid: str) -> Path:
        return self.manifest_dir / f"{appid}.json"

    def _load_manifest(self, appid: str) -> dict:
        path = self._manifest_path(appid)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"mods": {}}

    def _save_manifest(self, appid: str, manifest: dict) -> None:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        path = self._manifest_path(appid)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # -- repository placement ---------------------------------------------

    def _repo_root(self, appid: str, game: UEGameInfo) -> Path:
        default = self.base / "mods" / appid
        if _device_of(self.base) == _device_of(game.paks_dir):
            return default
        library = _library_root(game.paks_dir)
        if library is None:
            return default
        return library / ".moddock" / appid

    def _mod_repo_dir(self, appid: str, game: UEGameInfo, mod_name: str) -> Path:
        return self._repo_root(appid, game) / mod_name

    # -- operations ---------------------------------------------------------

    @staticmethod
    def _find_file_conflict(
        manifest: dict, files: list[str]
    ) -> tuple[str, str] | None:
        """Return (owning mod, file name) for the first already-claimed file."""
        claimed = {
            f: name
            for name, entry in sorted(manifest["mods"].items())
            for f in entry["files"]
        }
        for f in files:
            owner = claimed.get(f)
            if owner is not None:
                return owner, f
        return None

    def import_mod(
        self, appid: str, game: UEGameInfo, mod_name: str, source: Path
    ) -> dict:
        mod_name = sanitize_mod_name(mod_name)
        manifest = self._load_manifest(appid)
        if mod_name in manifest["mods"]:
            raise StoreError(f'a mod named "{mod_name}" already exists')
        repo = self._mod_repo_dir(appid, game, mod_name)
        try:
            files = ingest(source, repo)
        except ImportProblem as exc:
            raise StoreError(str(exc)) from exc
        # File names must be unique across a game's mods: enable/disable and
        # delete address files by base name, so two mods claiming the same name
        # would let one destroy or hijack the other's files. Such mods could
        # never be co-enabled anyway (~mods is a flat directory), so rejecting
        # the second import is the honest failure. The check needs the ingested
        # file list, hence it lands after extraction; the repo dir the failed
        # import just created is removed so nothing is left half-imported.
        conflict = self._find_file_conflict(manifest, files)
        if conflict is not None:
            other_name, filename = conflict
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(
                f'file "{filename}" conflicts with existing mod "{other_name}"'
            )
        manifest["mods"][mod_name] = {
            "files": files,
            "source": source.name,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(repo),
        }
        self._save_manifest(appid, manifest)
        return {"name": mod_name, "files": files, "state": "disabled"}

    def list_mods(self, appid: str, game: UEGameInfo | None) -> list[dict]:
        manifest = self._load_manifest(appid)
        mods: list[dict] = []
        for name, entry in sorted(manifest["mods"].items()):
            state = "disabled"
            if game is not None:
                in_mods = [
                    f for f in entry["files"] if (game.mods_dir / f).is_file()
                ]
                if len(in_mods) == len(entry["files"]):
                    state = "enabled"
                elif in_mods:
                    state = "partial"
                else:
                    repo = Path(entry["repo"])
                    missing = [
                        f for f in entry["files"] if not (repo / f).is_file()
                    ]
                    state = "partial" if missing else "disabled"
            mods.append({"name": name, "files": entry["files"], "state": state})
        return mods

    def set_enabled(
        self, appid: str, game: UEGameInfo, mod_name: str, enabled: bool
    ) -> None:
        manifest = self._load_manifest(appid)
        entry = manifest["mods"].get(mod_name)
        if entry is None:
            raise StoreError(f'unknown mod "{mod_name}"')
        repo = Path(entry["repo"])
        src_dir, dst_dir = (repo, game.mods_dir) if enabled else (game.mods_dir, repo)
        # Filesystem errors (ENOSPC, EACCES, a card pulled mid-move) become
        # StoreError so callers only ever handle one exception type. A move
        # that dies partway leaves the mod in the "partial" state, which
        # list_mods reports and a second toggle repairs.
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            moves = []
            for f in entry["files"]:
                src, dst = src_dir / f, dst_dir / f
                if not src.is_file():
                    continue  # already on the target side, or lost (partial)
                if dst.exists():
                    raise StoreError(
                        f'cannot move "{f}": a file with that name already exists'
                    )
                moves.append((src, dst))
            for src, dst in moves:
                shutil.move(str(src), str(dst))
        except OSError as exc:
            raise StoreError(str(exc)) from exc

    def delete_mod(
        self, appid: str, game: UEGameInfo | None, mod_name: str
    ) -> None:
        manifest = self._load_manifest(appid)
        entry = manifest["mods"].get(mod_name)
        if entry is None:
            raise StoreError(f'unknown mod "{mod_name}"')
        if game is None:
            # Without a detected game there is no ~mods path, so an enabled
            # mod's installed files would be orphaned there while the manifest
            # forgot about them. Refuse instead of half-deleting.
            raise StoreError("game is not installed — cannot delete mod files safely")
        repo = Path(entry["repo"])
        try:
            for f in entry["files"]:
                (repo / f).unlink(missing_ok=True)
                (game.mods_dir / f).unlink(missing_ok=True)
            shutil.rmtree(repo, ignore_errors=True)
        except OSError as exc:
            # The manifest entry is deliberately kept so the mod stays known
            # and the user can retry the delete.
            raise StoreError(str(exc)) from exc
        del manifest["mods"][mod_name]
        self._save_manifest(appid, manifest)
