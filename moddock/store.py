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

import filecmp
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

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
    def _check_destinations(mod_name: str, items: list[dict]) -> None:
        """Reject destinations that do not stay inside the game root.

        Every dst is joined onto the install dir by enable, disable and
        delete, so a manifest that was hand-edited, half-written or corrupted
        on disk could otherwise steer those operations at an arbitrary path.
        The manifest is data read back from disk, not a trusted input: an
        absolute, empty or `..`-bearing dst fails the whole entry.
        """
        for item in items:
            dst = str(item.get("dst") or "").replace("\\", "/")
            parts = PurePosixPath(dst).parts
            if not dst or dst.startswith("/") or ".." in parts:
                raise StoreError(
                    f'manifest entry for "{mod_name}" is corrupted — delete '
                    "the mod and import it again"
                )

    def _entry_deploy(
        self, mod_name: str, entry: dict, game: UEGameInfo | None
    ) -> list[dict] | None:
        """Deploy items for an entry; synthesizes them for legacy v1 entries.

        v1 manifests recorded a flat "files" list and always installed into
        the game's ~mods directory. That is exactly what the ue-paks-mods
        recipe produces, so the equivalent deploy list can be reconstructed
        on the fly from the game's anchors — no migration pass, and a
        rollback to v1 keeps working. Without a detected game there are no
        anchors to resolve against, so such an entry is unusable (repo-side
        delete still works).

        Raises StoreError for an entry whose destinations are not usable.
        """
        if "deploy" in entry:
            items = entry["deploy"]
        elif game is None or game.install_dir is None:
            return None
        else:
            paks_rel = game.anchor_map()["paks_dir"]
            items = [
                {
                    "src": f,
                    "dst": f"{paks_rel}/{MODS_DIR_NAME}/{f}",
                    "overwrite": "refuse",
                }
                for f in entry["files"]
            ]
        self._check_destinations(mod_name, items)
        return items

    def _claimed(
        self, manifest: dict, game: UEGameInfo | None
    ) -> dict[str, str]:
        """Destination -> owning mod name, over every entry in the manifest.

        Keyed case-insensitively, for the same reason apply_recipe folds case
        within one recipe: a Steam library on exFAT/NTFS treats "Mod.pak" and
        "mod.pak" as one file, so two mods spelling a destination differently
        would silently clobber each other there.
        """
        claimed: dict[str, str] = {}
        for name, entry in sorted(manifest["mods"].items()):
            # A corrupted entry aborts the import that is asking: its claims
            # cannot be read, so no new mod can be proven collision-free
            # against it. The message names the mod to delete.
            for item in self._entry_deploy(name, entry, game) or []:
                claimed[item["dst"].lower()] = name
        return claimed

    def _is_deployed(self, appid: str, game: UEGameInfo, item: dict) -> bool:
        """Is this item's file currently our copy sitting at the destination?

        For an item that displaced one of the game's own files, presence at
        the destination proves nothing — after a disable the game's restored
        original occupies exactly that path. The parked backup is the second
        witness: it exists only while our copy is deployed over it. Both are
        required, because a backup outlives the game it was taken from — after
        an uninstall/reinstall it is still parked while the deployed file went
        with the old install dir.
        """
        deployed = (game.install_dir / item["dst"]).is_file()
        if item.get("overwrite") == "backup" and item.get("displaced"):
            return deployed and self._backup_path(appid, item["dst"]).is_file()
        return deployed

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
            owner = claimed.get(item.dst.lower())
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
            try:
                deploy = self._entry_deploy(name, entry, game)
            except StoreError:
                # One unusable entry must not blank the whole list: the mod is
                # shown as it stands so the user can see it and delete it.
                mods.append(
                    {
                        "name": name,
                        "state": "disabled",
                        "recipe_name": "corrupted entry",
                    }
                )
                continue
            state = "disabled"
            if game is not None and game.install_dir is not None and deploy:
                present = sum(
                    1 for item in deploy if self._is_deployed(appid, game, item)
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
            else self._entry_deploy(mod_name, entry, game)
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
                changed = False
                for item in deploy:
                    dst_abs = game.install_dir / item["dst"]
                    dst_abs.parent.mkdir(parents=True, exist_ok=True)
                    if item.get("overwrite") == "backup":
                        changed |= self._park_original(appid, item, dst_abs)
                    # Overwriting is safe: the import-time claim map and the
                    # refuse check ensure any file already at this path is
                    # ModDock's own (possibly stale) copy, so enable doubles
                    # as repair and is idempotent.
                    shutil.copy2(repo / item["src"], dst_abs)
            else:
                changed = False
                for item in deploy:
                    changed |= self._recall(appid, game, repo, item)
            if changed and "deploy" in entry:
                self._save_manifest(appid, manifest)
        except OSError as exc:
            raise StoreError(str(exc)) from exc

    def _park_original(self, appid: str, item: dict, dst_abs: Path) -> bool:
        """Move a `backup` item's pre-existing destination file out of the way.

        Also re-decides the item's `displaced` flag, which is persisted
        provenance about the GAME's own file rather than derived enable-state:
        it records that this destination holds something of the game's that we
        must never unlink. Three states, checked in this order:

        - a backup is already parked -> the original is safe; never overwrite
          it (that would bury the true original under our own copy), flag on;
        - nothing parked but the destination is occupied -> that file is the
          game's, park it, flag on. This covers the first enable and every
          re-enable after a disable put the original back;
        - nothing parked and nothing at the destination -> there is nothing
          left to protect, flag off. The item degrades to fresh-path
          semantics, so a later disable removes our copy normally.

        Returns whether the flag changed and the manifest needs saving.
        """
        # A directory can be moved into the backup tree, but nothing that
        # follows can cope with it: deployed-state, recall and delete all test
        # the backup with is_file(), so the mod would be wedged — enable stuck
        # at partial, disable a no-op, delete unable to unlink. Refuse before
        # anything moves, while the game directory is still intact.
        if dst_abs.is_dir():
            raise StoreError(
                f'"{item["dst"]}" is a directory in the game — this install '
                "method cannot replace it"
            )
        backup = self._backup_path(appid, item["dst"])
        if backup.exists():
            displaced = True
        elif dst_abs.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst_abs), str(backup))
            displaced = True
        else:
            displaced = False
        if bool(item.get("displaced")) == displaced:
            return False
        item["displaced"] = displaced
        return True

    def _recall(
        self, appid: str, game: UEGameInfo, repo: Path, item: dict
    ) -> bool:
        """Undo one deployed item: restore the backup, or just remove ours.

        The parked backup FILE is the first authority, ahead of the flag: the
        move that parks it necessarily happens before the manifest can record
        it, so a crash in that window leaves a real original with no flag
        pointing at it. Whenever a backup file exists it goes back, and the
        flag is repaired on the way so the next recall knows.

        With no backup parked, the flag decides: an item that displaced
        something has normally had its original restored by an earlier recall
        and must be left alone. That case is not always benign, though — the
        backup may have been deleted by hand while our copy was still
        deployed, which would strand our file in the game forever. The
        destination's CONTENT settles it: byte-identical to the stored copy
        means it is ours and goes; anything else is assumed to be the restored
        original and stays. Any other item's destination holds our own copy
        and is unlinked outright.

        Only the file itself is touched — directories are left in place
        because they are shared with the game and with other mods.

        Returns whether the flag changed and the manifest needs saving.
        """
        dst_abs = game.install_dir / item["dst"]
        backup = self._backup_path(appid, item["dst"])
        if item.get("overwrite") == "backup":
            if backup.is_file():
                dst_abs.unlink(missing_ok=True)
                dst_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(dst_abs))
                if item.get("displaced"):
                    return False
                item["displaced"] = True
                return True
            if item.get("displaced"):
                src = repo / item["src"]
                if (
                    dst_abs.is_file()
                    and src.is_file()
                    and filecmp.cmp(dst_abs, src, shallow=False)
                ):
                    dst_abs.unlink()  # our orphaned copy, not the original
                return False
        dst_abs.unlink(missing_ok=True)
        return False

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
        try:
            deploy = self._entry_deploy(mod_name, entry, usable_game) or []
        except StoreError:
            # A corrupted entry names no destination anyone may act on, and
            # delete is the only way out of it. Clean up what is unambiguously
            # ours — the repository and the manifest entry — and touch nothing
            # under the game or the backup tree.
            deploy = []
        repo = self._mod_repo_dir(appid, mod_name)
        try:
            for item in deploy:
                if usable_game is None:
                    # No visible install dir: an unmounted SD card looks
                    # exactly like an uninstalled game from here, and the
                    # parked original may still be the only copy of a file the
                    # game dir (invisible, not gone) still needs. Leaving a
                    # stale backup behind is the cheap mistake; deleting a live
                    # one is not.
                    continue
                self._recall(appid, usable_game, repo, item)
                # Whatever the recall did not put back is debris: the original
                # is either restored or was never the game's. Nothing will ever
                # restore it now, so it goes with the mod.
                backup = self._backup_path(appid, item["dst"])
                if backup.is_file():
                    backup.unlink()
            shutil.rmtree(repo, ignore_errors=True)
            del manifest["mods"][mod_name]
            self._save_manifest(appid, manifest)
        except OSError as exc:
            # The manifest entry is deliberately kept so the mod stays known
            # and the user can retry the delete. By the time the manifest
            # write runs the repository is already gone, so a failure there
            # would otherwise strand the mod: listed nowhere, yet still
            # holding its claim on every destination.
            raise StoreError(str(exc)) from exc
