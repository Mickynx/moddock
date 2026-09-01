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

# Ceiling on what a single archive may expand to. Extraction goes to a scratch
# directory, so an archive claiming hundreds of gigabytes must be refused before
# it fills the device (or, worse, RAM when the scratch space is tmpfs-backed).
MAX_UNCOMPRESSED = 8 * 1024**3  # 8 GiB
# Timeout for the external .7z extractor: a wedged or interactively prompting
# binary must not hang the import forever.
SEVENZIP_TIMEOUT = 300  # seconds

# Directory to place extraction scratch dirs in; None means the system default.
# main.py points this at the plugin's own data directory, because /tmp on
# SteamOS/Bazzite is tmpfs (RAM) and a multi-gigabyte mod would exhaust it.
TEMP_ROOT: Path | None = None


def _scratch_dir(prefix: str) -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(
        prefix=prefix, dir=str(TEMP_ROOT) if TEMP_ROOT else None
    )


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


def _verify_extraction_tree(dest: Path) -> None:
    """Reject anything suspicious in the tree an extractor wrote under `dest`.

    The zip branch pre-checks member names, but the .7z branch delegates to
    whatever bsdtar/7z binary is on the system, so the result is verified after
    the fact for both. Symlinks are rejected outright: a mod archive has no
    legitimate use for them, and one can redirect a later copy or install step
    onto an arbitrary path.

    This walks only the tree it can see under `dest`; a file an external
    extractor wrote somewhere else entirely is invisible here, so containment
    for the .7z path also relies on those tools' own member sanitization
    (bsdtar and 7z both refuse absolute and `..` member paths by default).
    """
    root = dest.resolve()
    for entry in sorted(dest.rglob("*")):
        if entry.is_symlink():
            raise ImportProblem(
                f"archive contains a symlink, which is not allowed: "
                f"{entry.relative_to(dest)}"
            )
        if not entry.resolve().is_relative_to(root):
            raise ImportProblem(
                f"archive wrote outside the extraction directory: "
                f"{entry.relative_to(dest)}"
            )


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
                total = sum(info.file_size for info in zf.infolist())
                if total > MAX_UNCOMPRESSED:
                    raise ImportProblem(
                        f"archive would uncompress to "
                        f"{total / 1024**3:.1f} GiB, over the "
                        f"{MAX_UNCOMPRESSED / 1024**3:.0f} GiB limit"
                    )
                zf.extractall(dest)
        except zipfile.BadZipFile as exc:
            raise ImportProblem(f"corrupt zip file: {exc}") from exc
        except (RuntimeError, NotImplementedError) as exc:
            # zipfile signals an encrypted member with RuntimeError and an
            # unknown compression method with NotImplementedError; both are
            # ordinary bad uploads, not bugs.
            raise ImportProblem(f"zip file cannot be extracted: {exc}") from exc
        _verify_extraction_tree(dest)
    elif suffix == ".7z":
        command = _find_7z_command(archive, dest)
        if command is None:
            raise ImportProblem(
                ".7z extraction needs bsdtar or 7z installed on the system"
            )
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=SEVENZIP_TIMEOUT
            )
        except subprocess.TimeoutExpired as exc:
            raise ImportProblem(
                f"7z extraction timed out after {SEVENZIP_TIMEOUT}s"
            ) from exc
        if result.returncode != 0:
            raise ImportProblem(f"7z extraction failed: {result.stderr.strip()}")
        _verify_extraction_tree(dest)
    else:
        raise ImportProblem(f"unsupported format: {suffix or archive.name}")


def _collect(path: Path, workdir: Path) -> tuple[list[Path], list[str]]:
    """Extract or accept `path`, returning (mod files, validation errors)."""
    if not path.is_file():
        raise ImportProblem(f"file not found: {path.name}")
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
    # Contract: this classifier never raises. It runs once per inbox entry, so
    # any escaping exception would blank the whole inbox listing instead of
    # marking one bad upload. Archive libraries reach well past ImportProblem
    # and OSError (zipfile alone raises RuntimeError for an encrypted member
    # and NotImplementedError for an unknown compression method), so every
    # exception becomes a per-entry error string.
    try:
        with _scratch_dir("moddock-inspect-") as tmp:
            files, errors = _collect(path, Path(tmp))
    except Exception as exc:  # noqa: BLE001 - classifier must not raise
        return "error", str(exc)
    if errors:
        return "error", "; ".join(errors)
    return "ready", f"{len(files)} mod file(s)"


def ingest(path: Path, dest: Path) -> list[str]:
    # Contract: every failure surfaces as ImportProblem, so filesystem errors
    # (missing file, ENOSPC, EACCES) are wrapped rather than leaked.
    try:
        with _scratch_dir("moddock-ingest-") as tmp:
            files, errors = _collect(path, Path(tmp))
            if errors:
                raise ImportProblem("; ".join(errors))
            names = [f.name for f in files]
            if len(set(names)) != len(names):
                raise ImportProblem("archive contains duplicate mod file names")
            dest.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, dest / f.name)
    except OSError as exc:
        raise ImportProblem(str(exc)) from exc
    except (RuntimeError, NotImplementedError) as exc:
        # Belt and braces: extract_archive already wraps what zipfile throws,
        # but any other archive backend must not break the "all failures are
        # ImportProblem" contract callers (ModStore) rely on.
        raise ImportProblem(str(exc)) from exc
    return sorted(names)
