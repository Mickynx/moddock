# ModDock

**Upload game mods from your phone or PC over LAN and manage them per game — without ever leaving Gaming Mode.**

ModDock is a [Decky Loader](https://decky.xyz/) plugin for gaming handhelds (Steam Deck, ROG Xbox Ally on Bazzite, and friends). It detects Unreal Engine (UE4/UE5) games — Stellar Blade, Lies of P, and most other UE titles — and installs mods the way *you* say they install: pick one of the built-in install methods, or describe your own on the upload page.

No Windows tools, no desktop-mode file juggling, no mod-site account required on the device: download a mod on your phone, scan a QR code, upload, toggle it on.

## Features

- **Automatic game detection** — scans all your Steam libraries (internal storage and SD card) and recognizes Unreal Engine games by their on-disk structure, with a UE / UE·IoStore badge. No hardcoded game list.
- **Web upload over LAN** — the plugin serves a mobile-friendly upload page behind a random token URL, shown as a QR code in the Quick Access panel. Off by default; one toggle to start, one to stop. Configurable port.
- **Install methods you control** — pick how a mod installs: built-in methods for UE paks, LogicMods, root merges and EXE-dir drops, or define your own multi-rule method right on the upload page (match patterns → anchor + subpath, flatten or keep the tree). Refuse-by-default overwrite protection, with opt-in backup/restore of replaced game files.
- **One-step install with validation** — pick the game and the install method on the upload page and every file installs the moment it lands: the built-in pak methods require complete IoStore sets (`.pak` + `.utoc` + `.ucas`), and a broken archive, a destination the game does not have, or a collision with another mod fails right on the page with the reason instead of failing silently.
- **Per-game mod management** — enable, disable, and delete mods per game from the panel, each mod showing the method that placed it. Changes take effect on the next game launch.
- **Safe by construction** — enabled state is derived from the filesystem, never cached; imports that would collide with another mod's files (or with a mod you installed by hand) are refused up front, and disable/delete unlink exactly the recorded destinations, so ModDock can never destroy files it doesn't own.
- **Uninstall-proof** — the store keeps the full copy of every mod, so uninstalling a game simply disables its mods; reinstall and flip them back on.

## How it works

```
phone browser ──upload + game + method──▶ apply method ──▶ mod store ──enable(copy)──▶ Paks/~mods
   (QR code, progress bars)              (rules → file list)  (full copy) ◀──disable──  Binaries/Win64
                                                                                        game root, …
```

1. **Add a game** — tap *Add Game*; ModDock scans your Steam libraries on demand and lists detected UE games.
2. **Start the upload service** — *Upload Settings → toggle on*, then scan the QR code with your phone (same Wi-Fi).
3. **Upload** — pick the target game *and* the install method on the page (both required; the last game is remembered, and each game remembers its own method), select one or more files, and watch the per-file progress bars. If no method fits, tap *+ New install method…* and describe one: a name plus one or more rules, each matching file patterns and pointing them at a location in the game (`game_root`, `paks_dir`, `win64_dir`) with an optional subpath. Accepted uploads: a mod archive (`.zip` / `.7z`) or a bare `.pak`; a `.utoc`/`.ucas` cannot stand alone — send those inside an archive together with their matching `.pak`.
4. **Play** — every upload validates, imports and enables in one step. Toggle mods on/off any time; changes apply on next launch.

An install method turns an archive into a list of destinations: for each file, the first rule whose patterns match decides where it lands — under one of the game's anchors, flattened to its basename or keeping its directory tree (a single wrapping top-level folder is stripped first, so `MyMod-1.0/BepInEx/…` installs as `BepInEx/…`). Files no rule matched are ignored or fail the upload, as the method says. Saved methods are reusable for any game and are listed in the panel's *Install methods* section, where custom ones can be deleted; mods already installed with a method keep their recorded destinations, so deleting it does not disturb them.

Mods live in a central store under `~/.local/share/moddock/`, which always keeps the full copy. Enabling copies the files to their recorded destinations; disabling deletes exactly those copies. Destinations are stored relative to the game root, so they survive reinstalls and library moves — and uninstalling a game just leaves its mods disabled; reinstall and re-enable whenever you like.

Two mods can never claim the same destination, and a file ModDock does not manage is never overwritten: a `refuse` rule fails the import and names the file, while a `backup` rule parks the original under `~/.local/share/moddock/backup/` and puts it back when you disable or delete the mod. (If the game updated that file in the meantime, the restored original is stale — verify the game files in Steam.)

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
| "already exists and is not managed by ModDock" | Something else (a hand-installed mod, the game itself) owns that path. Remove it by hand, or use an install method whose rule has backup enabled. |
| An anchor is greyed out in the new-method form | The game has no such directory — e.g. `win64_dir` only appears once `<Project>/Binaries/Win64` exists. Pick another anchor, or install the framework that creates it first. |
| Upload fails with "no files in the upload match this install method" | The method's rules match nothing in that archive — check the patterns (e.g. `*.lua` vs `*.LUA` is fine, but `lua` alone is not) or pick another method. |
| A mod installed, but some archive files went nowhere | The method ignored the files no rule matched. The game view shows which method placed each mod; create a variant with a rule for the missing files, or set the new method's leftover policy to "fail" so unmatched files are named instead of skipped. |
| A mod shows "partial — some files missing" | Some of its recorded destinations are gone (an enable interrupted mid-copy, files deleted by hand, a game verify). Toggle the mod off and on: enable re-copies every file from the store's full copy and is safe to repeat. |

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
  adapters/unreal.py    UE game detection + anchor map (engine adapter; more engines can follow)
  recipes.py            Install methods: rule matching, path mapping, built-ins, custom store
  importer.py           Archive extraction, tree ingest, pak-set validation
  store.py              Central mod store, deploy lists, claim map, enable/disable/backup
  uploader.py           aiohttp LAN upload service (token, allowlist, size caps, QR, recipe form)
  settings.py           JSON-backed settings (managed games, upload port)
src/                    Quick Access panel (games / add / detail / settings)
```

Everything under `moddock/` is decky-free and covered by the pytest suite (126 tests).

## Roadmap

- Decky store submission (needs a real `publish.image` and on-SteamOS testing)
- Editing a custom install method in place (today: delete and re-create)
- More engine adapters — detection and anchors for non-UE games (install shapes themselves are already covered by custom methods)
- Non-ASCII filename polish and `.rar` messaging

## License

BSD-3-Clause (see [LICENSE](LICENSE)).
