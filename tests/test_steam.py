from pathlib import Path

from moddock.steam import (
    SteamGame,
    discover_libraries,
    list_installed_games,
    parse_acf,
    parse_vdf_library_paths,
)

LIBRARYFOLDERS_VDF = '''
"libraryfolders"
{
    "0"
    {
        "path"        "/home/user/.local/share/Steam"
        "label"        ""
    }
    "1"
    {
        "path"        "/run/media/mmcblk0p1"
        "label"        "sdcard"
    }
}
'''

APPMANIFEST_ACF = '''
"AppState"
{
    "appid"        "3489700"
    "name"        "Stellar Blade"
    "installdir"        "StellarBlade"
    "StateFlags"        "4"
}
'''


def test_parse_vdf_library_paths():
    assert parse_vdf_library_paths(LIBRARYFOLDERS_VDF) == [
        "/home/user/.local/share/Steam",
        "/run/media/mmcblk0p1",
    ]


def test_parse_acf():
    kv = parse_acf(APPMANIFEST_ACF)
    assert kv["appid"] == "3489700"
    assert kv["name"] == "Stellar Blade"
    assert kv["installdir"] == "StellarBlade"


def _make_library(root: Path, appid: str, name: str, installdir: str) -> None:
    steamapps = root / "steamapps"
    (steamapps / "common" / installdir).mkdir(parents=True)
    manifest = APPMANIFEST_ACF.replace("3489700", appid)
    manifest = manifest.replace("Stellar Blade", name)
    manifest = manifest.replace('"StellarBlade"', f'"{installdir}"')
    (steamapps / f"appmanifest_{appid}.acf").write_text(manifest)


def test_discover_libraries_includes_root_and_extra(tmp_path):
    steam_root = tmp_path / "Steam"
    sd_lib = tmp_path / "sd"
    _make_library(steam_root, "1", "A", "GameA")
    _make_library(sd_lib, "2", "B", "GameB")
    vdf = LIBRARYFOLDERS_VDF.replace(
        "/home/user/.local/share/Steam", str(steam_root)
    ).replace("/run/media/mmcblk0p1", str(sd_lib))
    (steam_root / "steamapps" / "libraryfolders.vdf").write_text(vdf)

    libs = discover_libraries(steam_root)
    assert libs == [steam_root, sd_lib]


def test_discover_libraries_skips_missing_paths(tmp_path):
    steam_root = tmp_path / "Steam"
    _make_library(steam_root, "1", "A", "GameA")
    vdf = LIBRARYFOLDERS_VDF.replace(
        "/home/user/.local/share/Steam", str(steam_root)
    )  # sd path left dangling
    (steam_root / "steamapps" / "libraryfolders.vdf").write_text(vdf)

    assert discover_libraries(steam_root) == [steam_root]


def test_list_installed_games(tmp_path):
    lib = tmp_path / "Steam"
    _make_library(lib, "3489700", "Stellar Blade", "StellarBlade")
    games = list_installed_games([lib])
    assert games == [
        SteamGame(
            appid="3489700",
            name="Stellar Blade",
            install_dir=lib / "steamapps" / "common" / "StellarBlade",
        )
    ]


def test_list_installed_games_skips_manifest_without_folder(tmp_path):
    lib = tmp_path / "Steam"
    _make_library(lib, "1", "Ghost", "GhostGame")
    import shutil

    shutil.rmtree(lib / "steamapps" / "common" / "GhostGame")
    assert list_installed_games([lib]) == []
