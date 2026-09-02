# ModDock v2 Design: Install Recipes

Date: 2026-09-02
Status: approved by the project owner (design settled over four discussion
rounds; symlink deployment was considered and explicitly rejected — enable
stays a real copy).

## 1. Motivation

v1 hardcodes one install shape: flatten pak files into `Paks/~mods`. Real
mods come in many shapes — directory trees (BepInEx, UE4SS Lua), fixed-name
DLLs next to the executable, multi-destination packages (paks + scripts),
archives that mirror the game's directory layout. No plugin can enumerate
every game's conventions, so the install method itself becomes a
user-facing, savable, reusable object: the **Recipe**.

The user flow becomes: upload a mod, pick the game AND the install method;
if no method fits, define a new one on the spot. ModDock ships a few
built-in recipes; custom ones are created in the upload page's form
(typing paths on a phone keyboard beats a gamepad) and stored for reuse.

## 2. Recipe Model

A recipe is a declarative, JSON-serializable object:

```json
{
  "id": "ue-paks-mods",
  "name": "UE ~mods (pak)",
  "builtin": true,
  "rules": [
    {
      "match": ["*.pak", "*.utoc", "*.ucas"],
      "anchor": "paks_dir",
      "subpath": "~mods",
      "mapping": "flatten",
      "overwrite": "refuse"
    }
  ],
  "leftover": "ignore",
  "validate": "pak-set"
}
```

- **rules** — an ordered list; for each file in the (unpacked) upload, the
  first rule whose `match` patterns (fnmatch against the archive-relative
  path, case-insensitive) hit decides where it goes. A single-rule recipe
  is just the degenerate case.
- **anchor** — where the rule's path is rooted. The engine adapter provides
  the anchor map; for UE games: `game_root` (install dir), `paks_dir`
  (`<Project>/Content/Paks`), `win64_dir` (`<Project>/Binaries/Win64`,
  absent when the directory does not exist).
- **subpath** — relative path under the anchor (may be empty or nested;
  created on demand).
- **mapping** — `flatten` (file lands as its basename under the target) or
  `preserve_tree` (the archive-relative path is preserved under the
  target). Before mapping, a single wrapping top-level directory in the
  archive is stripped automatically (the most common packaging shape).
- **overwrite** — `refuse` (default): enabling fails if the destination
  holds a file ModDock does not manage. `backup`: the original file is
  backed up first and restored on disable/delete (see §5).
- **leftover** — what to do with files no rule matched: `ignore` (skip
  them) or `fail` (reject the whole upload naming the first orphan).
- **validate** — optional named validator run against the matched file
  set; v2 defines only `pak-set` (the v1 three-file rule).

### Built-in recipes (fixed, not editable)

| id | name | rules |
|---|---|---|
| `ue-paks-mods` | UE ~mods (pak) | pak/utoc/ucas → `paks_dir/~mods`, flatten, validate pak-set |
| `ue-logic-mods` | UE LogicMods | pak/utoc/ucas → `paks_dir/LogicMods`, flatten, validate pak-set |
| `game-root-merge` | Merge into game folder | `**` → `game_root`, preserve_tree |
| `win64-drop` | Drop next to game EXE | `**` → `win64_dir`, preserve_tree |

Custom recipes live in `~/.local/share/moddock/recipes.json`, are created
from the upload page, and can be inspected/deleted from the panel's
settings view. Builtin and custom recipes share one namespace of ids
(custom ids are generated, e.g. `custom-<random>`).

## 3. Storage Model (unchanged foundation, generalized bookkeeping)

The copy model from the 2026-09-02 revision stands: the repository under
`~/.local/share/moddock/mods/<appid>/<mod>/` holds the full copy and is the
single source of truth; enable copies into the game, disable deletes the
copies. Symlink deployment was evaluated and rejected (exFAT/NTFS shared
libraries, Flatpak Steam sandboxing, and write-through corruption of the
store for replaced game files outweigh the disk/time savings).

The manifest entry generalizes from a flat file list to a deploy list:

```json
"Some Mod": {
  "recipe": "ue-paks-mods",
  "recipe_name": "UE ~mods (pak)",
  "deploy": [
    {"src": "scarlet.pak", "dst": "SB/Content/Paks/~mods/scarlet.pak",
     "overwrite": "refuse"}
  ],
  "source": "ScarletHead.zip",
  "imported_at": "..."
}
```

- **src** — repo-relative path; the repository preserves the (stripped)
  archive tree, so `src` may be nested.
- **dst** — GAME-ROOT-RELATIVE path, resolved at import time from the
  rule's anchor + subpath + mapping. Storing it game-root-relative keeps
  the manifest valid across reinstalls and library moves; anchors are only
  needed again if the project directory name changes (it does not).
- Enabled state remains derived from the filesystem: all `dst` present →
  enabled; some → partial; none → disabled. `game is None` → disabled.

