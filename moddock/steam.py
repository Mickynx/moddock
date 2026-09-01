"""Steam library discovery: locate libraries and installed games.

VDF/ACF files are simple quoted key-value trees; we only need flat
key-value extraction, so a regex parser is sufficient and avoids a
third-party dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STEAM_ROOT_CANDIDATES = (
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
)

_KV_RE = re.compile(r'"([^"]+)"\s+"([^"]*)"')


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    install_dir: Path


def parse_vdf_library_paths(text: str) -> list[str]:
    return [value for key, value in _KV_RE.findall(text) if key == "path"]


def parse_acf(text: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for key, value in _KV_RE.findall(text):
        kv.setdefault(key, value)
    return kv


def find_steam_root() -> Path | None:
    for candidate in STEAM_ROOT_CANDIDATES:
        if (candidate / "steamapps").is_dir():
            return candidate
    return None


def discover_libraries(steam_root: Path) -> list[Path]:
    libraries: list[Path] = []
    if (steam_root / "steamapps").is_dir():
        libraries.append(steam_root)
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for raw in parse_vdf_library_paths(text):
            path = Path(raw)
            if path not in libraries and (path / "steamapps").is_dir():
                libraries.append(path)
    return libraries


def list_installed_games(libraries: list[Path]) -> list[SteamGame]:
    games: list[SteamGame] = []
    seen: set[str] = set()
    for library in libraries:
        steamapps = library / "steamapps"
        for acf in sorted(steamapps.glob("appmanifest_*.acf")):
            kv = parse_acf(acf.read_text(encoding="utf-8", errors="replace"))
            appid = kv.get("appid")
            name = kv.get("name")
            installdir = kv.get("installdir")
            if not appid or not name or not installdir or appid in seen:
                continue
            install_dir = steamapps / "common" / installdir
            if install_dir.is_dir():
                seen.add(appid)
                games.append(SteamGame(appid, name, install_dir))
    return games
