"""Central mod repository and enable/disable state machine.

The repository under the store base always holds the full copy of every
imported mod — it is the single source of truth. Enabling a mod COPIES
its files into the game's ~mods directory; disabling deletes the copies
from ~mods and nothing else. Enabled state is never persisted: a mod
whose files are all present in ~mods is enabled, otherwise it is not.

Consequences of the copy model, all deliberate:
- Uninstalling a game (Steam removes the install dir, ~mods included)
  simply leaves every mod disabled; after a reinstall they can be
  re-enabled from the intact repository.
- Enable is idempotent and self-repairing: import-time conflict checks
  guarantee every managed basename in ~mods belongs to ModDock, so
  overwriting on enable is safe.
- Enabled mods occupy disk twice (repository + ~mods); toggling large
  mods is a real copy rather than a rename.
"""

from __future__ import annotations

import json
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

    def _mod_repo_dir(self, appid: str, mod_name: str) -> Path:
        return self.base / "mods" / appid / mod_name

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
        repo = self._mod_repo_dir(appid, mod_name)
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
        # Same reasoning for files ModDock does not know about: a mod installed
        # by hand into ~mods carries no manifest entry, so adopting its name
        # would let a later disable or delete overwrite or unlink a file
        # ModDock never put there. Refusing keeps the invariant that every
        # managed basename in ~mods belongs to ModDock by construction — which
        # is also what makes overwrite-on-enable safe.
        unmanaged = next(
            (f for f in files if (game.mods_dir / f).is_file()), None
        )
        if unmanaged is not None:
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(
                f'file "{unmanaged}" already exists in the game\'s ~mods '
                "directory and is not managed by ModDock — remove it by hand "
                "first"
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
        # Filesystem errors (ENOSPC, EACCES, a card pulled mid-copy) become
        # StoreError so callers only ever handle one exception type. An enable
        # that dies partway leaves the mod in the "partial" state, which
        # list_mods reports and a second toggle repairs.
        try:
            if enabled:
                missing = next(
                    (f for f in entry["files"] if not (repo / f).is_file()),
                    None,
                )
                if missing is not None:
                    raise StoreError(
                        f'the stored copy of "{missing}" is missing — delete '
                        "the mod and import it again"
                    )
                game.mods_dir.mkdir(parents=True, exist_ok=True)
                for f in entry["files"]:
                    # Overwriting is safe: import-time conflict checks ensure
                    # any same-named file in ~mods is ModDock's own (possibly
                    # stale) copy, so enable doubles as repair and is
                    # idempotent.
                    shutil.copy2(repo / f, game.mods_dir / f)
            else:
                for f in entry["files"]:
                    (game.mods_dir / f).unlink(missing_ok=True)
        except OSError as exc:
            raise StoreError(str(exc)) from exc

    def delete_mod(
        self, appid: str, game: UEGameInfo | None, mod_name: str
    ) -> None:
        manifest = self._load_manifest(appid)
        entry = manifest["mods"].get(mod_name)
        if entry is None:
            raise StoreError(f'unknown mod "{mod_name}"')
        repo = Path(entry["repo"])
        try:
            for f in entry["files"]:
                (repo / f).unlink(missing_ok=True)
                if game is not None:
                    # With no detected game there is nothing to clean in
                    # ~mods: the copy model means the install dir (and any
                    # enabled copies inside it) is gone with the game.
                    (game.mods_dir / f).unlink(missing_ok=True)
            shutil.rmtree(repo, ignore_errors=True)
        except OSError as exc:
            # The manifest entry is deliberately kept so the mod stays known
            # and the user can retry the delete.
            raise StoreError(str(exc)) from exc
        del manifest["mods"][mod_name]
        self._save_manifest(appid, manifest)
