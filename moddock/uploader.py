"""LAN web-upload service.

Serves a single mobile-friendly upload page behind a random URL token.
The page requires picking a target game up front; each uploaded file is
streamed to a staging directory, handed to the installer callback
(validate → import → enable, provided by main.py), and the staging copy
is discarded — success or failure is reported straight back to the
browser. Off by default; main.py starts/stops it from the panel toggle.
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

ALLOWED_UPLOAD_EXTS = {".zip", ".7z", ".pak", ".utoc", ".ucas"}
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
label{margin:.4rem 0}
select{background:#1d2733;color:#e6ebf0;border:1px solid #33404f;
       border-radius:6px;padding:.5rem;font-size:1rem;max-width:90vw}
input[type=file]{margin:1rem 0;max-width:90vw}
button{background:#3a9bed;border:0;color:#fff;padding:.7rem 2rem;
       border-radius:8px;font-size:1rem}
button:disabled{opacity:.5}
#items{width:min(28rem,90vw);margin-top:1rem}
.item{margin:.6rem 0;font-size:.9rem}
.name{word-break:break-all}
.track{background:#1d2733;height:6px;border-radius:3px;margin:.25rem 0}
.bar{background:#3a9bed;height:100%;width:0;border-radius:3px}
.ok{color:#7bd88f}
.bad{color:#ff6a6a}
#hint{font-size:.9rem;opacity:.8}
</style></head><body>
<h1>ModDock — upload mods</h1>
<label>Install to <select id="g"></select></label>
<p id="hint">Accepted: .zip .7z .pak — mods install and enable immediately.</p>
<input id="f" type="file" multiple>
<button id="btn" onclick="up()">Upload &amp; install</button>
<div id="items"></div>
<script>
const sel=document.getElementById('g');
const btn=document.getElementById('btn');
const items=document.getElementById('items');
const hint=document.getElementById('hint');
async function loadGames(){
  try{
    const r=await fetch(location.pathname+'/games');
    const j=await r.json();
    if(!j.games.length){
      btn.disabled=true;
      hint.textContent='No installed games are managed yet — use Add Game in the ModDock panel first.';
      return;
    }
    for(const g of j.games){
      const o=document.createElement('option');
      o.value=g.appid; o.textContent=g.name;
      sel.appendChild(o);
    }
    const last=localStorage.getItem('moddock_appid');
    if(last && [...sel.options].some(o=>o.value===last)) sel.value=last;
  }catch(e){
    btn.disabled=true;
    hint.textContent='Could not load the game list — reopen this page from the panel QR code.';
  }
}
function row(name){
  const box=document.createElement('div'); box.className='item';
  const label=document.createElement('div'); label.className='name'; label.textContent=name;
  const track=document.createElement('div'); track.className='track';
  const bar=document.createElement('div'); bar.className='bar';
  const status=document.createElement('div'); status.textContent='waiting…';
  track.appendChild(bar); box.append(label,track,status); items.appendChild(box);
  return {
    progress(frac){ bar.style.width=(frac*100).toFixed(1)+'%'; status.textContent='uploading '+(frac*100).toFixed(0)+'%'; },
    done(msg,ok){ bar.style.width='100%'; bar.style.background=ok?'#7bd88f':'#ff6a6a';
                  status.textContent=msg; status.className=ok?'ok':'bad'; },
  };
}
function sendOne(file,appid,ui){
  return new Promise(resolve=>{
    const fd=new FormData();
    fd.append('appid',appid);
    fd.append('file',file,file.name);
    const x=new XMLHttpRequest();
    x.open('POST',location.pathname);
    x.upload.onprogress=e=>{ if(e.lengthComputable) ui.progress(e.loaded/e.total); };
    x.onload=()=>{
      let j=null; try{ j=JSON.parse(x.responseText); }catch(e){}
      if(x.status===200 && j){
        if(j.installed.length) ui.done('installed as "'+j.installed[0].mod+'"',true);
        else ui.done(j.failed.length?j.failed[0].reason:'failed',false);
      }else ui.done('upload failed (HTTP '+x.status+')',false);
      resolve();
    };
    x.onerror=()=>{ ui.done('network error',false); resolve(); };
    x.send(fd);
  });
}
async function up(){
  const files=[...document.getElementById('f').files];
  if(!files.length){ hint.textContent='Pick one or more files first.'; return; }
  const appid=sel.value;
  if(!appid){ hint.textContent='Pick a game first.'; return; }
  localStorage.setItem('moddock_appid',appid);
  btn.disabled=true;
  items.textContent='';
  for(const f of files){ await sendOne(f,appid,row(f.name)); }
  btn.disabled=false;
}
loadGames();
</script></body></html>"""


