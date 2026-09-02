# ModDock Design Document

Date: 2026-09-01 (revised 2026-09-02)
Status: v1 design approved by the project owner.
Revision 2026-09-02, approved by the project owner: the mod store moved
from move-based to copy-based enable/disable (§4), and the inbox/assign
flow was replaced by direct install at upload time (§5–§7).

## 1. Project Positioning

ModDock is a Decky Loader plugin that lets handheld users import and manage
game mods entirely from Gaming Mode: a phone or PC uploads mod archives to the
handheld through a browser, the plugin validates and stores them, and the user
enables/disables mods per game from the Quick Access panel.

v1 implements only the Unreal Engine (UE4/UE5) pak-style mod adapter, but the
engine-specific logic is encapsulated behind an adapter interface and the
naming/framework stays engine-agnostic, so more engines/games can be added
later.

Primary validation target: installing and managing a head-swap mod
(Seamless EVE Scarlet Head) for Stellar Blade on a ROG Xbox Ally X running
Bazzite.

Non-goals (explicitly out of scope for v1):

- Downloading mods from Nexus or similar sites (no free-tier API; see prior
  research)
- Watch-folder import (removed from the design; web upload is the only
  import entry point)
- `.rar` extraction (rejected with a clear "unsupported" message)
- Load-order editing and mod conflict detection
- Non-Steam games (Epic/GOG/Heroic)

## 2. Overall Architecture

Standard two-layer Decky plugin:

- **Frontend**: React + TypeScript using @decky/ui (Decky Frontend Library),
  running inside the Gaming Mode Quick Access panel.
- **Backend**: Python 3 (a resident process hosted by Decky Loader),
  responsible for all filesystem operations, game detection, and the HTTP
  upload service. Frontend and backend communicate through Decky's
  callable/event mechanism.

Backend module layout:

```
main.py                 # Decky entry point: registers callables, wires modules
moddock/
  steam.py              # Steam library discovery: libraryfolders.vdf / appmanifest_*.acf parsing
  adapters/
    base.py             # Engine adapter interface: detect() / mods_dir() / enable-disable semantics
    unreal.py           # UE adapter (the only v1 implementation)
  store.py              # Central mod repository + manifest JSON + enable/disable state machine
  importer.py           # Extraction, scanning, pak-set validation
  uploader.py           # aiohttp upload service + token + QR code SVG
  settings.py           # Plugin settings persistence (managed game list, port, etc.)
```

Each module has a single responsibility and prefers pure functions. Everything
except `uploader` is network-free, so it can be unit-tested on the development
machine with pytest.

## 3. Steam Library Discovery and UE Game Detection

### 3.1 Steam Library Discovery (steam.py)

1. Probe Steam root directories in order: `~/.local/share/Steam`, then
   `~/.steam/steam` (symlink).
2. Parse `steamapps/libraryfolders.vdf` to obtain all library paths
   (including libraries on SD cards).
3. For each library, parse `steamapps/appmanifest_*.acf` to obtain installed
   games' `appid`, `name`, and `installdir`, then resolve the absolute install
   path.
4. VDF/ACF parsing uses a minimal built-in parser (only key-value extraction
   is needed; no third-party dependency).

### 3.2 UE Detection (adapters/unreal.py)

A candidate game directory is a UE game when both conditions hold:

- An `Engine/` subdirectory exists;
- Exactly one `<ProjectName>/Content/Paks/` structure exists, where
  `<ProjectName>` is a directory sibling to `Engine`.

Auxiliary signals (recorded in the detection result but not hard
requirements): a `*-Win64-Shipping.exe` under `Binaries`; `.utoc/.ucas` files
inside `Paks` (IoStore, flagged as UE4.27+/UE5 — used only as a frontend
badge; handling is identical to classic paks).

The mods directory is by convention `<ProjectName>/Content/Paks/~mods`,
created automatically the first time a mod is enabled.

Detection runs **on demand**: a full library scan happens only when the user
taps "Add Game" (see §7).

## 4. Mod Storage and Enable/Disable Model (store.py)

- The central repository lives at
  `~/.local/share/moddock/mods/<appid>/<mod-name>/` and always holds the
  full copy of every imported mod — it is the single source of truth.
- One manifest per game at `~/.local/share/moddock/manifest/<appid>.json`,
  recording each mod's name, file list, source archive name, and import time.
  Enabled state is NOT stored in the manifest; the filesystem is the source
  of truth (all of a mod's files present in `~mods` = enabled, some =
  partial, none = disabled), which prevents state desync.
- **Enable = COPY all of a mod's files into `~mods`; disable = delete the
  copies from `~mods` (the repository is untouched).** Copying was chosen
  over the earlier move model so that uninstalling a game (Steam removes the
  install dir, `~mods` included) merely leaves every mod disabled: after a
  reinstall the repository is intact and mods can be re-enabled manually.
  Accepted trade-offs: enabled mods occupy disk twice, and toggling a large
  mod is a real copy rather than a rename. Cross-partition relocation is no
  longer needed and was removed.
- Enable verifies the repository copy is complete before copying, and may
  overwrite same-named files in `~mods`: import-time conflict checks (see
  §5) guarantee any such file is ModDock's own, so enable doubles as repair
  and is idempotent.
