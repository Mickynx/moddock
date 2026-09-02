import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

from moddock.importer import (
    ImportProblem,
    _verify_extraction_tree,
    ingest,
    inspect_upload,
    scan_mod_files,
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


def test_scan_complete_iostore_set(tmp_path):
    _touch(tmp_path, "mod.pak", "mod.utoc", "mod.ucas")
    files, errors = scan_mod_files(tmp_path)
    assert [f.name for f in files] == ["mod.pak", "mod.ucas", "mod.utoc"]
    assert errors == []


def test_scan_standalone_pak_is_valid(tmp_path):
    _touch(tmp_path, "legacy.pak")
    files, errors = scan_mod_files(tmp_path)
    assert errors == []


def test_scan_missing_ucas_fails(tmp_path):
    _touch(tmp_path, "mod.pak", "mod.utoc")
    _, errors = scan_mod_files(tmp_path)
    assert errors and ".ucas" in errors[0]


def test_scan_nested_and_ignores_junk(tmp_path):
    _touch(tmp_path, "sub/dir/mod.pak", "readme.txt", "preview.png")
    files, errors = scan_mod_files(tmp_path)
    assert [f.name for f in files] == ["mod.pak"]
    assert errors == []


def test_scan_no_mod_files(tmp_path):
    _touch(tmp_path, "readme.txt")
    _, errors = scan_mod_files(tmp_path)
    assert errors


def test_inspect_zip_ready(tmp_path):
    archive = _make_zip(tmp_path / "CoolMod.zip", "mod.pak", "mod.utoc", "mod.ucas")
    status, detail = inspect_upload(archive)
    assert status == "ready"
    assert "3" in detail  # mentions the file count


def test_inspect_rar_unsupported(tmp_path):
    rar = tmp_path / "mod.rar"
    rar.write_bytes(b"Rar!")
    status, reason = inspect_upload(rar)
    assert status == "error"
    assert "unsupported" in reason.lower()


def test_inspect_bare_pak(tmp_path):
    _touch(tmp_path, "single.pak")
    status, _ = inspect_upload(tmp_path / "single.pak")
    assert status == "ready"


def test_inspect_zip_missing_member(tmp_path):
    archive = _make_zip(tmp_path / "broken.zip", "mod.pak", "mod.utoc")
    status, reason = inspect_upload(archive)
    assert status == "error"
    assert ".ucas" in reason


def test_ingest_zip_flattens(tmp_path):
    archive = _make_zip(
        tmp_path / "CoolMod.zip", "nested/mod.pak", "nested/mod.utoc", "nested/mod.ucas"
    )
    dest = tmp_path / "store"
    names = ingest(archive, dest)
    assert sorted(names) == ["mod.pak", "mod.ucas", "mod.utoc"]
    assert (dest / "mod.pak").is_file()


def test_ingest_bare_file(tmp_path):
    _touch(tmp_path, "single.pak")
    dest = tmp_path / "store"
    assert ingest(tmp_path / "single.pak", dest) == ["single.pak"]


def test_ingest_duplicate_basenames_rejected(tmp_path):
    archive = _make_zip(tmp_path / "dup.zip", "a/mod.pak", "b/mod.pak")
    with pytest.raises(ImportProblem):
        ingest(archive, tmp_path / "store")


def test_ingest_zip_blocks_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.pak", "x")
    with pytest.raises(ImportProblem):
        ingest(archive, tmp_path / "store")


def test_inspect_missing_file_reports_error(tmp_path):
    status, reason = inspect_upload(tmp_path / "ghost.pak")
    assert status == "error"
    assert "not found" in reason.lower()


def test_inspect_missing_archive_reports_error(tmp_path):
    status, reason = inspect_upload(tmp_path / "ghost.zip")
    assert status == "error"
    assert "not found" in reason.lower()


def test_ingest_missing_archive_raises_import_problem(tmp_path):
    with pytest.raises(ImportProblem):
        ingest(tmp_path / "ghost.zip", tmp_path / "store")


def test_ingest_wraps_os_error_as_import_problem(tmp_path):
    _touch(tmp_path, "single.pak")
    dest = tmp_path / "store"
    dest.write_bytes(b"a file where the store should be")
    with pytest.raises(ImportProblem):
        ingest(tmp_path / "single.pak", dest)


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


def test_inspect_encrypted_zip_reports_error(tmp_path):
    """An encrypted member makes zipfile raise RuntimeError, not ImportProblem.

    inspect_upload is a classifier and must never raise: a single bad upload
    would otherwise take down the whole inbox listing.
    """
    archive = _make_encrypted_zip(tmp_path / "locked.zip")
    status, reason = inspect_upload(archive)
    assert status == "error"
    assert "encrypted" in reason.lower()


def test_inspect_unsupported_compression_reports_error(tmp_path, monkeypatch):
    archive = _make_zip(tmp_path / "weird.zip", "mod.pak")

    def boom(self, *args, **kwargs):
        raise NotImplementedError("that compression method is not supported")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    status, reason = inspect_upload(archive)
    assert status == "error"
    assert "not supported" in reason.lower()


def test_ingest_encrypted_zip_raises_import_problem(tmp_path):
    archive = _make_encrypted_zip(tmp_path / "locked.zip")
    with pytest.raises(ImportProblem) as exc:
        ingest(archive, tmp_path / "store")
    assert "encrypted" in str(exc.value).lower()


def test_zip_uncompressed_size_cap(tmp_path, monkeypatch):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x" * 4096)
    monkeypatch.setattr("moddock.importer.MAX_UNCOMPRESSED", 1024)
    with pytest.raises(ImportProblem) as exc:
        ingest(archive, tmp_path / "store")
    assert "uncompress" in str(exc.value).lower()


def test_temp_root_is_honoured(tmp_path, monkeypatch):
    """Extraction must be able to avoid /tmp, which is RAM-backed on Bazzite."""
    root = tmp_path / "moddock-tmp"
    root.mkdir()
    monkeypatch.setattr("moddock.importer.TEMP_ROOT", root)
    archive = _make_zip(tmp_path / "mod.zip", "mod.pak")
    ingest(archive, tmp_path / "store")
    # The directory is cleaned up, but it must have been created under TEMP_ROOT
    # -- verified by watching what tempfile was asked for.
    seen: list[str | None] = []
    real = tempfile.TemporaryDirectory

    def spy(*args, **kwargs):
        seen.append(kwargs.get("dir"))
        return real(*args, **kwargs)

    monkeypatch.setattr("moddock.importer.tempfile.TemporaryDirectory", spy)
    ingest(archive, tmp_path / "store2")
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
        ingest(archive, tmp_path / "store")
    assert "timed out" in str(exc.value).lower()


def test_pak_set_errors_pure_helper():
    from moddock.importer import pak_set_errors

    assert pak_set_errors(["a/mod.pak", "a/mod.utoc", "a/mod.ucas"]) == []
    assert pak_set_errors(["solo.pak"]) == []
    errors = pak_set_errors(["mod.pak", "mod.utoc"])
    assert errors and ".ucas" in errors[0]
