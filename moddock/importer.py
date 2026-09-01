"""Import pipeline: archive extraction, mod-file scanning, pak-set validation.

A "pak set" is the group of same-stem files an IoStore mod ships as
(.pak/.utoc/.ucas). When a .utoc or .ucas is present, all three members
must exist; a standalone .pak is a valid classic mod on its own.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

MOD_FILE_EXTS = {".pak", ".utoc", ".ucas"}
ARCHIVE_EXTS = {".zip", ".7z"}


class ImportProblem(Exception):
    """User-facing import failure; str(exc) is shown in the inbox."""


def scan_mod_files(root: Path) -> tuple[list[Path], list[str]]:
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in MOD_FILE_EXTS
    )
    errors: list[str] = []
    if not files:
        errors.append("no .pak/.utoc/.ucas files found")
        return files, errors
    by_stem: dict[str, set[str]] = {}
    for p in files:
        by_stem.setdefault(p.stem, set()).add(p.suffix.lower())
    for stem, exts in sorted(by_stem.items()):
        if exts & {".utoc", ".ucas"}:
            missing = {".pak", ".utoc", ".ucas"} - exts
            if missing:
                errors.append(
                    f"{stem}: incomplete pak set, missing "
                    + ", ".join(sorted(missing))
                )
    return files, errors


def _find_7z_command(archive: Path, dest: Path) -> list[str] | None:
    if shutil.which("bsdtar"):
        return ["bsdtar", "-xf", str(archive), "-C", str(dest)]
    for tool in ("7z", "7za", "7zz"):
        if shutil.which(tool):
            return [tool, "x", "-y", f"-o{dest}", str(archive)]
    return None


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    target = (dest / member).resolve()
                    if not target.is_relative_to(dest.resolve()):
                        raise ImportProblem(
                            f"archive contains an unsafe path: {member}"
                        )
                zf.extractall(dest)
        except zipfile.BadZipFile as exc:
            raise ImportProblem(f"corrupt zip file: {exc}") from exc
    elif suffix == ".7z":
        command = _find_7z_command(archive, dest)
        if command is None:
            raise ImportProblem(
                ".7z extraction needs bsdtar or 7z installed on the system"
            )
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise ImportProblem(f"7z extraction failed: {result.stderr.strip()}")
    else:
        raise ImportProblem(f"unsupported format: {suffix or archive.name}")


def _collect(path: Path, workdir: Path) -> tuple[list[Path], list[str]]:
    """Extract or accept `path`, returning (mod files, validation errors)."""
    suffix = path.suffix.lower()
    if suffix in MOD_FILE_EXTS:
        return scan_mod_files_for_single(path)
    if suffix in ARCHIVE_EXTS:
        extract_archive(path, workdir)
        return scan_mod_files(workdir)
    raise ImportProblem(f"unsupported format: {suffix or path.name}")


def scan_mod_files_for_single(path: Path) -> tuple[list[Path], list[str]]:
    if path.suffix.lower() == ".pak":
        return [path], []
    return [path], [
        f"{path.stem}: a bare {path.suffix} needs its matching pak set "
        "— upload the archive instead"
    ]


def inspect_upload(path: Path) -> tuple[str, str]:
    try:
        with tempfile.TemporaryDirectory(prefix="moddock-inspect-") as tmp:
            files, errors = _collect(path, Path(tmp))
    except ImportProblem as exc:
        return "error", str(exc)
    if errors:
        return "error", "; ".join(errors)
    return "ready", f"{len(files)} mod file(s)"


def ingest(path: Path, dest: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="moddock-ingest-") as tmp:
        files, errors = _collect(path, Path(tmp))
        if errors:
            raise ImportProblem("; ".join(errors))
        names = [f.name for f in files]
        if len(set(names)) != len(names):
            raise ImportProblem("archive contains duplicate mod file names")
        dest.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest / f.name)
    return sorted(names)