def sanitize_filename(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    # Refuse anything that tried to walk out of the staging area, even though
    # the basename extraction below would already neutralise it.
    if ".." in PurePosixPath(normalized).parts:
        return None
    base = PureWindowsPath(normalized).name
    base = Path(base).name
    cleaned = _FILENAME_RE.sub("", base).strip()
    if not cleaned:
        return None
    # Split on the last dot of the sanitized name rather than via Path, so a
    # name whose stem sanitized away entirely (".zip") still exposes its
    # extension instead of looking like a dotfile.
    stem, dot, ext = cleaned.rpartition(".")
    suffix = f".{ext}" if dot else ""
    if suffix.lower() not in ALLOWED_UPLOAD_EXTS:
        return None
    stem = stem.strip()
    if stem.startswith("."):
        # A dotfile (or the remains of an escaped traversal such as
        # "..%2F..%2Fpwned.zip") is never a legitimate upload name.
        return None
    if not stem:
        # A fully non-ASCII name (e.g. 中文模组.zip) leaves nothing behind. The
        # file type is fine, so keep the upload under a generic stem rather
        # than rejecting it with a misleading "type not allowed"; _unique_path
        # de-duplicates if several arrive.
        stem = "upload"
    if len(stem) + len(suffix) > MAX_FILENAME_LEN:
        # Clamp rather than reject: the filesystem would raise ENAMETOOLONG.
        stem = stem[: MAX_FILENAME_LEN - len(suffix)].strip()
        if not stem:
            return None
    return stem + suffix


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


# The installer receives (staged file, appid) and returns (ok, detail):
# the imported mod's name on success, a user-facing reason on failure.
Installer = Callable[[Path, str], Awaitable[tuple[bool, str]]]
GamesProvider = Callable[[], Awaitable[list[dict]]]


class UploadServer:
    def __init__(
        self,
        staging: Path,
        port: int,
        installer: Installer | None = None,
        games_provider: GamesProvider | None = None,
        on_upload: Callable[[str], Awaitable[None]] | None = None,
        host: str = "0.0.0.0",
    ):
        self.staging = staging
        self.port = port
        self.installer = installer
        self.games_provider = games_provider
        self.on_upload = on_upload
        self.host = host
        self.token: str | None = None
        self._runner: web.AppRunner | None = None

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_FILE_SIZE + 1024**2)
        app.router.add_get("/u/{token}", self._page)
        app.router.add_get("/u/{token}/games", self._games)
        app.router.add_post("/u/{token}", self._upload)
        return app

    def _check_token(self, request: web.Request) -> None:
        if self.token is None or request.match_info["token"] != self.token:
            raise web.HTTPNotFound()

    async def _page(self, request: web.Request) -> web.Response:
        self._check_token(request)
        return web.Response(text=UPLOAD_PAGE, content_type="text/html")

    async def _games(self, request: web.Request) -> web.Response:
        self._check_token(request)
        games = await self.games_provider() if self.games_provider else []
        return web.json_response({"games": games})

    async def _upload(self, request: web.Request) -> web.Response:
        self._check_token(request)
        installed: list[dict] = []
        failed: list[dict] = []
        appid: str | None = None
        self.staging.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "appid" and not part.filename:
                appid = (await part.text()).strip() or None
                continue
            raw_name = part.filename or ""
            name = sanitize_filename(raw_name)
            if name is None:
                failed.append(
                    {
                        "name": raw_name or "(unnamed)",
                        "reason": "file type not allowed",
                    }
                )
                continue
            if appid is None:
                # The page always sends the appid field before the files;
                # anything else is a malformed client.
                failed.append({"name": name, "reason": "no game selected"})
                continue
            target = _unique_path(self.staging, name)
            # Stream to a staging name and rename on success, so a truncated
            # transfer is never handed to the installer.
            temp = _unique_path(self.staging, f"{target.name}.part")
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
                    failed.append({"name": name, "reason": "exceeds size limit"})
                    continue
                os.replace(temp, target)
            except OSError as exc:
                # One unwritable file must not fail the whole upload.
                _discard(temp)
                failed.append(
                    {
                        "name": name,
                        "reason": f"could not be saved ({exc.strerror or exc})",
                    }
                )
                continue
            except BaseException:
                # Client disconnect or cancellation: leave nothing behind.
                _discard(temp)
                raise
            if self.installer is None:
                _discard(target)
                failed.append({"name": name, "reason": "installer not available"})
                continue
            # The staging copy is consumed either way: import copies the files
            # it needs into the mod store, and a failed install has nothing to
            # keep.
            try:
                ok, detail = await self.installer(target, appid)
            except Exception as exc:  # noqa: BLE001 - report, don't 500
                ok, detail = False, f"install failed: {exc}"
            finally:
                _discard(target)
            if ok:
                installed.append({"name": name, "mod": detail})
                if self.on_upload is not None:
                    try:
                        await self.on_upload(name)
                    except Exception:
                        # The mod is already installed; a failing hook must
                        # not turn a successful upload into a 500. No decky
                        # logger is available in this module.
                        pass
            else:
                failed.append({"name": name, "reason": detail})
        return web.json_response({"installed": installed, "failed": failed})

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
