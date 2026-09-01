from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from moddock.uploader import (
    ALLOWED_UPLOAD_EXTS,
    UploadServer,
    qr_svg,
    sanitize_filename,
)


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
