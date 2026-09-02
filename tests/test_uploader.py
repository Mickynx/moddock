import socket
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from moddock.uploader import (
    UploadServer,
    qr_svg,
    sanitize_filename,
)


def free_port() -> int:
    """Reserve-then-release an ephemeral port for a real bind test."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sanitize_filename():
    assert sanitize_filename("Cool Mod v2.zip") == "Cool Mod v2.zip"
    assert sanitize_filename("../../etc/passwd") is None
    assert sanitize_filename("C:\\evil\\mod.zip") == "mod.zip"
    assert sanitize_filename(".hidden") is None
    assert sanitize_filename("mod.exe") is None  # extension not allowed
    assert sanitize_filename("mod.rar") is None  # rar cannot be extracted


def test_qr_svg():
    svg = qr_svg("http://192.168.1.2:8765/u/abc")
    assert svg.startswith("<svg") and "</svg>" in svg


class InstallerSpy:
    """Records installer calls; per-name results can be preloaded."""

    def __init__(self):
        self.calls: list[tuple[Path, str, bytes]] = []
        self.result: tuple[bool, str] = (True, "Some Mod")

    async def __call__(self, path: Path, appid: str) -> tuple[bool, str]:
        # Content is captured because the staging file is discarded afterwards.
        self.calls.append((path, appid, path.read_bytes()))
        return self.result


@pytest.fixture
async def client_and_server(tmp_path):
    installer = InstallerSpy()

    async def games():
        return [{"appid": "42", "name": "Stellar Blade"}]

    server = UploadServer(
        staging=tmp_path / "staging",
        port=0,
        installer=installer,
        games_provider=games,
    )
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    yield client, installer, tmp_path / "staging"
    await client.close()


def _form(filename: str, content: bytes = b"data", appid: str | None = "42"):
    form = aiohttp.FormData()
    if appid is not None:
        form.add_field("appid", appid)
    form.add_field("file", content, filename=filename)
    return form


async def test_get_upload_page(client_and_server):
    client, _, _ = client_and_server
    resp = await client.get("/u/testtoken")
    assert resp.status == 200
    text = await resp.text()
    assert "ModDock" in text
    assert "multiple" in text  # multi-file selection stays wired


async def test_games_endpoint(client_and_server):
    client, _, _ = client_and_server
    resp = await client.get("/u/testtoken/games")
    assert resp.status == 200
    assert (await resp.json())["games"] == [
        {"appid": "42", "name": "Stellar Blade"}
    ]


async def test_wrong_token_404(client_and_server):
    client, _, _ = client_and_server
    assert (await client.get("/u/wrong")).status == 404
    assert (await client.get("/u/wrong/games")).status == 404
    assert (await client.post("/u/wrong")).status == 404


async def test_upload_installs_file(client_and_server):
    client, installer, staging = client_and_server
    resp = await client.post("/u/testtoken", data=_form("mod.pak", b"pakdata"))
    assert resp.status == 200
    body = await resp.json()
    assert body["installed"] == [{"name": "mod.pak", "mod": "Some Mod"}]
    assert body["failed"] == []
    [(path, appid, content)] = installer.calls
    assert appid == "42"
    assert content == b"pakdata"
    # The staging copy is consumed: nothing is left behind.
    assert list(staging.iterdir()) == []


async def test_upload_without_game_fails(client_and_server):
    client, installer, _ = client_and_server
    resp = await client.post("/u/testtoken", data=_form("mod.pak", appid=None))
    body = await resp.json()
    assert body["installed"] == []
    assert body["failed"] == [{"name": "mod.pak", "reason": "no game selected"}]
    assert installer.calls == []


async def test_failed_install_reports_reason(client_and_server):
    client, installer, staging = client_and_server
    installer.result = (False, 'a mod named "Some Mod" already exists')
    resp = await client.post("/u/testtoken", data=_form("mod.zip"))
    body = await resp.json()
    assert body["installed"] == []
    assert body["failed"] == [
        {"name": "mod.zip", "reason": 'a mod named "Some Mod" already exists'}
    ]
    assert list(staging.iterdir()) == []


async def test_crashing_installer_reports_not_500(tmp_path):
    async def boom(path: Path, appid: str) -> tuple[bool, str]:
        raise RuntimeError("importer exploded")

    staging = tmp_path / "staging"
    server = UploadServer(staging=staging, port=0, installer=boom)
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        resp = await client.post("/u/testtoken", data=_form("mod.zip"))
        assert resp.status == 200
        body = await resp.json()
        assert "importer exploded" in body["failed"][0]["reason"]
        assert list(staging.iterdir()) == []
    finally:
        await client.close()


async def test_upload_rejects_bad_extension(client_and_server):
    client, installer, staging = client_and_server
    resp = await client.post(
        "/u/testtoken", data=_form("virus.exe", b"MZ")
    )
    body = await resp.json()
    assert body["installed"] == []
    assert body["failed"][0]["name"] == "virus.exe"
    assert installer.calls == []
    assert not staging.exists() or list(staging.iterdir()) == []


async def test_same_name_uploaded_twice_processes_both(client_and_server):
    client, installer, _ = client_and_server
    for _ in range(2):
        resp = await client.post("/u/testtoken", data=_form("mod.zip"))
        assert (await resp.json())["installed"]
    assert len(installer.calls) == 2


def test_sanitize_filename_clamps_long_names():
    clamped = sanitize_filename("x" * 400 + ".zip")
    assert clamped is not None
    assert len(clamped) <= 200
    assert clamped.endswith(".zip")


def test_sanitize_filename_rejects_traversal_segments():
    assert sanitize_filename("../../pwned.zip") is None
    assert sanitize_filename("..\\..\\pwned.zip") is None
    # A plain Windows path is still reduced to its basename.
    assert sanitize_filename("C:\\games\\mod.zip") == "mod.zip"


async def test_overlong_filename_is_clamped_not_500(client_and_server):
    client, installer, _ = client_and_server
    resp = await client.post(
        "/u/testtoken", data=_form("y" * 400 + ".zip")
    )
    assert resp.status == 200
    body = await resp.json()
    assert len(body["installed"]) == 1
    assert len(body["installed"][0]["name"]) <= 200


async def test_traversal_upload_stays_inside_staging(client_and_server):
    client, installer, staging = client_and_server
    resp = await client.post(
        "/u/testtoken", data=_form("../../pwned.zip")
    )
    body = await resp.json()
    assert body["installed"] == []
    assert "pwned.zip" in body["failed"][0]["name"]
    assert installer.calls == []
    assert not (staging.parent / "pwned.zip").exists()
    assert not (staging.parent.parent / "pwned.zip").exists()
    assert not staging.exists() or list(staging.iterdir()) == []


async def test_on_upload_fires_only_on_success(tmp_path):
    seen: list[str] = []

    async def on_upload(name: str) -> None:
        seen.append(name)

    installer = InstallerSpy()
    server = UploadServer(
        staging=tmp_path / "staging",
        port=0,
        installer=installer,
        on_upload=on_upload,
    )
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        resp = await client.post("/u/testtoken", data=_form("mod.zip"))
        assert (await resp.json())["installed"]
        installer.result = (False, "nope")
        resp = await client.post("/u/testtoken", data=_form("bad.zip"))
        assert (await resp.json())["failed"]
    finally:
        await client.close()
    assert seen == ["mod.zip"]


async def test_failing_on_upload_does_not_fail_the_upload(tmp_path):
    async def on_upload(name: str) -> None:
        raise RuntimeError("event hook exploded")

    server = UploadServer(
        staging=tmp_path / "staging",
        port=0,
        installer=InstallerSpy(),
        on_upload=on_upload,
    )
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        resp = await client.post("/u/testtoken", data=_form("mod.zip"))
        assert resp.status == 200
        assert (await resp.json())["installed"]
    finally:
        await client.close()


async def test_start_stop_lifecycle(tmp_path):
    server = UploadServer(
        staging=tmp_path / "staging", port=free_port(), host="127.0.0.1"
    )
    assert server.status() == {"running": False, "url": None}
    await server.start()
    try:
        status = server.status()
        assert status["running"] is True
        assert server.token is not None
        assert status["url"].endswith(f":{server.port}/u/{server.token}")
    finally:
        await server.stop()
    assert server.status() == {"running": False, "url": None}
    assert server.token is None


async def test_failed_bind_leaves_server_stoppable(tmp_path):
    port = free_port()
    first = UploadServer(staging=tmp_path / "a", port=port, host="127.0.0.1")
    await first.start()
    try:
        second = UploadServer(staging=tmp_path / "b", port=port, host="127.0.0.1")
        with pytest.raises(OSError):
            await second.start()
        # A failed bind must not look like a running server.
        assert second.status() == {"running": False, "url": None}
        assert second.token is None
        # ...and a later start on a free port must still work.
        second.port = free_port()
        await second.start()
        assert second.status()["running"] is True
        await second.stop()
    finally:
        await first.stop()


def test_sanitize_filename_keeps_non_ascii_uploads():
    """A fully non-ASCII stem must not be reported as a bad file type."""
    assert sanitize_filename("中文模组.zip") == "upload.zip"
    assert sanitize_filename("中文模组.pak") == "upload.pak"
    # A disallowed suffix is still refused, non-ASCII stem or not.
    assert sanitize_filename("中文模组.exe") is None