- Deleting a mod removes its files from the repository and from `~mods`, and
  updates the manifest; the frontend requires a confirmation step. Deletion
  also works while the game is not installed (repository-only cleanup).
- Edge case: toggling while the game is running is allowed; the frontend
  shows a "takes effect on next launch" notice.

## 5. Import Pipeline (importer.py + direct install)

Web upload is the only entry point, and it installs directly — there is no
inbox and no manual assignment step. Flow:

1. The upload page requires picking a target game before uploading (see §6).
2. Each uploaded file is streamed into an internal staging directory
   (`~/.local/share/moddock/staging/`, never user-facing; leftovers from
   interrupted transfers are cleared at plugin start).
3. Supported formats: `.zip` (Python standard library), `.7z` (extracted via
   the system `bsdtar` or `7z` binary when present — Bazzite ships `bsdtar`;
   if neither exists the upload fails with an actionable message), and bare
   `.pak` files. `.rar` and any other format is rejected at upload time.
   (`py7zr` was rejected: it pulls in C-extension dependencies that cannot
   be vendored portably.)
4. Archives are extracted to a temporary directory and scanned recursively
   for `.pak/.utoc/.ucas` files.
5. **Pak-set validation**: when a `.utoc` exists, a same-stem `.pak` and
   `.ucas` must both exist; any missing member fails validation with a
   message naming the missing file. A standalone `.pak` (no same-stem
   `.utoc`) is valid; a bare `.utoc`/`.ucas` upload is not.
6. On success the mod is imported into the repository under a name derived
   from the archive name (extension stripped, sanitized) and enabled
   immediately; the upload page reports the mod name.
7. On any failure (validation, name/file conflict, filesystem error) the
   upload page reports the reason verbatim and the staged file is discarded
   — nothing is left behind.

## 6. Web Upload Service (uploader.py)

- An aiohttp HTTP server bound to `0.0.0.0`, default port 8765
  (configurable), **off by default**, started/stopped by a panel toggle.
- When enabled, a random token is generated; the upload page URL is
  `http://<handheld-LAN-IP>:8765/u/<token>`. The panel shows the URL and a QR
  code (SVG generated in pure Python on the backend; no third-party frontend
  library).
- The upload page is a single embedded HTML page (mobile-friendly, no
  external resources). It fetches the pickable games from
  `GET /u/<token>/games` (managed AND currently installed games only; same
  token auth), requires a game selection before uploading (the last choice is
  remembered in the browser's localStorage), supports multi-file selection,
  and uploads files sequentially via XMLHttpRequest with a per-file progress
  bar and per-file install/failure result.
- Security measures: token check (wrong token returns 404), per-file size
  limit (default 2 GiB), extension allowlist, filename sanitization
  (basename only plus special-character filtering, preventing path
  traversal), and writes restricted to the staging directory.
- After an upload completes, the backend notifies the frontend through a
  Decky event so an open mod list refreshes.

## 7. Frontend Panel (Quick Access)

Lazy-loading with a user-managed game list:

- **Main view**: lists only the games the user has added (persisted in
  settings), each showing its name and an engine badge (UE / UE5·IoStore).
  An "Add Game" button at the bottom triggers the backend full-library scan
  only when tapped, then shows a picker of detected UE games not yet added;
  selection adds them to the list. Games can be removed from the list
  (installed mod files are untouched).
- **Game detail view**: the game's mod list; each row = mod name + enable
  toggle + delete. Toggling while the game is running shows a "takes effect
  on next launch" notice.
- **Settings view**: upload service toggle, port, URL + QR code display.
- Uninstalled games: shown greyed out in the main view with a
  "not detected as installed" hint.

## 8. Testing Strategy

- Backend pytest (run on the macOS development machine, TDD):
  - VDF/ACF parsing (text fixtures);
  - UE detection (fake directory trees built in `tmp_path`: hit/miss,
    multiple projects, IoStore variants);
  - Pak-set validation (complete set / missing `.ucas` / standalone `.pak` /
    nested directories);
  - Store enable/disable state machine (cross-partition fallback simulated
    with monkeypatch);
  - Uploader via the aiohttp test client: token check, allowlist, path
    traversal protection.
- Frontend: `pnpm build` passing is sufficient; interaction logic is thin,
  no unit tests.
- Integration: SSH deploy to the ROG Xbox Ally (Bazzite, Decky installed),
  end-to-end test with Stellar Blade + Seamless EVE Scarlet Head:
  upload (game picked on the page) → validate → install → verify in game.

## 9. Repository and Toolchain

- Location: `~/bazzite/moddock`, initialized from the official
  decky-plugin-template, keeping the template LICENSE (BSD, required for
  store distribution).
- Development machine: macOS with Node 22 installed; pnpm enabled via
  `corepack enable`; backend tests via `python3 -m venv` + pytest.
- Deployment: the template's SSH deploy flow pushes to
  `~/homebrew/plugins/moddock` on the handheld; restart Decky to load.
- Store-readiness: `package.json`/`plugin.json` follow the
  decky-plugin-database submission rules; the only vendored Python dependency
  is `segno` (pure Python, zero dependencies, BSD-licensed, used for QR SVG
  generation), vendored as source into `py_modules/` — no prebuilt binaries.
- All project documentation, code comments, and the README are written in
  English.
