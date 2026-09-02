# ModDock On-Device Test Checklist

Device: ROG Xbox Ally X, Bazzite, Decky Loader installed.
Test game: Stellar Blade. Test mod: Seamless EVE Scarlet Head
(mesh pak set + one texture pak set, from Nexus).

1. [ ] `scripts/deploy.sh user@ally` completes; ModDock appears in QAM.
2. [ ] Games list is empty on first run; "Add Game" scans and lists
       Stellar Blade with a UE badge.
3. [ ] Adding Stellar Blade persists across a Decky reload.
4. [ ] Upload Settings: toggling the service on shows URL + QR.
5. [ ] Phone on the same LAN: QR opens the page; the game dropdown lists
       Stellar Blade (required — upload is blocked until a game is picked;
       the choice is remembered on the next visit).
6. [ ] The file picker allows selecting BOTH mod archives at once; each
       file uploads with its own progress bar.
7. [ ] Each archive reports `installed as "<name>"` on the page; the mods
       appear enabled in the game view (open panel refreshes via event) and
       the files exist in `SB/Content/Paks/~mods/`.
8. [ ] Wrong-token URL returns 404; an `.exe` or `.rar` upload is rejected
       on the page with a per-file reason.
9. [ ] Toggle a mod off: its files disappear from `~mods` while the full
       copy stays in `~/.local/share/moddock/mods/<appid>/<mod name>/`;
       toggling back on copies them in again.
10. [ ] Launch the game with the mods enabled: no crash, head swapped.
11. [ ] Uninstall-safety spot check (optional): uninstalling the game shows
        every mod as disabled with the game row marked "not detected as
        installed"; after reinstalling, mods stay disabled and can be
        re-enabled manually.
12. [ ] Delete mod with confirmation: files gone from `~mods` and from the
        store repository.
13. [ ] Remove game from list: mods manifest untouched on disk.
