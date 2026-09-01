from pathlib import Path

from moddock.adapters.unreal import MODS_DIR_NAME, detect_ue_game


def _make_ue_game(
    root: Path,
    project: str = "SB",
    iostore: bool = False,
    shipping_exe: bool = True,
) -> Path:
    (root / "Engine" / "Binaries").mkdir(parents=True)
    paks = root / project / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / f"{project}-Windows.pak").touch()
    if iostore:
        (paks / f"{project}-Windows.utoc").touch()
        (paks / f"{project}-Windows.ucas").touch()
    if shipping_exe:
        binaries = root / project / "Binaries" / "Win64"
        binaries.mkdir(parents=True)
        (binaries / f"{project}-Win64-Shipping.exe").touch()
    return root


def test_detects_classic_ue_game(tmp_path):
    _make_ue_game(tmp_path, project="SB")
    info = detect_ue_game(tmp_path)
    assert info is not None
    assert info.project_name == "SB"
    assert info.paks_dir == tmp_path / "SB" / "Content" / "Paks"
    assert info.mods_dir == info.paks_dir / MODS_DIR_NAME
    assert info.is_iostore is False
    assert info.has_shipping_exe is True


def test_detects_iostore(tmp_path):
    _make_ue_game(tmp_path, iostore=True)
    info = detect_ue_game(tmp_path)
    assert info is not None
    assert info.is_iostore is True


def test_installed_utoc_mod_does_not_flip_iostore(tmp_path):
    # ~mods is written by ModDock itself; a mod's .utoc must not be mistaken
    # for evidence that the base game is packaged with IoStore.
    _make_ue_game(tmp_path, project="SB", iostore=False)
    mods = tmp_path / "SB" / "Content" / "Paks" / MODS_DIR_NAME
    mods.mkdir()
    (mods / "CoolMod_P.pak").touch()
    (mods / "CoolMod_P.utoc").touch()
    (mods / "CoolMod_P.ucas").touch()
    info = detect_ue_game(tmp_path)
    assert info is not None
    assert info.is_iostore is False


def test_iostore_detected_in_nested_platform_dir(tmp_path):
    # Some titles nest base paks under Paks/<Platform>/, so recursion outside
    # ~mods must be preserved.
    _make_ue_game(tmp_path, project="SB", iostore=False)
    nested = tmp_path / "SB" / "Content" / "Paks" / "Windows"
    nested.mkdir()
    (nested / "SB-Windows.utoc").touch()
    (nested / "SB-Windows.ucas").touch()
    info = detect_ue_game(tmp_path)
    assert info is not None
    assert info.is_iostore is True


def test_rejects_game_without_engine_dir(tmp_path):
    (tmp_path / "SB" / "Content" / "Paks").mkdir(parents=True)
    assert detect_ue_game(tmp_path) is None


def test_rejects_game_without_paks(tmp_path):
    (tmp_path / "Engine").mkdir()
    (tmp_path / "SB" / "Content").mkdir(parents=True)
    assert detect_ue_game(tmp_path) is None


def test_rejects_two_project_candidates(tmp_path):
    _make_ue_game(tmp_path, project="A")
    (tmp_path / "B" / "Content" / "Paks").mkdir(parents=True)
    assert detect_ue_game(tmp_path) is None


def test_missing_shipping_exe_is_soft_signal(tmp_path):
    _make_ue_game(tmp_path, shipping_exe=False)
    info = detect_ue_game(tmp_path)
    assert info is not None
    assert info.has_shipping_exe is False


def test_nonexistent_dir(tmp_path):
    assert detect_ue_game(tmp_path / "nope") is None
