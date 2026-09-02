"""Central mod repository and enable/disable state machine.

The repository under the store base always holds the full copy of every
imported mod — it is the single source of truth. An install recipe turns
that copy into a *deploy list*: pairs of (repo-relative source,
game-root-relative destination) recorded in the manifest at import time.
Enabling a mod COPIES each source to its destination; disabling removes
those destinations again. Enabled state is never persisted: a mod whose
destinations are all present under the game install is enabled, otherwise
it is not.

Two mechanisms keep a mod from stepping on files it does not own:
- The claim map. Every destination recorded in the manifest belongs to
  exactly one mod; an import whose deploy list collides with an existing
  claim is refused, naming the owner.
- Per-item overwrite policy. `refuse` items must not already exist on
  disk at import time (a hand-installed file is never silently
  clobbered), which is what makes overwrite-on-enable safe afterwards.
  `backup` items may replace a pre-existing file: the original is moved
  aside to base/backup/<appid>/<dst> on the first enable and moved back
  on disable or delete.

Consequences of the copy model, all deliberate:
- Uninstalling a game (Steam removes the install dir, deployed copies
  included) simply leaves every mod disabled; after a reinstall they can
  be re-enabled from the intact repository.
- Enable is idempotent and self-repairing: re-enabling a "partial" mod
  restores the missing destinations.
- Enabled mods occupy disk twice (repository + game dir); toggling large
  mods is a real copy rather than a rename.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .adapters.unreal import MODS_DIR_NAME, UEGameInfo
from .importer import ImportProblem, ingest_tree
from .recipes import Recipe, RecipeError, apply_recipe


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
        self.backup_dir = base / "backup"

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

    # -- placement ---------------------------------------------------------

    def _mod_repo_dir(self, appid: str, mod_name: str) -> Path:
        return self.base / "mods" / appid / mod_name

    def _backup_path(self, appid: str, dst: str) -> Path:
        """Where the pre-existing file displaced by `dst` is parked.

        The game-root-relative destination is mirrored under the store's
        backup tree, so a backup is addressable from the destination alone
        — no extra bookkeeping in the manifest, and it survives the game
        being uninstalled underneath us.
        """
        return self.backup_dir / appid / dst

    # -- deploy lists ------------------------------------------------------

    @staticmethod
    def _entry_deploy(entry: dict, game: UEGameInfo | None) -> list[dict] | None:
        """Deploy items for an entry; synthesizes them for legacy v1 entries.

        v1 manifests recorded a flat "files" list and always installed into
        the game's ~mods directory. That is exactly what the ue-paks-mods
        recipe produces, so the equivalent deploy list can be reconstructed
        on the fly from the game's anchors — no migration pass, and a
        rollback to v1 keeps working. Without a detected game there are no
        anchors to resolve against, so such an entry is unusable (repo-side
        delete still works).
        """
        if "deploy" in entry:
            return entry["deploy"]
        if game is None or game.install_dir is None:
            return None
        paks_rel = game.anchor_map()["paks_dir"]
        return [
            {
                "src": f,
                "dst": f"{paks_rel}/{MODS_DIR_NAME}/{f}",
                "overwrite": "refuse",
            }
            for f in entry["files"]
        ]

    def _claimed(
        self, manifest: dict, game: UEGameInfo | None
    ) -> dict[str, str]:
        """Destination -> owning mod name, over every entry in the manifest."""
        claimed: dict[str, str] = {}
        for name, entry in sorted(manifest["mods"].items()):
            for item in self._entry_deploy(entry, game) or []:
                claimed[item["dst"]] = name
        return claimed

    # -- operations --------------------------------------------------------

    def import_mod(
        self,
        appid: str,
        game: UEGameInfo,
        mod_name: str,
        source: Path,
        recipe: Recipe,
    ) -> dict:
        mod_name = sanitize_mod_name(mod_name)
        manifest = self._load_manifest(appid)
        if mod_name in manifest["mods"]:
            raise StoreError(f'a mod named "{mod_name}" already exists')
        # Resolved before anything is written, so a game that cannot supply
        # anchors at all fails without leaving a repository directory behind.
        anchors = game.anchor_map()
        repo = self._mod_repo_dir(appid, mod_name)
        # Every failure past this point removes the repository directory:
        # ingest_tree copies file by file, so even a mid-copy error leaves
        # debris that must not be mistaken for an imported mod.
        try:
            tree = ingest_tree(source, repo)
        except ImportProblem as exc:
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(str(exc)) from exc
        try:
            items = apply_recipe(recipe, tree, anchors)
        except RecipeError as exc:
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(str(exc)) from exc
        claimed = self._claimed(manifest, game)
        for item in items:
            # Destinations must be unique across a game's mods: enable,
            # disable and delete address files by destination path, so two
            # mods claiming the same one would let either destroy or hijack
            # the other's file. Rejecting the second import is the honest
            # failure.
            owner = claimed.get(item.dst)
            if owner is not None:
                shutil.rmtree(repo, ignore_errors=True)
                raise StoreError(
                    f'"{item.dst}" is already used by mod "{owner}"'
                )
            # Same reasoning for files ModDock does not know about: a mod
            # installed by hand carries no manifest entry, so adopting its
            # path would let a later disable or delete unlink a file ModDock
            # never put there. Refusing keeps the invariant that every
            # managed destination belongs to ModDock by construction — which
            # is also what makes overwrite-on-enable safe.
            if (
                item.overwrite == "refuse"
                and (game.install_dir / item.dst).exists()
            ):
                shutil.rmtree(repo, ignore_errors=True)
                raise StoreError(
                    f'"{item.dst}" already exists and is not managed by '
                    "ModDock — remove it by hand, or use an install method "
                    "with backup enabled"
                )
        manifest["mods"][mod_name] = {
            "recipe": recipe.id,
            "recipe_name": recipe.name,
            "deploy": [
                {"src": i.src, "dst": i.dst, "overwrite": i.overwrite}
                for i in items
            ],
            "source": source.name,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._save_manifest(appid, manifest)
        except OSError as exc:
            # An unrecorded repository directory would be invisible to every
            # later operation, so it goes with the failed manifest write.
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(str(exc)) from exc
        return {"name": mod_name, "state": "disabled"}

    def list_mods(self, appid: str, game: UEGameInfo | None) -> list[dict]:
        manifest = self._load_manifest(appid)
        mods: list[dict] = []
        for name, entry in sorted(manifest["mods"].items()):
            deploy = self._entry_deploy(entry, game)
            state = "disabled"
            if game is not None and game.install_dir is not None and deploy:
                present = sum(
                    1
                    for item in deploy
                    if (game.install_dir / item["dst"]).is_file()
                )
                if present == len(deploy):
                    state = "enabled"
                elif present:
                    state = "partial"
            mods.append(
                {
                    "name": name,
                    "state": state,
                    "recipe_name": entry.get("recipe_name", "legacy"),
                }
            )
        return mods

    def set_enabled(
        self, appid: str, game: UEGameInfo, mod_name: str, enabled: bool
    ) -> None:
        manifest = self._load_manifest(appid)
        entry = manifest["mods"].get(mod_name)
        if entry is None:
            raise StoreError(f'unknown mod "{mod_name}"')
        deploy = (
            None
            if game is None or game.install_dir is None
            else self._entry_deploy(entry, game)
        )
        if deploy is None:
            raise StoreError("game is not installed")
        repo = self._mod_repo_dir(appid, mod_name)
        # Filesystem errors (ENOSPC, EACCES, a card pulled mid-copy) become
        # StoreError so callers only ever handle one exception type. An enable
        # that dies partway leaves the mod in the "partial" state, which
        # list_mods reports and a second toggle repairs.
        try:
            if enabled:
                # Verified up front, before anything is written: a mod with a
                # missing stored copy can only ever be half-deployed, and
                # failing before the first copy keeps the game untouched.
                missing = next(
                    (
                        item["src"]
                        for item in deploy
                        if not (repo / item["src"]).is_file()
                    ),
                    None,
                )
                if missing is not None:
                    raise StoreError(
                        f'the stored copy of "{missing}" is missing — delete '
                        "the mod and import it again"
                    )
                for item in deploy:
                    dst_abs = game.install_dir / item["dst"]
                    dst_abs.parent.mkdir(parents=True, exist_ok=True)
                    backup = self._backup_path(appid, item["dst"])
                    # Only the first enable backs up: a re-enable would
                    # otherwise back up our own copy and lose the original.
                    if (
                        item.get("overwrite") == "backup"
                        and dst_abs.exists()
                        and not backup.exists()
                    ):
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(dst_abs), str(backup))
                    # Overwriting is safe: the import-time claim map and the
                    # refuse check ensure any file already at this path is
                    # ModDock's own (possibly stale) copy, so enable doubles
                    # as repair and is idempotent.
                    shutil.copy2(repo / item["src"], dst_abs)
            else:
                for item in deploy:
                    self._recall(appid, game, item["dst"])
        except OSError as exc:
            raise StoreError(str(exc)) from exc

    def _recall(self, appid: str, game: UEGameInfo, dst: str) -> None:
        """Undo one deployed item: restore the backup, or just remove ours.

        Only the file itself is touched — directories are left in place
        because they are shared with the game and with other mods.
        """
        dst_abs = game.install_dir / dst
        backup = self._backup_path(appid, dst)
        if backup.is_file():
            dst_abs.unlink(missing_ok=True)
            dst_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(dst_abs))
        else:
            dst_abs.unlink(missing_ok=True)

    def delete_mod(
        self, appid: str, game: UEGameInfo | None, mod_name: str
    ) -> None:
        manifest = self._load_manifest(appid)
        entry = manifest["mods"].get(mod_name)
        if entry is None:
            raise StoreError(f'unknown mod "{mod_name}"')
        usable_game = (
            game if game is not None and game.install_dir is not None else None
        )
        deploy = self._entry_deploy(entry, usable_game) or []
        repo = self._mod_repo_dir(appid, mod_name)
        try:
            for item in deploy:
                if usable_game is not None:
                    self._recall(appid, usable_game, item["dst"])
                # A backup can outlive the game it was taken from (the install
                # dir may be gone, or the recall above may have found the
                # destination's parent missing); nothing else will ever
                # restore it, so it goes with the mod.
                self._backup_path(appid, item["dst"]).unlink(missing_ok=True)
            shutil.rmtree(repo, ignore_errors=True)
        except OSError as exc:
            # The manifest entry is deliberately kept so the mod stays known
            # and the user can retry the delete.
            raise StoreError(str(exc)) from exc
        del manifest["mods"][mod_name]
        self._save_manifest(appid, manifest)
