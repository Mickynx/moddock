"""Plugin settings persistence (managed game list, upload port)."""

from __future__ import annotations

import json
from pathlib import Path


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {"managed_games": [], "upload_port": 8765}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            except (ValueError, OSError):
                # Corrupt settings fall back to defaults. ValueError covers both
                # json.JSONDecodeError and UnicodeDecodeError (undecodable bytes,
                # e.g. a crash mid-write splitting a multi-byte sequence).
                pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    @property
    def managed_games(self) -> list[dict]:
        return list(self._data["managed_games"])

    def add_game(self, appid: str, name: str, install_dir: str) -> None:
        if any(g["appid"] == appid for g in self._data["managed_games"]):
            return
        self._data["managed_games"].append(
            {"appid": appid, "name": name, "install_dir": install_dir}
        )
        self._save()

    def remove_game(self, appid: str) -> None:
        self._data["managed_games"] = [
            g for g in self._data["managed_games"] if g["appid"] != appid
        ]
        self._save()

    @property
    def upload_port(self) -> int:
        return int(self._data["upload_port"])

    def set_upload_port(self, port: int) -> None:
        self._data["upload_port"] = int(port)
        self._save()
