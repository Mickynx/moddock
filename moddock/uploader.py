"""LAN web-upload service.

Serves a single mobile-friendly upload page behind a random URL token
and streams multipart uploads into the inbox directory. Off by default;
main.py starts/stops it from the panel toggle.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import socket
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Awaitable, Callable

from aiohttp import web

ALLOWED_UPLOAD_EXTS = {".zip", ".7z", ".rar", ".pak", ".utoc", ".ucas"}
MAX_FILE_SIZE = 2 * 1024**3  # 2 GiB per file
# Well under the 255-byte per-component limit of ext4/btrfs, leaving room for
# the " (n)" collision suffix and the ".part" staging suffix.
MAX_FILENAME_LEN = 200

_FILENAME_RE = re.compile(r"[^A-Za-z0-9 ._()\[\]-]+")

UPLOAD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ModDock Upload</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f141b;color:#e6ebf0;
     display:flex;flex-direction:column;align-items:center;padding:2rem 1rem}
h1{font-size:1.3rem}
input[type=file]{margin:1rem 0;max-width:100%}
button{background:#3a9bed;border:0;color:#fff;padding:.7rem 2rem;
       border-radius:8px;font-size:1rem}
#log{margin-top:1rem;font-size:.9rem;white-space:pre-line}
</style></head><body>
<h1>ModDock — upload mods</h1>
<p>Accepted: .zip .7z .pak .utoc .ucas</p>
<input id="f" type="file" multiple>
<button onclick="up()">Upload</button>
<div id="log"></div>
<script>
async function up(){
  const files=document.getElementById('f').files;
  const log=document.getElementById('log');
  if(!files.length){log.textContent='pick a file first';return}
  const fd=new FormData();
  for(const f of files) fd.append('file',f,f.name);
  log.textContent='uploading…';
  const r=await fetch(location.pathname,{method:'POST',body:fd});
  const j=await r.json();
  log.textContent='saved: '+j.saved.join(', ')+
    (j.rejected.length?'\\nrejected: '+j.rejected.join('; '):'');
}
</script></body></html>"""


def sanitize_filename(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    # Refuse anything that tried to walk out of the inbox, even though the
    # basename extraction below would already neutralise it.
    if ".." in PurePosixPath(normalized).parts:
        return None
    base = PureWindowsPath(normalized).name
    base = Path(base).name
    base = _FILENAME_RE.sub("", base).strip()
    if not base or base.startswith("."):
        return None
    suffix = Path(base).suffix
    if suffix.lower() not in ALLOWED_UPLOAD_EXTS:
        return None
    if len(base) > MAX_FILENAME_LEN:
        # Clamp rather than reject: the filesystem would raise ENAMETOOLONG.
        stem = Path(base).stem[: MAX_FILENAME_LEN - len(suffix)].strip()
        if not stem or stem.startswith("."):
            return None
        base = stem + suffix
    return base


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # no packets are actually sent (UDP)
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def qr_svg(text: str) -> str:
    import io

    import segno

    buffer = io.BytesIO()
    segno.make(text, error="m").save(
        buffer, kind="svg", xmldecl=False, scale=4, dark="#e6ebf0", light=None
    )
    return buffer.getvalue().decode("utf-8")


def _discard(path: Path) -> None:
    """Remove a staging file, ignoring anything the filesystem complains about."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _unique_path(directory: Path, name: str) -> Path:
    candidate = directory / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


class UploadServer:
    def __init__(
        self,
        inbox: Path,
        port: int,
        on_upload: Callable[[str], Awaitable[None]] | None = None,
        host: str = "0.0.0.0",
    ):
        self.inbox = inbox
        self.port = port
        self.on_upload = on_upload
        self.host = host
        self.token: str | None = None
        self._runner: web.AppRunner | None = None

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_FILE_SIZE + 1024**2)
        app.router.add_get("/u/{token}", self._page)
        app.router.add_post("/u/{token}", self._upload)
        return app

    def _check_token(self, request: web.Request) -> None:
        if self.token is None or request.match_info["token"] != self.token:
            raise web.HTTPNotFound()

    async def _page(self, request: web.Request) -> web.Response:
        self._check_token(request)
        return web.Response(text=UPLOAD_PAGE, content_type="text/html")

    async def _upload(self, request: web.Request) -> web.Response:
        self._check_token(request)
        saved: list[str] = []
        rejected: list[str] = []
        self.inbox.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            raw_name = part.filename or ""
            name = sanitize_filename(raw_name)
            if name is None:
                rejected.append(f"{raw_name or '(unnamed)'}: file type not allowed")
                continue
            target = _unique_path(self.inbox, name)
            # Stream to a staging name and rename on success, so a truncated
            # transfer never shows up in the inbox under its final name.
            temp = _unique_path(self.inbox, f"{target.name}.part")
            try:
                size = 0
                too_large = False
                with temp.open("wb") as fh:
                    while chunk := await part.read_chunk(1024 * 256):
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            too_large = True
                            break
                        fh.write(chunk)
                if too_large:
                    _discard(temp)
                    rejected.append(f"{name}: exceeds size limit")
                    continue
                os.replace(temp, target)
            except OSError as exc:
                # One unwritable file must not fail the whole upload.
                _discard(temp)
                rejected.append(f"{name}: could not be saved ({exc.strerror or exc})")
                continue
            except BaseException:
                # Client disconnect or cancellation: leave nothing behind.
                _discard(temp)
                raise
            saved.append(target.name)
            if self.on_upload is not None:
                try:
                    await self.on_upload(target.name)
                except Exception:
                    # The file is already safely on disk; a failing hook must
                    # not turn a successful upload into a 500. No decky logger
                    # is available in this module, so the error is swallowed.
                    pass
        return web.json_response({"saved": saved, "rejected": rejected})

    async def start(self) -> None:
        if self._runner is not None:
            return
        token = secrets.token_urlsafe(8)
        runner = web.AppRunner(self.build_app())
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
        except BaseException:
            # A failed bind (port in use) must not leave a half-started
            # server: status() would advertise a URL for a dead socket and
            # every later start() would early-return on a stale runner.
            with contextlib.suppress(Exception):
                await runner.cleanup()
            raise
        # Only publish state once the socket is really listening.
        self._runner = runner
        self.token = token

    async def stop(self) -> None:
        runner, self._runner = self._runner, None
        self.token = None
        if runner is not None:
            # State is cleared first, so a failing cleanup still leaves the
            # server restartable rather than wedged as "running".
            await runner.cleanup()

    def status(self) -> dict:
        running = self._runner is not None
        url = (
            f"http://{get_lan_ip()}:{self.port}/u/{self.token}" if running else None
        )
        return {"running": running, "url": url}
