import socket
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from moddock.uploader import (
    ALLOWED_UPLOAD_EXTS,
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


def test_qr_svg():
    svg = qr_svg("http://192.168.1.2:8765/u/abc")
    assert svg.startswith("<svg") and "</svg>" in svg


@pytest.fixture
async def client_and_server(tmp_path):
    server = UploadServer(inbox=tmp_path / "inbox", port=0)
    app = server.build_app()
    server.token = "testtoken"
    test_server = TestServer(app)
    client = TestClient(test_server)
    await client.start_server()
    yield client, server, tmp_path / "inbox"
    await client.close()


async def test_get_upload_page(client_and_server):
    client, server, _ = client_and_server
    resp = await client.get("/u/testtoken")
    assert resp.status == 200
    assert "ModDock" in await resp.text()


async def test_wrong_token_404(client_and_server):
    client, _, _ = client_and_server
    assert (await client.get("/u/wrong")).status == 404
    assert (await client.post("/u/wrong")).status == 404


async def test_upload_saves_file(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    form = aiohttp.FormData()
    form.add_field("file", b"pakdata", filename="mod.pak")
    resp = await client.post("/u/testtoken", data=form)
    assert resp.status == 200
    body = await resp.json()
    assert body["saved"] == ["mod.pak"]
    assert (inbox / "mod.pak").read_bytes() == b"pakdata"


async def test_upload_rejects_bad_extension(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    form = aiohttp.FormData()
    form.add_field("file", b"MZ", filename="virus.exe")
    resp = await client.post("/u/testtoken", data=form)
    body = await resp.json()
    assert body["saved"] == []
    assert "virus.exe" in body["rejected"][0]
    assert not (inbox / "virus.exe").exists()


async def test_upload_avoids_overwrite(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    for _ in range(2):
        form = aiohttp.FormData()
        form.add_field("file", b"data", filename="mod.zip")
        await client.post("/u/testtoken", data=form)
    assert (inbox / "mod.zip").is_file()
    assert (inbox / "mod (1).zip").is_file()


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


async def test_overlong_filename_is_rejected_not_500(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    form = aiohttp.FormData()
    form.add_field("file", b"data", filename="y" * 400 + ".zip")
    resp = await client.post("/u/testtoken", data=form)
    assert resp.status == 200
    body = await resp.json()
    assert len(body["saved"]) == 1
    saved = inbox / body["saved"][0]
    assert saved.read_bytes() == b"data"
    assert len(saved.name) <= 205  # clamp plus a possible " (n)" collision suffix


async def test_no_partial_files_left_behind(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    form = aiohttp.FormData()
    form.add_field("file", b"data", filename="mod.zip")
    await client.post("/u/testtoken", data=form)
    assert [p.name for p in sorted(inbox.iterdir())] == ["mod.zip"]


async def test_traversal_upload_stays_inside_inbox(client_and_server):
    import aiohttp

    client, _, inbox = client_and_server
    form = aiohttp.FormData()
    form.add_field("file", b"data", filename="../../pwned.zip")
    resp = await client.post("/u/testtoken", data=form)
    body = await resp.json()
    assert body["saved"] == []
    assert "pwned.zip" in body["rejected"][0]
    assert not (inbox.parent / "pwned.zip").exists()
    assert not (inbox.parent.parent / "pwned.zip").exists()
    assert not inbox.exists() or list(inbox.iterdir()) == []


async def test_on_upload_callback_fires(tmp_path):
    import aiohttp

    seen: list[str] = []

    async def on_upload(name: str) -> None:
        seen.append(name)

    server = UploadServer(inbox=tmp_path / "inbox", port=0, on_upload=on_upload)
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        form = aiohttp.FormData()
        form.add_field("file", b"data", filename="mod.zip")
        resp = await client.post("/u/testtoken", data=form)
        assert (await resp.json())["saved"] == ["mod.zip"]
    finally:
        await client.close()
    assert seen == ["mod.zip"]


async def test_failing_on_upload_does_not_fail_the_upload(tmp_path):
    import aiohttp

    async def on_upload(name: str) -> None:
        raise RuntimeError("importer exploded")

    server = UploadServer(inbox=tmp_path / "inbox", port=0, on_upload=on_upload)
    server.token = "testtoken"
    client = TestClient(TestServer(server.build_app()))
    await client.start_server()
    try:
        form = aiohttp.FormData()
        form.add_field("file", b"data", filename="mod.zip")
        resp = await client.post("/u/testtoken", data=form)
        assert resp.status == 200
        assert (await resp.json())["saved"] == ["mod.zip"]
    finally:
        await client.close()
    assert (tmp_path / "inbox" / "mod.zip").is_file()


async def test_start_stop_lifecycle(tmp_path):
    server = UploadServer(
        inbox=tmp_path / "inbox", port=free_port(), host="127.0.0.1"
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
    first = UploadServer(inbox=tmp_path / "a", port=port, host="127.0.0.1")
    await first.start()
    try:
        second = UploadServer(inbox=tmp_path / "b", port=port, host="127.0.0.1")
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
