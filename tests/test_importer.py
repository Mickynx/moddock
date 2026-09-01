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
