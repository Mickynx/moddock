# ModDock

**Upload game mods from your phone or PC over LAN and manage them per game — without ever leaving Gaming Mode.**

ModDock is a [Decky Loader](https://decky.xyz/) plugin for gaming handhelds (Steam Deck, ROG Xbox Ally on Bazzite, and friends). v1 supports Unreal Engine (UE4/UE5) pak-style mods — the format used by Stellar Blade, Lies of P, and most other UE titles.

No Windows tools, no desktop-mode file juggling, no mod-site account required on the device: download a mod on your phone, scan a QR code, upload, toggle it on.

## Features

- **Automatic game detection** — scans all your Steam libraries (internal storage and SD card) and recognizes Unreal Engine games by their on-disk structure, with a UE / UE·IoStore badge. No hardcoded game list.
- **Web upload over LAN** — the plugin serves a mobile-friendly upload page behind a random token URL, shown as a QR code in the Quick Access panel. Off by default; one toggle to start, one to stop. Configurable port.
- **One-step install with validation** — pick the game on the upload page and every file installs the moment it lands: IoStore pak sets (`.pak` + `.utoc` + `.ucas`) must be complete, and a broken or unsupported archive fails right on the page with the reason instead of failing silently.
- **Per-game mod management** — enable, disable, and delete mods per game from the panel. Toggling is instant (a same-filesystem file move) and takes effect on the next game launch.
- **Safe by construction** — enabled state is derived from the filesystem, never cached; imports that would collide with another mod's files (or with a mod you installed by hand) are refused up front, so ModDock can never destroy files it doesn't own.
- **Uninstall-proof** — the store keeps the full copy of every mod, so uninstalling a game simply disables its mods; reinstall and flip them back on.

## How it works

```
phone browser ──upload+game──▶ validate ──▶ mod store ──enable(copy)──▶ <Game>/Content/Paks/~mods
   (QR code, progress bar)                (full copy)  ◀──disable────    (loaded by the engine)
```

1. **Add a game** — tap *Add Game*; ModDock scans your Steam libraries on demand and lists detected UE games.
2. **Start the upload service** — *Upload Settings → toggle on*, then scan the QR code with your phone (same Wi-Fi).
3. **Upload** — pick the target game on the page (remembered for next time), select one or more files, and watch the per-file progress bars. Accepted: a mod archive (`.zip` / `.7z`) or a bare `.pak`; a `.utoc`/`.ucas` cannot stand alone — send those inside an archive together with their matching `.pak`.
4. **Play** — every upload validates, imports and enables in one step. Toggle mods on/off any time; changes apply on next launch.

Mods live in a central store under `~/.local/share/moddock/`, which always keeps the full copy. Enabling copies the files into the game's `Paks/~mods`; disabling deletes the copies. Uninstalling a game therefore just leaves its mods disabled — reinstall and re-enable whenever you like.

## Installation

ModDock is not yet on the Decky store. To sideload:

1. Install [Decky Loader](https://decky.xyz/) on the handheld.
2. Enable SSH on the handheld and, once, fix plugin-directory ownership:
   ```bash
   sudo chown -R $USER: ~/homebrew/plugins
   ```
3. On your development machine (Node 22+, Python 3.10+, pnpm):
   ```bash
   git clone https://github.com/Mickynx/moddock.git
   cd moddock
   pnpm install
   scripts/deploy.sh user@handheld-ip
   ```
   The script builds the frontend, vendors the Python dependency into `py_modules/`, rsyncs the plugin to `~/homebrew/plugins/moddock/`, and restarts Decky.
4. Open the Quick Access menu → plug icon → **ModDock**.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Plugin missing after deploy | Check `~/homebrew/logs/moddock/`; most often `py_modules/` didn't sync — re-run the deploy script. |
| Deploy's restart step fails (sudo password) | Files are already synced; restart manually: `sudo systemctl restart plugin_loader`. |
| Upload page unreachable | Phone must be on the same LAN; the URL/token rotates every time the service is toggled — rescan the QR. |
| "Address already in use" in Upload Settings | Another service holds the port — change it in Upload Settings. |
| Mod enabled but not visible in game | Some mods replace one specific outfit/asset — check the mod's page for which. Also confirm the game was restarted. |
| Upload fails with "incomplete pak set" | The archive is missing one of `.pak`/`.utoc`/`.ucas` — re-download the mod. |

## Development

- **Frontend** (React + TypeScript via `@decky/ui`): `pnpm install && pnpm build`
- **Backend tests** (pure Python, run anywhere):
  ```bash
  python3 -m venv venv
  ./venv/bin/pip install -r requirements-dev.txt
  ./venv/bin/python -m pytest tests -v
  ```
- **Deploy to a handheld**: `scripts/deploy.sh user@handheld`
- **On-device acceptance runbook**: [docs/testing-checklist.md](docs/testing-checklist.md)
- **Design & plan**: [docs/specs/](docs/specs/) and [docs/plans/](docs/plans/)

### Architecture

```
main.py                 Decky entry point — the only file that imports `decky`
moddock/
  steam.py              Steam library discovery (libraryfolders.vdf / appmanifest ACF)
  adapters/unreal.py    UE game detection (engine adapter; more engines can follow)
  importer.py           Archive extraction, scanning, pak-set validation
  store.py              Central mod store, move-based enable/disable state machine
  uploader.py           aiohttp LAN upload service (token, allowlist, size caps, QR)
  settings.py           JSON-backed settings (managed games, upload port)
src/                    Quick Access panel (games / add / detail / settings)
```

Everything under `moddock/` is decky-free and covered by the pytest suite (87 tests).

## Roadmap

- Decky store submission (needs a real `publish.image` and on-SteamOS testing)
- More engine adapters beyond Unreal
- Non-ASCII filename polish and `.rar` messaging

## License

BSD-3-Clause (see [LICENSE](LICENSE)).
