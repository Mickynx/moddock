# ModDock On-Device Test Checklist

Device: ROG Xbox Ally X, Bazzite, Decky Loader installed.
Test game: Stellar Blade. Test mod: Seamless EVE Scarlet Head
(mesh pak set + one texture pak set, from Nexus).
Also needed: a hand-made "combo" zip (one `.pak` plus a `foo.lua`) to
exercise a multi-rule install method, and a throwaway text file to plant
as a fake game file for the backup test.

1. [ ] `scripts/deploy.sh user@ally` completes; ModDock appears in QAM.
2. [ ] Games list is empty on first run; "Add Game" scans and lists
       Stellar Blade with a UE badge.
3. [ ] Adding Stellar Blade persists across a Decky reload.
4. [ ] Upload Settings: toggling the service on shows URL + QR.
5. [ ] Phone on the same LAN: QR opens the page; the game dropdown lists
       Stellar Blade and the install-method dropdown lists the four
       built-ins. BOTH are required — uploading with either unpicked is
       blocked with a message on the page; both choices are remembered
       for that game on the next visit. Pick "UE ~mods (pak)".
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
11. [ ] Custom install method, created on the phone: choose "+ New install
        method…", name it `Pak + Lua`, and give it two rules —
        (a) "Files matching" `*.pak, *.utoc, *.ucas`, install under
        `paks_dir`, subfolder `~mods`, layout `flatten`, if the file
        exists `refuse`; (b) "Files matching" `*.lua`, install under
        `win64_dir`, subfolder `ue4ss/Mods`, layout `preserve_tree`, if
        the file exists `refuse`; files no rule matches → `ignore`.
        Saving reports success and selects the new method. Anchors the
        game lacks are greyed out and unselectable (`win64_dir` only
        appears once `<Project>/Binaries/Win64` exists).
12. [ ] Upload the combo zip (`mod.pak` + `foo.lua` at its top level) with
        `Pak + Lua`: the page reports `installed as "<name>"`, and BOTH
        destinations are real — `SB/Content/Paks/~mods/mod.pak` and
        `SB/Binaries/Win64/ue4ss/Mods/foo.lua`. The panel lists the mod
        with `Pak + Lua` as its install method; toggling it off removes
        both files, toggling it on restores both.
13. [ ] The method survives: it appears under *Install methods* in the
        panel's settings (custom · 2 rules) and in the phone dropdown
        after a page reload.
14. [ ] Overwrite protection, refuse: plant a throwaway `probe.txt`
        ("original") in the game root. Create a method `Root txt` with one
        rule — `*.txt` under `game_root`, no subfolder, layout
        `preserve_tree`, if the file exists `refuse` — and upload
        `probe-mod.zip`, a zip holding a `probe.txt` with different
        content. The upload fails on the page saying the destination
        already exists and is not managed by ModDock; the planted file is
        untouched and no mod appears in the panel.
15. [ ] Overwrite protection, backup/restore: create the same method with
        "if the file exists" = **backup** (`Root txt (backup)`) and upload
        the same zip with it. It succeeds; the game root's `probe.txt`
        now holds the mod's content and the original is parked at
        `~/.local/share/moddock/backup/<appid>/probe.txt`. Toggle the mod
        off: `probe.txt` is the "original" content again and the parked
        backup is gone. Toggle it on and off once more (the backup is
        re-taken and restored), then delete the mod: the original is
        still in the game root — recall never eats it.
16. [ ] Delete a custom install method in the panel's settings: it
        disappears from the list and from the phone dropdown, while mods
        already installed with it still toggle on and off correctly.
        Built-in methods show as "built-in" with no delete control.
17. [ ] Uninstall-safety spot check (optional): uninstalling the game shows
        every mod as disabled with the game row marked "not detected as
        installed"; after reinstalling, mods stay disabled and can be
        re-enabled manually.
18. [ ] Delete mod with confirmation: files gone from `~mods` and from the
        store repository.
19. [ ] Remove game from list: mods manifest untouched on disk.