Legacy v1 manifests (a flat `files` list + `repo`) are migrated on first
load: each file becomes `{"src": f, "dst": "<project>/Content/Paks/~mods/" + f,
"overwrite": "refuse"}` with recipe `ue-paks-mods` — the project segment is
recovered from the game's detection info when available, otherwise the
entry stays legacy-flagged and read-only until the game is detected.

## 4. Safety Model (three layers)

1. **Cross-mod claim map.** Per game, every manifest entry's `dst` paths
   form a claim map. Importing a mod whose resolved `dst` collides with
   any other mod's claim is refused, naming the owner. This holds across
   recipes — two different recipes resolving to the same path still
   collide. Within one upload, two files mapping to the same `dst` is
   likewise an error.
2. **List-precise recall.** Disable/delete unlink exactly the recorded
   `dst` paths. Directories are never removed — targets like the game
   root or `r6/scripts`-style shared folders contain other mods' and the
   game's own files. Empty directories left behind are harmless.
3. **Unmanaged files: refuse by default, backup on request.**
   - `refuse` rules: at import AND at enable, a `dst` that exists but is
     not claimed by this mod fails with a message telling the user to
     remove the foreign file by hand.
   - `backup` rules: at enable, an existing unmanaged `dst` is moved to
     `~/.local/share/moddock/backup/<appid>/<dst>` before the copy.
     Disable and delete restore the backup (move it back) instead of just
     unlinking. A backup is taken at most once per `dst` (the first enable
     preserves the true original). Documented caveat: if the game updated
     that file while the mod was enabled, the restored original is
     outdated — verify game files in Steam. Backups are real files, never
     links.

## 5. Import Pipeline (per upload)

1. Upload page sends: file + appid + recipe id.
2. Extract to temp (unchanged: zip stdlib / 7z via system tool, temp under
   the plugin data dir, containment sweep).
3. Strip a single wrapping top-level directory if present.
4. Apply the recipe: first-match rule per file → deploy list; `leftover`
   policy for unmatched files; recipe validator (e.g. pak-set) over the
   matched set.
5. Safety checks: intra-upload dst uniqueness; cross-mod claim map;
   refuse-rule unmanaged collision (checked now and again at enable).
6. Copy matched files into the repository preserving their (stripped)
   archive-relative paths; write the manifest entry; enable immediately.
7. Any failure discards everything (repo dir removed, staging file
   discarded) and reports the reason verbatim to the upload page.

## 6. Upload Page and Panel Changes

- The `GET /u/<token>/games` payload becomes
  `{"games": [...], "recipes": [{"id", "name", "builtin"}...]}`.
- The page shows a second required dropdown, "Install method", defaulting
  to the last method used FOR THAT GAME (localStorage, per-appid key; the
  UE pak recipe is the initial default).
- The last dropdown entry is "+ New install method…", expanding an inline
  form: name; a repeatable list of rule rows (match patterns
  comma-separated, anchor dropdown, subpath text, mapping toggle,
  overwrite toggle); leftover toggle. Submitting POSTs
  `/u/<token>/recipes` (same token auth), the new recipe is selected, and
  it becomes reusable everywhere.
- Anchor availability: the games payload includes each game's available
  anchors so the form can grey out anchors a game lacks (e.g. no
  `win64_dir` detected). Choosing a recipe whose anchor a game lacks
  fails the upload with a clear message.
- Panel settings view: a "Install methods" section listing custom recipes
  (name + rule count) with delete; builtins are listed read-only.
- New/changed callables: `list_recipes()`, `delete_recipe(id)`;
  `_install_upload` gains the recipe id. Recipe creation happens through
  the uploader HTTP endpoint (the phone is where the form lives), which
  calls back into the same store of recipes.

## 7. Non-Goals (unchanged from prior discussions, restated)

- Load-order management (Bethesda `plugins.txt`) — a different problem
  domain, deliberately excluded.
- Text-registry enable/disable (UE4SS `mods.txt`) — copy-presence only in
  v2; may later become a builtin-only strategy.
- Automatic framework installation and dependency resolution — users
  install BepInEx/UE4SS themselves via `game-root-merge`.
- Symlink/hardlink deployment — rejected, see §3.
- Non-Steam games and non-UE game scanning — the Add Game scan remains
  UE-only in v2; recipes with `game_root` work for any game the scan
  accepts today.

## 8. Testing Strategy

- recipes module: rule matching (order, patterns, case), both mappings,
  wrapper-stripping, leftover policies, anchor resolution incl. missing
  anchors, dst collision within an upload.
- store v2: claim map across recipes; refuse vs backup enable paths;
  backup-restore on disable/delete (incl. backup taken once); legacy
  manifest migration; all v1.1 state-machine tests ported to deploy lists.
- uploader: recipes in the games payload; recipe-creation endpoint (token
  auth, validation of anchors/mappings); installer receives the recipe id.
- main: end-to-end with a multi-destination recipe (pak → ~mods, lua →
  win64_dir/ue4ss) on a fake UE tree; recipe CRUD callables.
- Frontend: build passes; settings view lists/deletes custom recipes.
