"""LAN web-upload service.

Serves a single mobile-friendly upload page behind a random URL token.
The page requires picking a target game *and* an install method (recipe)
up front; each uploaded file is streamed to a staging directory, handed
to the installer callback (validate → import → enable, provided by
main.py), and the staging copy is discarded — success or failure is
reported straight back to the browser. The page can also create a custom
install method inline, which it POSTs to the recipes endpoint. Off by
default; main.py starts/stops it from the panel toggle.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import socket
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Awaitable, Callable

from aiohttp import web

ALLOWED_UPLOAD_EXTS = {".zip", ".7z", ".pak", ".utoc", ".ucas"}
MAX_FILE_SIZE = 2 * 1024**3  # 2 GiB per file
# A recipe body is buffered in memory, so it gets its own (much smaller) cap
# than the streamed-to-disk uploads the app-wide client_max_size is sized for.
RECIPE_BODY_LIMIT = 256 * 1024
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
select,input[type=text]{background:#1d2733;color:#e6ebf0;border:1px solid #33404f;
       border-radius:6px;padding:.5rem;font-size:1rem;max-width:90vw}
input[type=file]{margin:1rem 0;max-width:90vw}
button{background:#3a9bed;border:0;color:#fff;padding:.7rem 2rem;
       border-radius:8px;font-size:1rem}
button:disabled{opacity:.5}
button.small{background:#2b3947;padding:.4rem 1rem;font-size:.85rem}
#newform{display:none;width:min(28rem,90vw);background:#151d27;
         border:1px solid #33404f;border-radius:8px;padding:.8rem;margin:.6rem 0}
#newform .title{font-weight:600;margin-bottom:.4rem}
.field{display:flex;flex-direction:column;gap:.2rem;margin:.4rem 0;font-size:.85rem}
.field span{opacity:.75}
.rule{border:1px solid #2b3947;border-radius:6px;padding:.5rem;margin:.5rem 0}
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
<label>Install method <select id="r"></select></label>
<div id="newform"></div>
<p id="hint">Accepted: .zip .7z .pak — mods install and enable immediately.</p>
<input id="f" type="file" multiple>
<button id="btn" onclick="up()">Upload &amp; install</button>
<div id="items"></div>
<script>
const sel=document.getElementById('g');
const rsel=document.getElementById('r');
const form=document.getElementById('newform');
const btn=document.getElementById('btn');
const items=document.getElementById('items');
const hint=document.getElementById('hint');
// Sentinel option value: picking it opens the inline creation form instead
// of naming a recipe, so it is never sent with an upload.
const NEW='__new';
const DEFAULT_RECIPE='ue-paks-mods';
const ANCHORS=['game_root','paks_dir','win64_dir'];
const MAPPINGS=['flatten','preserve_tree'];
const OVERWRITES=['refuse','backup'];
const LEFTOVERS=['ignore','fail'];
// The games payload, kept so a rule row can grey out anchors this game lacks.
let games=[];
function option(value,text){
  const o=document.createElement('option');
  o.value=value; o.textContent=text;
  return o;
}
function picker(values,initial){
  const s=document.createElement('select');
  for(const v of values) s.appendChild(option(v,v));
  s.value=initial;
  return s;
}
function textInput(placeholder){
  const i=document.createElement('input');
  i.type='text'; i.placeholder=placeholder;
  return i;
}
function field(text,control){
  const l=document.createElement('label'); l.className='field';
  const s=document.createElement('span'); s.textContent=text;
  l.append(s,control);
  return l;
}
async function loadGames(){
  try{
    const r=await fetch(location.pathname+'/games');
    const j=await r.json();
    sel.textContent=''; rsel.textContent='';
    if(!j.games.length){
      btn.disabled=true;
      hint.textContent='No installed games are managed yet — use Add Game in the ModDock panel first.';
      return;
    }
    games=j.games;
    for(const g of j.games) sel.appendChild(option(g.appid,g.name));
    const last=localStorage.getItem('moddock_appid');
    if(last && [...sel.options].some(o=>o.value===last)) sel.value=last;
    for(const rc of (j.recipes||[])) rsel.appendChild(option(rc.id,rc.name));
    rsel.appendChild(option(NEW,'+ New install method…'));
    onGameChange();
    btn.disabled=false;
  }catch(e){
    btn.disabled=true;
    hint.textContent='Could not load the game list — reopen this page from the panel QR code.';
  }
}
function restoreRecipe(){
  // Each game remembers its own install method; an id that no longer exists
  // (recipe deleted since) falls back to the built-in default.
  const want=localStorage.getItem('moddock_recipe_'+sel.value)||DEFAULT_RECIPE;
  const usable=[...rsel.options].filter(o=>o.value!==NEW);
  if(usable.some(o=>o.value===want)) rsel.value=want;
  else if(usable.length) rsel.value=usable[0].value;
  toggleForm();
}
function toggleForm(){
  form.style.display=(rsel.value===NEW)?'block':'none';
}
function gameAnchors(){
  // null means "unknown" — an older server that sends no anchors must not grey
  // out every choice.
  const g=games.find(x=>String(x.appid)===String(sel.value));
  return (g && Array.isArray(g.anchors) && g.anchors.length) ? g.anchors : null;
}
function syncAnchor(anchorSel){
  const allowed=gameAnchors();
  for(const o of anchorSel.options) o.disabled=allowed?!allowed.includes(o.value):false;
  // A pick this game cannot honour would only fail at upload time, so move it
  // to the first location the game does have.
  const current=[...anchorSel.options].find(o=>o.value===anchorSel.value);
  if(current && current.disabled){
    const usable=[...anchorSel.options].find(o=>!o.disabled);
    if(usable) anchorSel.value=usable.value;
  }
}
function syncAnchors(){
  for(const ruleRow of rulesBox.children){
    const f=ruleFields.get(ruleRow);
    if(f) syncAnchor(f.anchor);
  }
}
function onGameChange(){
  restoreRecipe();
  syncAnchors();
}
sel.onchange=onGameChange;
rsel.onchange=toggleForm;
const nameInput=textInput('Name (e.g. Movies folder)');
const rulesBox=document.createElement('div');
const leftoverSel=picker(LEFTOVERS,'ignore');
const saveBtn=document.createElement('button');
const ruleFields=new WeakMap();
function addRule(){
  const ruleRow=document.createElement('div'); ruleRow.className='rule';
  const match=textInput('*.pak, *.utoc, *.ucas');
  const anchor=picker(ANCHORS,'paks_dir');
  syncAnchor(anchor);
  const subpath=textInput('subfolder (optional)');
  const mapping=picker(MAPPINGS,'flatten');
  const overwrite=picker(OVERWRITES,'refuse');
  const del=document.createElement('button');
  del.type='button'; del.className='small'; del.textContent='Remove rule';
  del.onclick=()=>ruleRow.remove();
  ruleRow.append(
    field('Files matching (comma separated)',match),
    field('Install under',anchor),
    field('Subfolder',subpath),
    field('Layout',mapping),
    field('If the file exists',overwrite),
    del);
  ruleFields.set(ruleRow,{match,anchor,subpath,mapping,overwrite});
  rulesBox.appendChild(ruleRow);
}
function buildForm(){
  const title=document.createElement('div');
  title.className='title'; title.textContent='New install method';
  const addBtn=document.createElement('button');
  addBtn.type='button'; addBtn.className='small'; addBtn.textContent='+ Add rule';
  addBtn.onclick=addRule;
  saveBtn.type='button'; saveBtn.textContent='Save install method';
  saveBtn.onclick=saveRecipe;
  form.append(title,field('Name',nameInput),rulesBox,addBtn,
              field('Files no rule matches',leftoverSel),saveBtn);
  addRule();
}
async function saveRecipe(){
  const rules=[];
  for(const ruleRow of rulesBox.children){
    const f=ruleFields.get(ruleRow);
    if(!f) continue;
    rules.push({
      match:f.match.value.split(',').map(s=>s.trim()).filter(Boolean),
      anchor:f.anchor.value,
      subpath:f.subpath.value.trim(),
      mapping:f.mapping.value,
      overwrite:f.overwrite.value,
    });
  }
  const payload={name:nameInput.value.trim(),rules:rules,leftover:leftoverSel.value};
  saveBtn.disabled=true;
  try{
    const r=await fetch(location.pathname+'/recipes',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload),
    });
    let j=null; try{ j=await r.json(); }catch(e){}
    if(!r.ok || !j || !j.id){
      hint.textContent=(j&&j.error)||'Could not save this install method.';
      return;
    }
    // Remember both before reloading, so the refreshed lists come back on the
    // same game with the freshly created method selected.
    localStorage.setItem('moddock_appid',sel.value);
    localStorage.setItem('moddock_recipe_'+sel.value,j.id);
    await loadGames();
    if([...rsel.options].some(o=>o.value===j.id)) rsel.value=j.id;
    toggleForm();
    hint.textContent='Saved install method "'+j.name+'".';
  }catch(e){
    hint.textContent='Could not save this install method.';
  }finally{
    saveBtn.disabled=false;
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
function sendOne(file,appid,recipe,ui){
  return new Promise(resolve=>{
    const fd=new FormData();
    // Both selections must precede the file part: the server streams parts
    // in order and refuses a file it cannot route yet.
    fd.append('appid',appid);
    fd.append('recipe',recipe);
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
  const recipe=rsel.value;
  if(!recipe || recipe===NEW){
    hint.textContent='Pick an install method first — or save the new one you are creating.';
    return;
  }
  localStorage.setItem('moddock_appid',appid);
  localStorage.setItem('moddock_recipe_'+appid,recipe);
  btn.disabled=true;
  items.textContent='';
  for(const f of files){ await sendOne(f,appid,recipe,row(f.name)); }
  btn.disabled=false;
}
buildForm();
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


# The installer receives (staged file, appid, recipe id) and returns
# (ok, detail): the imported mod's name on success, a user-facing reason on
# failure.
Installer = Callable[[Path, str, str], Awaitable[tuple[bool, str]]]
GamesProvider = Callable[[], Awaitable[list[dict]]]
RecipesProvider = Callable[[], Awaitable[list[dict]]]
# Receives the page's raw JSON body and returns the created recipe as a dict;
# a ValueError carries a user-facing rejection reason.
RecipeCreator = Callable[[dict], Awaitable[dict]]


class UploadServer:
    def __init__(
        self,
        staging: Path,
        port: int,
        installer: Installer | None = None,
        games_provider: GamesProvider | None = None,
        on_upload: Callable[[str], Awaitable[None]] | None = None,
        recipes_provider: RecipesProvider | None = None,
        recipe_creator: RecipeCreator | None = None,
        host: str = "0.0.0.0",
    ):
        self.staging = staging
        self.port = port
        self.installer = installer
        self.games_provider = games_provider
        self.on_upload = on_upload
        self.recipes_provider = recipes_provider
        self.recipe_creator = recipe_creator
        self.host = host
        self.token: str | None = None
        self._runner: web.AppRunner | None = None

    def build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_FILE_SIZE + 1024**2)
        app.router.add_get("/u/{token}", self._page)
        app.router.add_get("/u/{token}/games", self._games)
        app.router.add_post("/u/{token}", self._upload)
        app.router.add_post("/u/{token}/recipes", self._create_recipe)
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
        recipes = await self.recipes_provider() if self.recipes_provider else []
        return web.json_response({"games": games, "recipes": recipes})

    @staticmethod
    async def _read_capped(request: web.Request, limit: int) -> bytes | None:
        """Body bytes, or None when the client sent more than `limit`.

        Content-Length is not consulted: a chunked request declares none. A
        single StreamReader.read() can also come back short, so keep reading
        until the cap is passed or the body ends.
        """
        raw = b""
        while len(raw) <= limit:
            chunk = await request.content.read(limit + 1 - len(raw))
            if not chunk:
                return raw
            raw += chunk
        return None

    async def _create_recipe(self, request: web.Request) -> web.Response:
        self._check_token(request)
        raw = await self._read_capped(request, RECIPE_BODY_LIMIT)
        if raw is None:
            return web.json_response({"error": "recipe too large"}, status=400)
        try:
            body = json.loads(raw)
        except ValueError:
            # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors.
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid JSON"}, status=400)
        if self.recipe_creator is None:
            return web.json_response(
                {"error": "install methods cannot be created right now"},
                status=400,
            )
        try:
            result = await self.recipe_creator(body)
        except ValueError as exc:
            # A rejected recipe is user error, not a server fault: the page
            # shows str(exc) verbatim.
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def _upload(self, request: web.Request) -> web.Response:
        self._check_token(request)
        installed: list[dict] = []
        failed: list[dict] = []
        appid: str | None = None
        recipe_id: str | None = None
        self.staging.mkdir(parents=True, exist_ok=True)
        reader = await request.multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "appid" and not part.filename:
                appid = (await part.text()).strip() or None
                continue
            if part.name == "recipe" and not part.filename:
                recipe_id = (await part.text()).strip() or None
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
            if recipe_id is None:
                # Likewise for the install method: without one there is no way
                # to know where the file belongs.
                failed.append(
                    {"name": name, "reason": "no install method selected"}
                )
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
                ok, detail = await self.installer(target, appid, recipe_id)
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
