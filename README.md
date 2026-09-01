# ModDock

Upload game mods from your phone or PC over LAN and manage them per
game, right from Gaming Mode. v1 supports Unreal Engine (UE4/UE5)
pak-style mods.

## How it works

1. Add a game — ModDock scans your Steam libraries and detects
   Unreal Engine games automatically.
2. Toggle on the upload service and scan the QR code with your phone.
3. Upload a mod archive (`.zip`/`.7z`) or bare `.pak`/`.utoc`/`.ucas`
   files from any browser on your LAN.
4. Assign the upload to a game, then enable/disable mods per game.

Mods live in a central store under `~/.local/share/moddock/`;
enabling moves the files into the game's `Paks/~mods` directory,
disabling moves them back. No symlinks, no copies.

## Development

- Frontend: `pnpm install && pnpm build`
- Backend tests: `python3 -m venv venv && ./venv/bin/pip install -r
  requirements-dev.txt && ./venv/bin/python -m pytest tests -v`
- Deploy to a handheld: `scripts/deploy.sh user@handheld`

## License

BSD-3-Clause (see LICENSE).
