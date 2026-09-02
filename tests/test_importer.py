import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from moddock.importer import (
    ImportProblem,
    _verify_extraction_tree,
    extract_archive,
    ingest_tree,
    pak_set_errors,
)


def _touch(root: Path, *names: str) -> None:
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def _make_zip(path: Path, *names: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")
    return path


def test_pak_set_errors_pure_helper():
    assert pak_set_errors(["a/mod.pak", "a/mod.utoc", "a/mod.ucas"]) == []
    assert pak_set_errors(["solo.pak"]) == []
    errors = pak_set_errors(["mod.pak", "mod.utoc"])
    assert errors and ".ucas" in errors[0]


def test_pak_set_errors_ignores_non_mod_files():
    assert pak_set_errors(["readme.txt", "preview.png"]) == []


def test_extract_archive_rejects_unsupported_format(tmp_path):
    rar = tmp_path / "mod.rar"
    rar.write_bytes(b"Rar!")
    with pytest.raises(ImportProblem) as exc:
        extract_archive(rar, tmp_path / "out")
    assert "unsupported" in str(exc.value).lower()


def test_verify_extraction_tree_accepts_plain_files(tmp_path):
    _touch(tmp_path, "mod.pak", "sub/mod.utoc")
    _verify_extraction_tree(tmp_path)  # must not raise


def test_verify_extraction_tree_rejects_symlink_escape(tmp_path):
    dest = tmp_path / "extracted"
    dest.mkdir()
    outside = tmp_path / "secret.pak"
    outside.write_bytes(b"x")
    (dest / "link.pak").symlink_to(outside)
    with pytest.raises(ImportProblem) as exc:
        _verify_extraction_tree(dest)
    assert "link.pak" in str(exc.value)


def test_verify_extraction_tree_rejects_internal_symlink(tmp_path):
    dest = tmp_path / "extracted"
    dest.mkdir()
    real = dest / "mod.pak"
    real.write_bytes(b"x")
    (dest / "alias.pak").symlink_to(real)
    with pytest.raises(ImportProblem):
        _verify_extraction_tree(dest)


def _make_encrypted_zip(path: Path, name: str = "secret.pak") -> Path:
    """Write a zip whose members are flagged encrypted.

    zipfile can list such an archive but raises RuntimeError when extracting
    it. The encryption bit (general-purpose flag bit 0) is patched into both
    the local file headers and the central directory so the fixture needs no
    external `zip` binary.
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, "x" * 32)
    raw = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = 0
        while True:
            index = raw.find(signature, index)
            if index < 0:
                break
            raw[index + flag_offset] |= 0x01
            index += 4
    path.write_bytes(bytes(raw))
    return path


def test_ingest_tree_encrypted_zip_raises_import_problem(tmp_path):
    archive = _make_encrypted_zip(tmp_path / "locked.zip")
    with pytest.raises(ImportProblem) as exc:
        ingest_tree(archive, tmp_path / "repo")
    assert "encrypted" in str(exc.value).lower()


def test_ingest_tree_unsupported_compression_raises(tmp_path, monkeypatch):
    """An unknown compression method is a bad upload, not a bug.

    zipfile signals it with NotImplementedError, which must still reach the
    caller as ImportProblem.
    """
    archive = _make_zip(tmp_path / "weird.zip", "mod.pak")

    def boom(self, *args, **kwargs):
        raise NotImplementedError("that compression method is not supported")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    with pytest.raises(ImportProblem) as exc:
        ingest_tree(archive, tmp_path / "repo")
    assert "not supported" in str(exc.value).lower()


def test_ingest_tree_blocks_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.pak", "x")
    with pytest.raises(ImportProblem):
        ingest_tree(archive, tmp_path / "repo")


def test_zip_uncompressed_size_cap(tmp_path, monkeypatch):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x" * 4096)
    monkeypatch.setattr("moddock.importer.MAX_UNCOMPRESSED", 1024)
    with pytest.raises(ImportProblem) as exc:
        ingest_tree(archive, tmp_path / "repo")
    assert "uncompress" in str(exc.value).lower()


def test_temp_root_is_honoured(tmp_path, monkeypatch):
    """Extraction must be able to avoid /tmp, which is RAM-backed on Bazzite."""
    root = tmp_path / "moddock-tmp"
    root.mkdir()
    monkeypatch.setattr("moddock.importer.TEMP_ROOT", root)
    archive = _make_zip(tmp_path / "mod.zip", "mod.pak")
    ingest_tree(archive, tmp_path / "repo")
    # The directory is cleaned up, but it must have been created under TEMP_ROOT
    # -- verified by watching what tempfile was asked for.
    seen: list[str | None] = []
    real = tempfile.TemporaryDirectory

    def spy(*args, **kwargs):
        seen.append(kwargs.get("dir"))
        return real(*args, **kwargs)

    monkeypatch.setattr("moddock.importer.tempfile.TemporaryDirectory", spy)
    ingest_tree(archive, tmp_path / "repo2")
    assert seen == [str(root)]


def test_7z_extraction_timeout_is_reported(tmp_path, monkeypatch):
    archive = tmp_path / "mod.7z"
    archive.write_bytes(b"7z")
    monkeypatch.setattr(
        "moddock.importer._find_7z_command", lambda a, d: ["true"]
    )

    def timeout(*args, **kwargs):
        assert kwargs.get("timeout") == 300
        raise subprocess.TimeoutExpired(cmd="7z", timeout=300)

    monkeypatch.setattr("moddock.importer.subprocess.run", timeout)
    with pytest.raises(ImportProblem) as exc:
        ingest_tree(archive, tmp_path / "repo")
    assert "timed out" in str(exc.value).lower()


def test_ingest_tree_preserves_structure(tmp_path):
    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x")
        zf.writestr("Mods/My/scripts/main.lua", "x")
    dest = tmp_path / "repo"
    assert ingest_tree(archive, dest) == ["Mods/My/scripts/main.lua", "mod.pak"]
    assert (dest / "Mods/My/scripts/main.lua").is_file()


def test_ingest_tree_strips_wrapper_dirs(tmp_path):
    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Wrapper/Inner/mod.pak", "x")
        zf.writestr("Wrapper/Inner/readme.txt", "x")
    tree = ingest_tree(archive, tmp_path / "repo")
    assert tree == ["mod.pak", "readme.txt"]


def test_ingest_tree_bare_file(tmp_path):
    (tmp_path / "solo.pak").write_bytes(b"x")
    assert ingest_tree(tmp_path / "solo.pak", tmp_path / "repo") == ["solo.pak"]


def test_ingest_tree_missing_source_raises(tmp_path):
    with pytest.raises(ImportProblem) as exc:
        ingest_tree(tmp_path / "ghost.zip", tmp_path / "repo")
    assert "not found" in str(exc.value).lower()


def test_ingest_tree_empty_archive_raises(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w"):
        pass
    with pytest.raises(ImportProblem):
        ingest_tree(archive, tmp_path / "repo")


def test_ingest_tree_wraps_os_errors(tmp_path, monkeypatch):
    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("moddock.importer.shutil.copy2", boom)
    with pytest.raises(ImportProblem):
        ingest_tree(archive, tmp_path / "repo")


def test_ingest_tree_wraps_os_error_when_dest_is_a_file(tmp_path):
    (tmp_path / "solo.pak").write_bytes(b"x")
    dest = tmp_path / "repo"
    dest.write_bytes(b"a file where the repository should be")
    with pytest.raises(ImportProblem):
        ingest_tree(tmp_path / "solo.pak", dest)
