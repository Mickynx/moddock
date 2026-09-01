from moddock.settings import Settings


def test_defaults(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert s.managed_games == []
    assert s.upload_port == 8765


def test_add_remove_game_persists(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.add_game("1", "Stellar Blade", "/games/sb")
    s.add_game("1", "Stellar Blade", "/games/sb")  # idempotent
    s.add_game("2", "Lies of P", "/games/lop")

    reloaded = Settings(path)
    assert [g["appid"] for g in reloaded.managed_games] == ["1", "2"]

    reloaded.remove_game("1")
    assert [g["appid"] for g in Settings(path).managed_games] == ["2"]


def test_upload_port_persists(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.set_upload_port(9000)
    assert Settings(path).upload_port == 9000


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    s = Settings(path)
    assert s.managed_games == []


def test_non_utf8_file_falls_back_to_defaults(tmp_path):
    # A crash mid-write can split a multi-byte sequence, leaving undecodable bytes.
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00garbage")
    s = Settings(path)
    assert s.managed_games == []
    assert s.upload_port == 8765
