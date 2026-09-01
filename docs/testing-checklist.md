# ModDock On-Device Test Checklist

Device: ROG Xbox Ally X, Bazzite, Decky Loader installed.
Test game: Stellar Blade. Test mod: Seamless EVE Scarlet Head
(mesh pak set + one texture pak set, from Nexus).

1. [ ] `scripts/deploy.sh user@ally` completes; ModDock appears in QAM.
2. [ ] Games list is empty on first run; "Add Game" scans and lists
       Stellar Blade with a UE badge.
3. [ ] Adding Stellar Blade persists across a Decky reload.
4. [ ] Upload Settings: toggling the service on shows URL + QR.
5. [ ] Phone on the same LAN: QR opens the page; uploading the mod
       archives lands them in the Inbox (panel refreshes via event).
6. [ ] Wrong-token URL returns 404; an `.exe` upload is rejected.
7. [ ] Inbox: entries show "ready"; a `.rar` shows an unsupported note.
8. [ ] Assign to Stellar Blade: mod appears in the game view, enabled;
       files exist in `SB/Content/Paks/~mods/`.
9. [ ] Toggle off: files return to `~/.local/share/moddock/mods/<appid>/`.
10. [ ] Launch the game with the mod enabled: no crash, head swapped.
11. [ ] Delete mod with confirmation: files gone from both locations.
12. [ ] Remove game from list: mods manifest untouched on disk.
