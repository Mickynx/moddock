"""Unreal Engine (UE4/UE5) game detection.

A UE game install looks like:
    <install>/Engine/...
    <install>/<Project>/Content/Paks/*.pak [+ .utoc/.ucas for IoStore]
Mods are loose pak files loaded from Paks/~mods.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MODS_DIR_NAME = "~mods"


@dataclass(frozen=True)
class UEGameInfo:
    project_name: str
    paks_dir: Path
    mods_dir: Path
    is_iostore: bool
    has_shipping_exe: bool
    install_dir: Path | None = None
    win64_dir: Path | None = None

    def anchor_map(self) -> dict[str, str | None]:
        """Game-root-relative anchor paths for the recipe engine."""
        if self.install_dir is None:
            raise ValueError("anchor_map() needs install_dir")

        def rel(path: Path) -> str:
            return path.relative_to(self.install_dir).as_posix()

        return {
            "game_root": "",
            "paks_dir": rel(self.paks_dir),
            "win64_dir": rel(self.win64_dir) if self.win64_dir else None,
        }


def detect_ue_game(install_dir: Path) -> UEGameInfo | None:
    if not (install_dir / "Engine").is_dir():
        return None
    candidates: list[tuple[str, Path]] = []
    for child in sorted(install_dir.iterdir()):
        if child.name == "Engine" or not child.is_dir():
            continue
        paks = child / "Content" / "Paks"
        if paks.is_dir():
            candidates.append((child.name, paks))
    if len(candidates) != 1:
        return None
    project_name, paks_dir = candidates[0]
    # Recurse (some titles nest base paks under Paks/<Platform>/) but skip
    # ~mods: that subtree is written by ModDock, and an installed IoStore mod
    # must not be mistaken for evidence about how the base game is packaged.
    is_iostore = any(
        MODS_DIR_NAME not in p.relative_to(paks_dir).parts
        for p in paks_dir.rglob("*.utoc")
    )
    binaries = install_dir / project_name / "Binaries"
    has_shipping_exe = binaries.is_dir() and (
        next(binaries.rglob("*-Win64-Shipping.exe"), None) is not None
    )
    win64 = install_dir / project_name / "Binaries" / "Win64"
    return UEGameInfo(
        project_name=project_name,
        paks_dir=paks_dir,
        mods_dir=paks_dir / MODS_DIR_NAME,
        is_iostore=is_iostore,
        has_shipping_exe=has_shipping_exe,
        install_dir=install_dir,
        win64_dir=win64 if win64.is_dir() else None,
    )
