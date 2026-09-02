# ModDock v2 (Install Recipes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** User-selectable, savable install methods (Recipes): ordered rule lists mapping an upload's file tree onto game locations, with refuse/backup overwrite protection, chosen per upload on the web page and creatable there.

**Architecture:** New `moddock/recipes.py` (model, rule engine, custom-recipe store) feeds a generalized `moddock/store.py` (deploy lists of game-root-relative paths, cross-recipe claim map, backup/restore). `moddock/importer.py` gains tree-preserving ingestion. The uploader exposes recipes + a creation endpoint and the page gains a second dropdown + inline form. Copy semantics (repo = source of truth) is unchanged.

**Tech Stack:** unchanged (Python 3.10+ stdlib + aiohttp, pytest; React/TS via @decky/ui).

**Spec:** `docs/specs/2026-09-02-moddock-recipes-design.md` (binding); background: `docs/specs/2026-09-01-moddock-design.md`.

## Global Constraints

- All docs/comments/UI copy in English; `moddock/` never imports `decky`; Python floor 3.10.
- Backend test command: `./venv/bin/python -m pytest tests -v` — must end green with 0 warnings after EVERY task.
- Frontend: `pnpm build` and `npx tsc --noEmit` must pass after frontend tasks.
- Copy semantics stands: enable = real copy, disable = delete/restore. No symlinks (explicitly rejected in the spec).
- `dst` paths in manifests are game-root-relative POSIX strings; directories are never deleted, only recorded files.
- Anchors: `game_root`, `paks_dir`, `win64_dir`. Mappings: `flatten`, `preserve_tree`. Overwrite: `refuse` (default), `backup`. Leftover: `ignore` (default), `fail`. Validator names: `pak-set`.
- Commit after every green task; conventional prefixes.

---

### Task 1: Recipe model and rule engine (`moddock/recipes.py`)

**Files:**
- Create: `moddock/recipes.py`
- Modify: `moddock/importer.py` (extract a pure `pak_set_errors` helper; keep `scan_mod_files` delegating to it)
- Test: `tests/test_recipes.py`; Modify: `tests/test_importer.py` (add one test for `pak_set_errors`)

**Interfaces:**
- Consumes: nothing new.
- Produces (all consumed by Tasks 4–6):
  - `class RecipeError(Exception)` — user-facing message.
  - `@dataclass(frozen=True) Rule(match: tuple[str, ...], anchor: str, subpath: str, mapping: str = "flatten", overwrite: str = "refuse")`
  - `@dataclass(frozen=True) Recipe(id: str, name: str, rules: tuple[Rule, ...], leftover: str = "ignore", validate: str | None = None, builtin: bool = False)`
  - `@dataclass(frozen=True) DeployItem(src: str, dst: str, overwrite: str)`
  - `recipe_from_dict(data: dict, *, recipe_id: str, builtin: bool = False) -> Recipe` (raises RecipeError)
  - `recipe_to_dict(recipe: Recipe) -> dict`
  - `apply_recipe(recipe: Recipe, files: list[str], anchors: dict[str, str | None]) -> list[DeployItem]` (raises RecipeError)
  - `BUILTIN_RECIPES: tuple[Recipe, ...]` — ids `ue-paks-mods`, `ue-logic-mods`, `game-root-merge`, `win64-drop` per spec §2.
  - `class RecipeStore(path: Path)` with `list() -> list[Recipe]` (builtins first), `get(recipe_id) -> Recipe | None`, `create(data: dict) -> Recipe`, `delete(recipe_id) -> None`.
  - `moddock.importer.pak_set_errors(names: Iterable[str]) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_recipes.py`:

```python
import pytest

from moddock.recipes import (
    BUILTIN_RECIPES,
    Recipe,
    RecipeError,
    RecipeStore,
    Rule,
    apply_recipe,
    recipe_from_dict,
    recipe_to_dict,
)

ANCHORS = {"game_root": "", "paks_dir": "SB/Content/Paks", "win64_dir": None}


def _recipe(**overrides) -> Recipe:
    data = {
        "name": "Test",
        "rules": [
            {"match": ["*.pak", "*.utoc", "*.ucas"], "anchor": "paks_dir",
             "subpath": "~mods"},
        ],
    }
    data.update(overrides)
    return recipe_from_dict(data, recipe_id="t")


def test_builtins_present_and_valid():
    ids = [r.id for r in BUILTIN_RECIPES]
    assert ids == ["ue-paks-mods", "ue-logic-mods", "game-root-merge", "win64-drop"]
    assert all(r.builtin for r in BUILTIN_RECIPES)
    assert BUILTIN_RECIPES[0].validate == "pak-set"


def test_from_dict_validates():
    with pytest.raises(RecipeError):
        recipe_from_dict({"name": "", "rules": []}, recipe_id="x")
    with pytest.raises(RecipeError):
        _recipe(rules=[{"match": ["*"], "anchor": "nope", "subpath": ""}])
    with pytest.raises(RecipeError):
        _recipe(rules=[{"match": ["*"], "anchor": "game_root",
                        "subpath": "../escape"}])
    with pytest.raises(RecipeError):
        _recipe(rules=[{"match": ["*"], "anchor": "game_root", "subpath": "",
                        "mapping": "hardlink"}])
    with pytest.raises(RecipeError):
        _recipe(leftover="explode")
    # Round-trip survives.
    r = _recipe()
    assert recipe_from_dict(recipe_to_dict(r), recipe_id=r.id).rules == r.rules


def test_apply_flatten_and_ordering():
    r = recipe_from_dict(
        {
            "name": "SB pak + lua",
            "rules": [
                {"match": ["*.pak", "*.utoc", "*.ucas"], "anchor": "paks_dir",
                 "subpath": "~mods"},
                {"match": ["*.lua"], "anchor": "game_root",
                 "subpath": "scripts", "mapping": "preserve_tree"},
            ],
        },
        recipe_id="combo",
    )
    items = apply_recipe(
        r, ["nested/mod.pak", "nested/mod.utoc", "nested/mod.ucas",
            "Mods/My/main.lua"], ANCHORS
    )
    by_src = {i.src: i.dst for i in items}
    assert by_src["nested/mod.pak"] == "SB/Content/Paks/~mods/mod.pak"
    assert by_src["Mods/My/main.lua"] == "scripts/Mods/My/main.lua"
    assert all(i.overwrite == "refuse" for i in items)


def test_apply_first_match_wins():
    r = recipe_from_dict(
        {
            "name": "order",
            "rules": [
                {"match": ["special.pak"], "anchor": "game_root", "subpath": "a"},
                {"match": ["*.pak"], "anchor": "game_root", "subpath": "b"},
            ],
        },
        recipe_id="o",
    )
    items = apply_recipe(r, ["special.pak", "other.pak"], ANCHORS)
    assert {i.dst for i in items} == {"a/special.pak", "b/other.pak"}


def test_apply_match_is_case_insensitive():
    items = apply_recipe(_recipe(), ["MOD.PAK", "MOD.UTOC", "MOD.UCAS"], ANCHORS)
    assert len(items) == 3


def test_apply_leftover_policies():
    files = ["mod.pak", "mod.utoc", "mod.ucas", "readme.txt"]
    assert len(apply_recipe(_recipe(), files, ANCHORS)) == 3  # ignore drops it
    with pytest.raises(RecipeError) as exc:
        apply_recipe(_recipe(leftover="fail"), files, ANCHORS)
    assert "readme.txt" in str(exc.value)


def test_apply_missing_anchor_raises():
    r = _recipe(rules=[{"match": ["*"], "anchor": "win64_dir", "subpath": ""}])
    with pytest.raises(RecipeError) as exc:
        apply_recipe(r, ["a.dll"], ANCHORS)
    assert "win64_dir" in str(exc.value)


def test_apply_nothing_matched_raises():
    with pytest.raises(RecipeError):
        apply_recipe(_recipe(), ["readme.txt"], ANCHORS)


def test_apply_duplicate_dst_raises():
    with pytest.raises(RecipeError) as exc:
        apply_recipe(_recipe(), ["a/mod.pak", "b/mod.pak", "mod.utoc",
                                 "mod.ucas", "a/mod.utoc", "a/mod.ucas",
                                 "b/mod.utoc", "b/mod.ucas"], ANCHORS)
    assert "mod.pak" in str(exc.value)


def test_apply_runs_pak_set_validator():
    with pytest.raises(RecipeError) as exc:
        apply_recipe(_recipe(validate="pak-set"), ["mod.pak", "mod.utoc"],
                     ANCHORS)
    assert ".ucas" in str(exc.value)


def test_apply_rejects_traversal_in_tree():
    r = _recipe(rules=[{"match": ["**"], "anchor": "game_root", "subpath": "",
                        "mapping": "preserve_tree"}])
    with pytest.raises(RecipeError):
        apply_recipe(r, ["../evil.pak"], ANCHORS)


def test_store_create_get_delete(tmp_path):
    store = RecipeStore(tmp_path / "recipes.json")
    assert [r.id for r in store.list()][:4] == [b.id for b in BUILTIN_RECIPES]

    created = store.create(
        {"name": "My method", "rules": [
            {"match": ["*.dll"], "anchor": "win64_dir", "subpath": ""}]}
    )
    assert created.id.startswith("custom-")
    assert store.get(created.id) is not None
    # Persisted across a reload.
    reloaded = RecipeStore(tmp_path / "recipes.json")
    assert reloaded.get(created.id).name == "My method"

    reloaded.delete(created.id)
    assert RecipeStore(tmp_path / "recipes.json").get(created.id) is None


def test_store_refuses_touching_builtins(tmp_path):
    store = RecipeStore(tmp_path / "recipes.json")
    with pytest.raises(RecipeError):
        store.delete("ue-paks-mods")


def test_store_survives_corrupt_file(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_bytes(b"\xff{not json")
    assert [r.id for r in RecipeStore(path).list()] == [
        b.id for b in BUILTIN_RECIPES
    ]
```

Add to `tests/test_importer.py`:

```python
def test_pak_set_errors_pure_helper():
    from moddock.importer import pak_set_errors

    assert pak_set_errors(["a/mod.pak", "a/mod.utoc", "a/mod.ucas"]) == []
    assert pak_set_errors(["solo.pak"]) == []
    errors = pak_set_errors(["mod.pak", "mod.utoc"])
    assert errors and ".ucas" in errors[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_recipes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'moddock.recipes'`

- [ ] **Step 3: Write the implementation**

In `moddock/importer.py`, add near `scan_mod_files` (and make `scan_mod_files` build its error list by calling it):

```python
def pak_set_errors(names) -> list[str]:
    """Pak-set rule over relative path strings: a stem with a .utoc or .ucas
    must have all three members; a standalone .pak is fine."""
    by_stem: dict[str, set[str]] = {}
    for name in names:
        p = PurePosixPath(str(name).replace("\\", "/"))
        if p.suffix.lower() in MOD_FILE_EXTS:
            by_stem.setdefault(p.stem, set()).add(p.suffix.lower())
    errors: list[str] = []
    for stem, exts in sorted(by_stem.items()):
        if exts & {".utoc", ".ucas"}:
            missing = {".pak", ".utoc", ".ucas"} - exts
            if missing:
                errors.append(
                    f"{stem}: incomplete pak set, missing "
                    + ", ".join(sorted(missing))
                )
    return errors
```

(`from pathlib import PurePosixPath` may need adding to importer's imports.)

`moddock/recipes.py`:

```python
"""Install recipes: declarative descriptions of how an upload's file tree
maps into a game's directory layout.

A recipe is an ordered rule list; for each file the first matching rule
decides the destination. Rules are rooted at named anchors the engine
adapter provides, so recipes stay game-agnostic and reusable. Built-in
recipes cover the common shapes; custom ones are created from the upload
page and persisted in recipes.json.
"""

from __future__ import annotations

import fnmatch
import json
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .importer import pak_set_errors

ANCHOR_NAMES = ("game_root", "paks_dir", "win64_dir")
MAPPINGS = ("flatten", "preserve_tree")
OVERWRITES = ("refuse", "backup")
LEFTOVERS = ("ignore", "fail")
VALIDATORS = ("pak-set",)


class RecipeError(Exception):
    """User-facing recipe failure; str(exc) is shown verbatim."""


@dataclass(frozen=True)
class Rule:
    match: tuple[str, ...]
    anchor: str
    subpath: str
    mapping: str = "flatten"
    overwrite: str = "refuse"


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    rules: tuple[Rule, ...]
    leftover: str = "ignore"
    validate: str | None = None
    builtin: bool = False


@dataclass(frozen=True)
class DeployItem:
    src: str  # repo-relative POSIX path (the unwrapped archive path)
    dst: str  # game-root-relative POSIX path
    overwrite: str


def _clean_subpath(raw: str) -> str:
    subpath = str(raw).strip().strip("/")
    if not subpath:
        return ""
    parts = PurePosixPath(subpath).parts
    if ".." in parts or subpath.startswith("/") or ":" in subpath:
        raise RecipeError(f'invalid subpath "{raw}"')
    return str(PurePosixPath(*parts))


def recipe_from_dict(data: dict, *, recipe_id: str, builtin: bool = False) -> Recipe:
    if not isinstance(data, dict):
        raise RecipeError("recipe must be an object")
    name = str(data.get("name") or "").strip()
    if not name:
        raise RecipeError("recipe needs a name")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RecipeError("recipe needs at least one rule")
    rules: list[Rule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise RecipeError("each rule must be an object")
        patterns = tuple(
            str(p).strip() for p in raw.get("match", []) if str(p).strip()
        )
        if not patterns:
            raise RecipeError("each rule needs at least one match pattern")
        anchor = raw.get("anchor")
        if anchor not in ANCHOR_NAMES:
            raise RecipeError(f'unknown anchor "{anchor}"')
        mapping = raw.get("mapping", "flatten")
        if mapping not in MAPPINGS:
            raise RecipeError(f'unknown mapping "{mapping}"')
        overwrite = raw.get("overwrite", "refuse")
        if overwrite not in OVERWRITES:
            raise RecipeError(f'unknown overwrite mode "{overwrite}"')
        rules.append(
            Rule(
                match=patterns,
                anchor=anchor,
                subpath=_clean_subpath(raw.get("subpath", "")),
                mapping=mapping,
                overwrite=overwrite,
            )
        )
    leftover = data.get("leftover", "ignore")
    if leftover not in LEFTOVERS:
        raise RecipeError(f'unknown leftover policy "{leftover}"')
    validate = data.get("validate") or None
    if validate is not None and validate not in VALIDATORS:
        raise RecipeError(f'unknown validator "{validate}"')
    return Recipe(
        id=recipe_id,
        name=name,
        rules=tuple(rules),
        leftover=leftover,
        validate=validate,
        builtin=builtin,
    )


def recipe_to_dict(recipe: Recipe) -> dict:
    return {
        "id": recipe.id,
        "name": recipe.name,
        "builtin": recipe.builtin,
        "leftover": recipe.leftover,
        "validate": recipe.validate,
        "rules": [
            {
                "match": list(rule.match),
                "anchor": rule.anchor,
                "subpath": rule.subpath,
                "mapping": rule.mapping,
                "overwrite": rule.overwrite,
            }
            for rule in recipe.rules
        ],
    }


def _matches(rule: Rule, relpath: str) -> bool:
    # Plain fnmatch over the whole relative path; '*' crosses directory
    # separators, so "*.pak" also hits nested paks.
    lower = relpath.lower()
    return any(fnmatch.fnmatchcase(lower, p.lower()) for p in rule.match)


def apply_recipe(
    recipe: Recipe, files: list[str], anchors: dict[str, str | None]
) -> list[DeployItem]:
    items: list[DeployItem] = []
    orphans: list[str] = []
    for rel in files:
        rel_posix = str(rel).replace("\\", "/").strip("/")
        if ".." in PurePosixPath(rel_posix).parts:
            raise RecipeError(f'unsafe path in upload: "{rel}"')
        rule = next((r for r in recipe.rules if _matches(r, rel_posix)), None)
        if rule is None:
            orphans.append(rel_posix)
            continue
        base = anchors.get(rule.anchor)
        if base is None:
            raise RecipeError(
                f'this game has no "{rule.anchor}" location — '
                "pick another install method"
            )
        tail = (
            PurePosixPath(rel_posix).name
            if rule.mapping == "flatten"
            else rel_posix
        )
        segments = [s for s in (base, rule.subpath) if s]
        dst = str(PurePosixPath(*segments, tail)) if segments else tail
        items.append(DeployItem(src=rel_posix, dst=dst, overwrite=rule.overwrite))
    if orphans and recipe.leftover == "fail":
        raise RecipeError(
            f'no rule matches "{orphans[0]}" and this install method '
            "rejects leftovers"
        )
    if not items:
        raise RecipeError("no files in the upload match this install method")
    seen: dict[str, str] = {}
    for item in items:
        if item.dst in seen:
            raise RecipeError(
                f'two files map to the same destination "{item.dst}"'
            )
        seen[item.dst] = item.src
    if recipe.validate == "pak-set":
        errors = pak_set_errors([i.src for i in items])
        if errors:
            raise RecipeError("; ".join(errors))
    return items


def _builtin(recipe_id: str, name: str, rules: tuple[Rule, ...], **kw) -> Recipe:
    return Recipe(id=recipe_id, name=name, rules=rules, builtin=True, **kw)


BUILTIN_RECIPES: tuple[Recipe, ...] = (
    _builtin(
        "ue-paks-mods",
        "UE ~mods (pak)",
        (Rule(("*.pak", "*.utoc", "*.ucas"), "paks_dir", "~mods"),),
        validate="pak-set",
    ),
    _builtin(
        "ue-logic-mods",
        "UE LogicMods",
        (Rule(("*.pak", "*.utoc", "*.ucas"), "paks_dir", "LogicMods"),),
        validate="pak-set",
    ),
    _builtin(
        "game-root-merge",
        "Merge into game folder",
        (Rule(("*",), "game_root", "", mapping="preserve_tree"),),
    ),
    _builtin(
        "win64-drop",
        "Drop next to game EXE",
        (Rule(("*",), "win64_dir", "", mapping="preserve_tree"),),
    ),
)


class RecipeStore:
    """Custom recipes persisted as JSON; builtins are compiled in."""

    def __init__(self, path: Path):
        self.path = path
        self._custom: list[Recipe] = []
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for raw in data.get("recipes", []):
                    self._custom.append(
                        recipe_from_dict(raw, recipe_id=str(raw.get("id")))
                    )
            except (ValueError, OSError, RecipeError):
                self._custom = []  # corrupt store falls back to builtins only

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"recipes": [recipe_to_dict(r) for r in self._custom]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list(self) -> list[Recipe]:
        return list(BUILTIN_RECIPES) + list(self._custom)

    def get(self, recipe_id: str) -> Recipe | None:
        return next((r for r in self.list() if r.id == recipe_id), None)

    def create(self, data: dict) -> Recipe:
        recipe = recipe_from_dict(data, recipe_id=f"custom-{secrets.token_hex(4)}")
        self._custom.append(recipe)
        self._save()
        return recipe

    def delete(self, recipe_id: str) -> None:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise RecipeError(f'unknown install method "{recipe_id}"')
        if recipe.builtin:
            raise RecipeError("built-in install methods cannot be deleted")
        self._custom = [r for r in self._custom if r.id != recipe_id]
        self._save()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_recipes.py tests/test_importer.py -v`
Expected: all PASS. Then the full suite once.

- [ ] **Step 5: Commit**

```bash
git add moddock/recipes.py moddock/importer.py tests/test_recipes.py tests/test_importer.py
git commit -m "feat: recipe model, rule engine and custom-recipe store"
```

---

### Task 2: Anchors on the UE adapter (`moddock/adapters/unreal.py`)

**Files:**
- Modify: `moddock/adapters/unreal.py`
- Test: `tests/test_unreal.py` (add cases; existing tests must keep passing)

**Interfaces:**
- Produces: `UEGameInfo` gains two OPTIONAL fields (defaults keep every existing constructor call working): `install_dir: Path | None = None`, `win64_dir: Path | None = None`; and a method `anchor_map(self) -> dict[str, str | None]` returning game-root-relative POSIX strings: `{"game_root": "", "paks_dir": <rel>, "win64_dir": <rel or None>}`; raises `ValueError` if `install_dir` is unset. `detect_ue_game` fills both new fields (`win64_dir` = `<install>/<Project>/Binaries/Win64` when that directory exists, else None).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_unreal.py`)

```python
def test_detect_fills_install_dir_and_win64(tmp_path):
    _make_ue_game(tmp_path, project="SB")
    info = detect_ue_game(tmp_path)
    assert info.install_dir == tmp_path
    assert info.win64_dir == tmp_path / "SB" / "Binaries" / "Win64"


def test_win64_dir_none_when_absent(tmp_path):
    _make_ue_game(tmp_path, shipping_exe=False)
    info = detect_ue_game(tmp_path)
    assert info.win64_dir is None


def test_anchor_map(tmp_path):
    _make_ue_game(tmp_path, project="SB")
    anchors = detect_ue_game(tmp_path).anchor_map()
    assert anchors == {
        "game_root": "",
        "paks_dir": "SB/Content/Paks",
        "win64_dir": "SB/Binaries/Win64",
    }


def test_anchor_map_requires_install_dir():
    import pytest

    info = UEGameInfo(
        project_name="SB",
        paks_dir=Path("/x/SB/Content/Paks"),
        mods_dir=Path("/x/SB/Content/Paks/~mods"),
        is_iostore=False,
        has_shipping_exe=False,
    )
    with pytest.raises(ValueError):
        info.anchor_map()
```

(Adjust imports at the top of the test file as needed: `UEGameInfo`, `Path`, `pytest`.)

Note: `_make_ue_game(..., shipping_exe=True)` already creates `Binaries/Win64`; with `shipping_exe=False` it does not, which is what the second test relies on.

- [ ] **Step 2: Run to verify the new tests fail**, then implement:

In `UEGameInfo` add fields (AFTER the existing ones, with defaults):

```python
    install_dir: Path | None = None
    win64_dir: Path | None = None
```

and the method:

```python
    def anchor_map(self) -> dict[str, str | None]:
        """Game-root-relative anchor paths for the recipe engine."""
        if self.install_dir is None:
            raise ValueError("anchor_map() needs install_dir")

        def rel(path: Path) -> str:
            return path.relative_to(self.install_dir).as_posix()

        return {
            "game_root": "",
            "paks_dir": rel(self.paks_dir),
            "win64_dir": rel(self.win64_dir) if self.win64_dir else None,
        }
```

In `detect_ue_game`, before the return, compute:

```python
    win64 = install_dir / project_name / "Binaries" / "Win64"
```

and pass `install_dir=install_dir, win64_dir=win64 if win64.is_dir() else None` to the constructor.

- [ ] **Step 3: Full suite green, commit**

```bash
git add moddock/adapters/unreal.py tests/test_unreal.py
git commit -m "feat: expose install-dir anchors on UE detection"
```

---

### Task 3: Tree-preserving ingestion (`moddock/importer.py`)

**Files:**
- Modify: `moddock/importer.py`
- Test: `tests/test_importer.py` (add tests; do NOT remove existing functions yet — Task 4 does the cleanup when the store stops using them)

**Interfaces:**
- Produces: `ingest_tree(source: Path, dest: Path) -> list[str]` — extract (zip/7z) or accept a bare non-archive file, strip wrapping top-level directories (repeatedly while the root holds exactly one directory and no files), copy EVERYTHING (no extension filtering — the recipe decides) into `dest` preserving relative paths, and return the sorted POSIX relative paths. Every failure raises `ImportProblem`. The existing containment sweep and TEMP_ROOT behavior apply.

- [ ] **Step 1: Failing tests** (append to `tests/test_importer.py`)

```python
def test_ingest_tree_preserves_structure(tmp_path):
    from moddock.importer import ingest_tree

    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x")
        zf.writestr("Mods/My/scripts/main.lua", "x")
    dest = tmp_path / "repo"
    assert ingest_tree(archive, dest) == ["Mods/My/scripts/main.lua", "mod.pak"]
    assert (dest / "Mods/My/scripts/main.lua").is_file()


def test_ingest_tree_strips_wrapper_dirs(tmp_path):
    from moddock.importer import ingest_tree

    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Wrapper/Inner/mod.pak", "x")
        zf.writestr("Wrapper/Inner/readme.txt", "x")
    tree = ingest_tree(archive, tmp_path / "repo")
    assert tree == ["mod.pak", "readme.txt"]


def test_ingest_tree_bare_file(tmp_path):
    from moddock.importer import ingest_tree

    (tmp_path / "solo.pak").write_bytes(b"x")
    assert ingest_tree(tmp_path / "solo.pak", tmp_path / "repo") == ["solo.pak"]


def test_ingest_tree_empty_archive_raises(tmp_path):
    from moddock.importer import ImportProblem, ingest_tree

    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w"):
        pass
    with pytest.raises(ImportProblem):
        ingest_tree(archive, tmp_path / "repo")


def test_ingest_tree_wraps_os_errors(tmp_path, monkeypatch):
    from moddock.importer import ImportProblem, ingest_tree

    archive = tmp_path / "m.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mod.pak", "x")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("moddock.importer.shutil.copy2", boom)
    with pytest.raises(ImportProblem):
        ingest_tree(archive, tmp_path / "repo")
```

- [ ] **Step 2: RED, then implement** in `moddock/importer.py`:

```python
def _strip_wrappers(root: Path) -> Path:
    """Descend through single-directory wrappers (the usual packaging shell)."""
    current = root
    while True:
        entries = [p for p in current.iterdir()]
        dirs = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        if len(dirs) == 1 and not files:
            current = dirs[0]
        else:
            return current


def ingest_tree(source: Path, dest: Path) -> list[str]:
    """Extract/copy `source` into `dest` preserving the (unwrapped) tree.

    Returns sorted POSIX relative paths. No extension filtering happens
    here — the recipe engine decides what each file means. All failures
    surface as ImportProblem.
    """
    try:
        if not source.is_file():
            raise ImportProblem(f"file not found: {source.name}")
        with tempfile.TemporaryDirectory(
            prefix="moddock-ingest-", dir=_temp_dir()
        ) as tmp:
            workdir = Path(tmp)
            if source.suffix.lower() in ARCHIVE_EXTS:
                extract_archive(source, workdir)
                root = _strip_wrappers(workdir)
            else:
                shutil.copy2(source, workdir / source.name)
                root = workdir
            files = sorted(
                p.relative_to(root).as_posix()
                for p in root.rglob("*")
                if p.is_file()
            )
            if not files:
                raise ImportProblem("the archive contains no files")
            for rel in files:
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, target)
        return files
    except OSError as exc:
        raise ImportProblem(str(exc)) from exc
```

Notes for the implementer: `_temp_dir()` is whatever helper the module already uses to honor `TEMP_ROOT` (reuse it; if the existing code inlines the `dir=` argument, follow that pattern instead). `extract_archive` and its containment sweep are reused untouched.

- [ ] **Step 3: Full suite green, commit**

```bash
git add moddock/importer.py tests/test_importer.py
git commit -m "feat: tree-preserving ingest with wrapper stripping"
```

---

### Task 4: Store v2 — deploy lists, claim map, backup (`moddock/store.py`)

**Files:**
- Modify: `moddock/store.py` (rewrite), `moddock/importer.py` (remove now-dead `ingest`, `inspect_upload`, `_collect`, `scan_mod_files_for_single`, `scan_mod_files` if unused)
- Test: `tests/test_store.py` (rewrite), `tests/test_importer.py` (drop tests of removed functions; keep everything else green)

**Interfaces:**
- Consumes: `recipes.Recipe/DeployItem/apply_recipe/RecipeError`, `importer.ingest_tree`, `UEGameInfo.anchor_map()/install_dir`.
- Produces (Task 6 consumes exactly):
  - `ModStore(base)` as before.
  - `import_mod(appid, game: UEGameInfo, mod_name: str, source: Path, recipe: Recipe) -> dict`
  - `list_mods(appid, game | None) -> list[dict]` — `{"name", "state", "recipe_name"}`.
  - `set_enabled(appid, game, mod_name, enabled) -> None`
  - `delete_mod(appid, game | None, mod_name) -> None`
  - `StoreError`, `sanitize_mod_name` unchanged.

**Semantics (from spec §3–§5):**
- Manifest entry: `{"recipe", "recipe_name", "deploy": [{"src","dst","overwrite"}...], "source", "imported_at"}`. Legacy v1 entries (`"files"` list) are synthesized at read time into deploy items (`dst = <paks_rel>/~mods/<f>` from `game.anchor_map()`) when a game is available; without a game they list as `state="disabled"`, `recipe_name="legacy"`, and only repo-side delete works.
- import: name-dup check → `ingest_tree` into repo → `apply_recipe(recipe, tree, game.anchor_map())` → claim-map check across ALL entries' dst (naming the owner) → for refuse items, `game.install_dir/dst` must not already exist → manifest write. Any failure removes the repo dir.
- state: all dst present under `game.install_dir` → enabled; some → partial; none → disabled; game None → disabled.
- enable: verify all `src` present in repo (else StoreError naming the file); for each item: dst dir mkdir; if `overwrite=="backup"` and dst exists and no backup file exists yet → `shutil.move(dst, backup_path)` (backup_path = `base/"backup"/appid/dst`, parents created); then `shutil.copy2(repo/src, dst)` (refuse items overwrite freely — the claim map made the path ours).
- disable: per item — if a backup exists for dst: unlink dst (missing ok) then `shutil.move(backup, dst)` (restore); else unlink dst (missing ok).
- delete: if game present, run the disable recall per item; additionally unlink any backup files for its dsts that remain (game gone case); rmtree repo; drop the manifest entry (entry survives if the filesystem work raised). game None → repo + backups cleanup only.
- OSError → StoreError everywhere in the mutating paths.

- [ ] **Step 1: Rewrite `tests/test_store.py`** — port every existing behavior test to the new API and add the new ones. The complete file:

```python
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from moddock.adapters.unreal import MODS_DIR_NAME, UEGameInfo, detect_ue_game
from moddock.recipes import BUILTIN_RECIPES, recipe_from_dict
from moddock.store import ModStore, StoreError, sanitize_mod_name

PAK_RECIPE = BUILTIN_RECIPES[0]  # ue-paks-mods


def _game(tmp_path: Path) -> UEGameInfo:
    install = tmp_path / "game"
    (install / "Engine").mkdir(parents=True)
    paks = install / "SB" / "Content" / "Paks"
    paks.mkdir(parents=True)
    (paks / "SB-Windows.pak").touch()
    (install / "SB" / "Binaries" / "Win64").mkdir(parents=True)
    return detect_ue_game(install)


def _archive(tmp_path: Path, name: str = "ScarletHead.zip", stem: str = "scarlet") -> Path:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"{stem}.{ext}", "x")
    return archive


def _combo_recipe():
    return recipe_from_dict(
        {
            "name": "pak + lua",
            "rules": [
                {"match": ["*.pak", "*.utoc", "*.ucas"], "anchor": "paks_dir",
                 "subpath": "~mods"},
                {"match": ["*.lua"], "anchor": "win64_dir",
                 "subpath": "ue4ss/Mods", "mapping": "preserve_tree"},
            ],
        },
        recipe_id="combo",
    )


def test_sanitize_mod_name():
    assert sanitize_mod_name("Seamless Scarlet Head v2!") == "Seamless Scarlet Head v2"
    assert sanitize_mod_name("../evil") == "evil"
    assert sanitize_mod_name("...") == "mod"


def test_import_enable_disable_cycle(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    repo_pak = tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.pak"
    assert repo_pak.is_file()

    [mod] = store.list_mods("1", game)
    assert (mod["name"], mod["state"]) == ("Scarlet", "disabled")
    assert mod["recipe_name"] == "UE ~mods (pak)"

    store.set_enabled("1", game, "Scarlet", True)
    assert (game.mods_dir / "scarlet.pak").is_file()
    assert repo_pak.is_file()  # copy semantics: repo keeps the full copy
    assert store.list_mods("1", game)[0]["state"] == "enabled"

    store.set_enabled("1", game, "Scarlet", False)
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert repo_pak.is_file()
    assert store.list_mods("1", game)[0]["state"] == "disabled"


def test_multi_destination_recipe(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    archive = tmp_path / "combo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for ext in ("pak", "utoc", "ucas"):
            zf.writestr(f"scarlet.{ext}", "x")
        zf.writestr("MyMod/scripts/main.lua", "x")
    store.import_mod("1", game, "Combo", archive, _combo_recipe())
    store.set_enabled("1", game, "Combo", True)

    assert (game.mods_dir / "scarlet.pak").is_file()
    lua = (game.install_dir / "SB/Binaries/Win64/ue4ss/Mods"
           / "MyMod/scripts/main.lua")
    assert lua.is_file()

    store.set_enabled("1", game, "Combo", False)
    assert not lua.exists()
    # Shared directories are never deleted, only our files.
    assert lua.parent.parent.parent.is_dir()


def test_partial_state_detected_and_repaired_by_reenabling(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)
    (game.mods_dir / "scarlet.ucas").unlink()

    assert store.list_mods("1", game)[0]["state"] == "partial"
    store.set_enabled("1", game, "Scarlet", True)
    assert store.list_mods("1", game)[0]["state"] == "enabled"


def test_enable_with_missing_store_copy_raises(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    (tmp_path / "base" / "mods" / "1" / "Scarlet" / "scarlet.ucas").unlink()

    with pytest.raises(StoreError) as excinfo:
        store.set_enabled("1", game, "Scarlet", True)
    assert "scarlet.ucas" in str(excinfo.value)
    assert not (game.mods_dir / "scarlet.pak").exists()


def test_duplicate_mod_name_rejected(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    with pytest.raises(StoreError):
        store.import_mod("1", game, "Scarlet", _archive(tmp_path, "o.zip"),
                         PAK_RECIPE)


def test_claim_map_rejects_dst_collision_across_recipes(tmp_path):
    """Two mods may not deploy to the same destination path, even via
    different recipes."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet v1", _archive(tmp_path), PAK_RECIPE)

    with pytest.raises(StoreError) as excinfo:
        store.import_mod("1", game, "Scarlet v2",
                         _archive(tmp_path, "other.zip"), PAK_RECIPE)
    message = str(excinfo.value)
    assert "scarlet.pak" in message
    assert "Scarlet v1" in message
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet v2").exists()
    assert [m["name"] for m in store.list_mods("1", game)] == ["Scarlet v1"]

    store.import_mod("1", game, "Other",
                     _archive(tmp_path, "o.zip", stem="other"), PAK_RECIPE)
    assert len(store.list_mods("1", game)) == 2


def test_refuse_rule_rejects_unmanaged_file_at_import(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    game.mods_dir.mkdir(parents=True)
    (game.mods_dir / "scarlet.pak").write_bytes(b"hand-installed")

    with pytest.raises(StoreError) as exc:
        store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    assert "scarlet.pak" in str(exc.value)
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()
    assert (game.mods_dir / "scarlet.pak").read_bytes() == b"hand-installed"


def test_backup_rule_backs_up_and_restores(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    original = game.install_dir / "SB" / "original.dll"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"vanilla")

    recipe = recipe_from_dict(
        {"name": "replace", "rules": [
            {"match": ["*.dll"], "anchor": "game_root", "subpath": "SB",
             "mapping": "flatten", "overwrite": "backup"}]},
        recipe_id="rep",
    )
    archive = tmp_path / "r.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("original.dll", "modded")
    store.import_mod("1", game, "Replacer", archive, recipe)

    store.set_enabled("1", game, "Replacer", True)
    assert original.read_bytes() == b"modded"
    backup = tmp_path / "base" / "backup" / "1" / "SB" / "original.dll"
    assert backup.read_bytes() == b"vanilla"

    # Re-enabling must NOT re-backup (the true original is preserved).
    store.set_enabled("1", game, "Replacer", True)
    assert backup.read_bytes() == b"vanilla"

    store.set_enabled("1", game, "Replacer", False)
    assert original.read_bytes() == b"vanilla"
    assert not backup.exists()


def test_delete_restores_backup(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    original = game.install_dir / "SB" / "original.dll"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"vanilla")
    recipe = recipe_from_dict(
        {"name": "replace", "rules": [
            {"match": ["*.dll"], "anchor": "game_root", "subpath": "SB",
             "overwrite": "backup"}]},
        recipe_id="rep",
    )
    archive = tmp_path / "r.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("original.dll", "modded")
    store.import_mod("1", game, "Replacer", archive, recipe)
    store.set_enabled("1", game, "Replacer", True)

    store.delete_mod("1", game, "Replacer")
    assert original.read_bytes() == b"vanilla"
    assert store.list_mods("1", game) == []


def test_uninstall_then_reinstall_keeps_mods_disabled(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)

    shutil.rmtree(game.install_dir)
    assert store.list_mods("1", None)[0]["state"] == "disabled"

    reinstalled = _game(tmp_path)
    assert store.list_mods("1", reinstalled)[0]["state"] == "disabled"
    store.set_enabled("1", reinstalled, "Scarlet", True)
    assert store.list_mods("1", reinstalled)[0]["state"] == "enabled"


def test_delete_without_game_cleans_repository(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    shutil.rmtree(game.install_dir)

    store.delete_mod("1", None, "Scarlet")
    assert store.list_mods("1", None) == []
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


def test_set_enabled_wraps_os_error(tmp_path, monkeypatch):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)

    def boom(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("moddock.store.shutil.copy2", boom)
    with pytest.raises(StoreError):
        store.set_enabled("1", game, "Scarlet", True)


def test_delete_enabled_mod_removes_files_everywhere(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    store.set_enabled("1", game, "Scarlet", True)
    store.delete_mod("1", game, "Scarlet")
    assert store.list_mods("1", game) == []
    assert not (game.mods_dir / "scarlet.pak").exists()
    assert not (tmp_path / "base" / "mods" / "1" / "Scarlet").exists()


def test_manifest_written_with_deploy_list(tmp_path):
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    store.import_mod("1", game, "Scarlet", _archive(tmp_path), PAK_RECIPE)
    manifest = json.loads((tmp_path / "base" / "manifest" / "1.json").read_text())
    entry = manifest["mods"]["Scarlet"]
    assert entry["recipe"] == "ue-paks-mods"
    assert sorted(i["dst"] for i in entry["deploy"]) == [
        "SB/Content/Paks/~mods/scarlet.pak",
        "SB/Content/Paks/~mods/scarlet.ucas",
        "SB/Content/Paks/~mods/scarlet.utoc",
    ]


def test_legacy_v1_manifest_still_works(tmp_path):
    """A pre-recipe manifest (flat "files" list) keeps functioning."""
    store = ModStore(tmp_path / "base")
    game = _game(tmp_path)
    repo = tmp_path / "base" / "mods" / "1" / "Old"
    repo.mkdir(parents=True)
    for ext in ("pak", "utoc", "ucas"):
        (repo / f"old.{ext}").write_bytes(b"x")
    (tmp_path / "base" / "manifest").mkdir(parents=True)
    (tmp_path / "base" / "manifest" / "1.json").write_text(json.dumps({
        "mods": {"Old": {
            "files": ["old.pak", "old.utoc", "old.ucas"],
            "source": "old.zip", "imported_at": "2026-09-01T00:00:00+00:00",
            "repo": str(repo),
        }}
    }))

    [mod] = store.list_mods("1", game)
    assert mod["state"] == "disabled"
    store.set_enabled("1", game, "Old", True)
    assert (game.mods_dir / "old.pak").is_file()
    store.set_enabled("1", game, "Old", False)
    store.delete_mod("1", game, "Old")
    assert store.list_mods("1", game) == []
```

- [ ] **Step 2: RED, then rewrite `moddock/store.py`.** Keep the module docstring's copy-model story (update it to mention recipes/deploy lists/backups). Implementation skeleton — follow it closely:

```python
from .recipes import Recipe, RecipeError, apply_recipe
from .importer import ImportProblem, ingest_tree

class ModStore:
    def __init__(self, base):
        self.base = base
        self.manifest_dir = base / "manifest"
        self.backup_dir = base / "backup"

    # manifest load/save: unchanged from v1.1

    def _mod_repo_dir(self, appid, mod_name):  # unchanged
    def _backup_path(self, appid, dst):
        return self.backup_dir / appid / dst

    @staticmethod
    def _entry_deploy(entry, game):
        """Deploy items for an entry; synthesizes them for legacy v1 entries."""
        if "deploy" in entry:
            return entry["deploy"]
        if game is None or game.install_dir is None:
            return None  # legacy entry, unusable without a detected game
        paks_rel = game.anchor_map()["paks_dir"]
        return [
            {"src": f, "dst": f"{paks_rel}/{MODS_DIR_NAME}/{f}",
             "overwrite": "refuse"}
            for f in entry["files"]
        ]

    def _claimed(self, manifest, game):
        claimed = {}
        for name, entry in sorted(manifest["mods"].items()):
            for item in self._entry_deploy(entry, game) or []:
                claimed[item["dst"]] = name
        return claimed

    def import_mod(self, appid, game, mod_name, source, recipe):
        mod_name = sanitize_mod_name(mod_name)
        manifest = self._load_manifest(appid)
        if mod_name in manifest["mods"]:
            raise StoreError(f'a mod named "{mod_name}" already exists')
        repo = self._mod_repo_dir(appid, mod_name)
        try:
            tree = ingest_tree(source, repo)
        except ImportProblem as exc:
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(str(exc)) from exc
        try:
            items = apply_recipe(recipe, tree, game.anchor_map())
        except RecipeError as exc:
            shutil.rmtree(repo, ignore_errors=True)
            raise StoreError(str(exc)) from exc
        claimed = self._claimed(manifest, game)
        for item in items:
            owner = claimed.get(item.dst)
            if owner is not None:
                shutil.rmtree(repo, ignore_errors=True)
                raise StoreError(
                    f'"{item.dst}" is already used by mod "{owner}"'
                )
            if item.overwrite == "refuse" and (game.install_dir / item.dst).exists():
                shutil.rmtree(repo, ignore_errors=True)
                raise StoreError(
                    f'"{item.dst}" already exists and is not managed by '
                    "ModDock — remove it by hand, or use an install method "
                    "with backup enabled"
                )
        manifest["mods"][mod_name] = {
            "recipe": recipe.id,
            "recipe_name": recipe.name,
            "deploy": [
                {"src": i.src, "dst": i.dst, "overwrite": i.overwrite}
                for i in items
            ],
            "source": source.name,
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_manifest(appid, manifest)
        return {"name": mod_name, "state": "disabled"}

    def list_mods(self, appid, game):
        # per entry: deploy = self._entry_deploy(entry, game)
        # state: deploy None or game None -> "disabled";
        # else count (game.install_dir / item["dst"]).is_file()
        # recipe_name: entry.get("recipe_name", "legacy")

    def set_enabled(self, appid, game, mod_name, enabled):
        # entry lookup; deploy = self._entry_deploy(entry, game)
        # deploy None -> StoreError("game is not installed")
        # repo = self._mod_repo_dir(appid, mod_name)
        # enable: first verify every repo/src is_file (StoreError naming it),
        #   then per item: dst_abs = game.install_dir / dst; mkdir parent;
        #   if overwrite=="backup" and dst_abs.exists() and not backup.exists():
        #       backup.parent.mkdir(parents=True, exist_ok=True)
        #       shutil.move(dst_abs, backup)
        #   shutil.copy2(repo / src, dst_abs)
        # disable: per item: backup = self._backup_path(appid, dst)
        #   if backup.is_file():
        #       (game.install_dir / dst).unlink(missing_ok=True)
        #       (game.install_dir / dst).parent.mkdir(parents=True, exist_ok=True)
        #       shutil.move(backup, game.install_dir / dst)
        #   else: (game.install_dir / dst).unlink(missing_ok=True)
        # wrap the whole mutating body: except OSError -> StoreError

    def delete_mod(self, appid, game, mod_name):
        # entry lookup; deploy list:
        #   with game: same recall as disable (restore-or-unlink per item)
        #   without game (or legacy without game): repo + leftover backups only
        # then: unlink any remaining self._backup_path(appid, dst) for its
        # items (covers the game-uninstalled case), rmtree repo,
        # drop entry, save. OSError -> StoreError (entry kept on failure).
```

The skeleton's commented sections must be written out in full — they are the specification of behavior, and every branch is covered by a Step 1 test.

Then remove from `moddock/importer.py`: `ingest`, `inspect_upload`, `_collect`, `scan_mod_files_for_single`, and `scan_mod_files` if nothing references it anymore; drop their tests from `tests/test_importer.py` (keep `extract_archive`/containment/`pak_set_errors`/`ingest_tree` coverage).

- [ ] **Step 3: Full suite green** (`tests/test_main.py` will now fail — that is Task 6's job; run `./venv/bin/python -m pytest tests -v --ignore=tests/test_main.py` for this task's gate and say so in the report), **commit**

```bash
git add moddock/store.py moddock/importer.py tests/test_store.py tests/test_importer.py
git commit -m "feat: recipe-driven store with claim map and backup/restore"
```

---

### Task 5: Uploader — recipes payload, creation endpoint, page v2 (`moddock/uploader.py`)

**Files:**
- Modify: `moddock/uploader.py`
- Test: `tests/test_uploader.py`

**Interfaces:**
- `UploadServer` ctor gains `recipes_provider: Callable[[], Awaitable[list[dict]]] | None = None` and `recipe_creator: Callable[[dict], Awaitable[dict]] | None = None` (positioned AFTER existing params, before `host`).
- `installer` signature becomes `(path: Path, appid: str, recipe_id: str) -> Awaitable[tuple[bool, str]]`.
- `GET /u/{token}/games` returns `{"games": [{"appid","name","anchors": [names...]}], "recipes": [{"id","name","builtin"}]}` (games payload shape now provided by main's provider; uploader passes it through and adds recipes from `recipes_provider`).
- `POST /u/{token}/recipes` — JSON body → `recipe_creator(body)`; returns its dict on 200; a `ValueError` from the creator becomes `{"error": str}` with status 400.
- `POST /u/{token}` requires BOTH `appid` and `recipe` fields before the files; a file arriving without either fails with `"no game selected"` / `"no install method selected"`.

- [ ] **Step 1: Update/extend `tests/test_uploader.py`:**
  - Fixture: add `recipes_provider` (returns `[{"id": "ue-paks-mods", "name": "UE ~mods (pak)", "builtin": True}]`) and a `recipe_creator` spy (records body; returns `{"id": "custom-ab", "name": body["name"], "builtin": False}`; raises `ValueError("bad recipe")` when `body.get("name") == "bad"`). `InstallerSpy.__call__` gains `recipe_id` and records it.
  - `_form()` gains `recipe: str | None = "ue-paks-mods"` adding a `recipe` field.
  - Update `test_games_endpoint`: response now `{"games": [...], "recipes": [...]}`.
  - New `test_upload_without_recipe_fails`: form without recipe → failed entry `"no install method selected"`, installer not called.
  - Update `test_upload_installs_file`: assert installer received `recipe_id == "ue-paks-mods"`.
  - New `test_create_recipe_endpoint`: POST JSON `{"name": "My", "rules": [...]}` to `/u/testtoken/recipes` → 200 + creator's dict; POST `{"name": "bad"}` → 400 with `{"error": "bad recipe"}`; wrong token → 404.
  - All other existing tests updated mechanically for the new form field.

- [ ] **Step 2: RED, then implement:**
  - `_games` handler: `games = await self.games_provider() ...; recipes = await self.recipes_provider() if self.recipes_provider else []` → `{"games": games, "recipes": recipes}`.
  - `_create_recipe` handler: token check; `body = await request.json()` (malformed JSON → 400 `{"error": "invalid JSON"}`); creator None → 400; `try: result = await self.recipe_creator(body); except ValueError as exc: 400 {"error": str(exc)}`; else `web.json_response(result)`.
  - `_upload`: track `recipe_id` like `appid` (multipart field named `recipe`); per file require both, with the two distinct failure reasons; pass `(target, appid, recipe_id)` to the installer.
  - Route: `app.router.add_post("/u/{token}/recipes", self._create_recipe)`.
  - **Page rewrite** (`UPLOAD_PAGE`): second `<select id="r">` labeled "Install method"; populate from `j.recipes` plus a final option `+ New install method…` (`value="__new"`); per-game memory `localStorage['moddock_recipe_' + appid]` (validated against existing options; fallback `ue-paks-mods`); selecting `__new` reveals an inline form `<div id="newform">`: name input; a rules container where `addRule()` appends a row (match patterns text input with placeholder `*.pak, *.utoc, *.ucas`; anchor `<select>` game_root/paks_dir/win64_dir; subpath text; mapping `<select>` flatten/preserve_tree; overwrite `<select>` refuse/backup; remove-row button), one row added initially; a leftover `<select>` ignore/fail; Save button POSTs `{name, rules:[{match: split(','), anchor, subpath, mapping, overwrite}], leftover}` to `location.pathname+'/recipes'` — on 200 re-fetch `loadGames()`, select the new id, hide the form; on error show the message in `#hint`. `up()` requires `sel.value` AND a concrete recipe (`__new` blocks with a hint), stores both localStorage keys, and appends `fd.append('recipe', recipeSel.value)`. All DOM built via `createElement`/`textContent` (no innerHTML with data). Keep the existing progress-bar machinery untouched.

- [ ] **Step 3: Full suite green except `tests/test_main.py` (Task 6), commit**

```bash
git add moddock/uploader.py tests/test_uploader.py
git commit -m "feat: recipe selection and creation on the upload page"
```

---

### Task 6: main.py wiring + recipe callables

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py` (update + extend)

**Interfaces (frontend consumes in Task 7):**
- New callables: `list_recipes() -> list[dict]` (`{"id","name","builtin","rules": <count>}`), `delete_recipe(recipe_id) -> {"ok","error"}`.
- `_install_upload(path, appid, recipe_id) -> tuple[bool, str]` (installer for the uploader).
- `_upload_games()` items gain `"anchors": [names of non-None anchors]`.
- `_recipes_payload() -> list[dict]` (id/name/builtin) and `_create_recipe(body: dict) -> dict` (RecipeError → `raise ValueError(str(exc))` so the uploader 400s it) wired into `UploadServer(recipes_provider=..., recipe_creator=...)`.
- `Plugin.recipes: RecipeStore` created in `_main` at `BASE_DIR / "recipes.json"`.

- [ ] **Step 1: Update `tests/test_main.py`:**
  - `_wired_plugin` additionally sets `plugin.recipes = RecipeStore(tmp_path / "recipes.json")`.
  - `_ue_tree` also creates `SB/Binaries/Win64` (so win64 anchor exists).
  - Roll-call: add `list_recipes`, `delete_recipe`.
  - `_install_upload` call sites gain the recipe id `"ue-paks-mods"`.
  - New tests:
    - `test_list_and_delete_recipes`: builtins listed with `builtin: True`; creating via `plugin._create_recipe({...})` returns a dict with an id; `delete_recipe` on it → ok; `delete_recipe("ue-paks-mods")` → `{"ok": False, ...}`.
    - `test_create_recipe_rejects_invalid`: `plugin._create_recipe({"name": ""})` raises `ValueError`.
    - `test_install_upload_with_unknown_recipe_fails`: returns `(False, ...)` mentioning install method.
    - `test_install_upload_multi_destination`: use the combo recipe (create via `plugin._create_recipe`), upload a zip holding a pak set + `MyMod/scripts/main.lua`, assert lua landed under `SB/Binaries/Win64/ue4ss/Mods/MyMod/scripts/main.lua` and state is enabled.
    - `test_http_upload_installs_end_to_end`: extend the existing test — the games payload now includes `"recipes"` and each game has `"anchors"`; the form adds `recipe: "ue-paks-mods"`.

- [ ] **Step 2: Implement in `main.py`:**
  - `from moddock.recipes import RecipeStore, RecipeError, recipe_to_dict` (as needed).
  - `_main`: `self.recipes = RecipeStore(BASE_DIR / "recipes.json")`.
  - `_upload_games`: for detected games include `"anchors": [k for k, v in info.anchor_map().items() if v is not None]`.
  - `_recipes_payload`: `[{"id": r.id, "name": r.name, "builtin": r.builtin} for r in self.recipes.list()]` (async).
  - `_create_recipe(body)`: `try: recipe = self.recipes.create(body); except RecipeError as exc: raise ValueError(str(exc));` return `{"id": recipe.id, "name": recipe.name, "builtin": False}` (async).
  - `list_recipes` callable: id/name/builtin/rules-count. `delete_recipe`: RecipeError → `{"ok": False, "error": ...}`.
  - `_install_upload(path, appid, recipe_id)`: resolve recipe via `self.recipes.get`; None → `(False, "unknown install method — refresh the page")`; pass `recipe=recipe` into `store.import_mod` via `_off_loop`; enable as before.
  - `_start_uploader`: pass `recipes_provider=self._recipes_payload, recipe_creator=self._create_recipe`.

- [ ] **Step 3: FULL suite green (all files), commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: wire recipes through the Decky entry point"
```

---

### Task 7: Frontend — install methods in Settings

**Files:**
- Modify: `src/api.ts`, `src/views/SettingsView.tsx`
- Verify: `pnpm build` + `npx tsc --noEmit`

- [ ] **Step 1:** `src/api.ts` additions:

```ts
export interface RecipeSummary {
  id: string;
  name: string;
  builtin: boolean;
  rules: number;
}

export const listRecipes = callable<[], RecipeSummary[]>("list_recipes");
export const deleteRecipe =
  callable<[recipe_id: string], OpResult>("delete_recipe");
```

- [ ] **Step 2:** `SettingsView.tsx`: add an "Install methods" `PanelSection` below the upload section: on mount `listRecipes().then(...).catch(...)` into state; each row shows `name` with description `builtin ? "built-in" : \`custom · ${rules} rule(s)\``; custom rows get a Delete `ButtonItem` (single tap; on result error route into the existing error display; refresh the list after). A short hint row: "New methods are created on the upload page."

- [ ] **Step 3:** `pnpm build` + `npx tsc --noEmit` pass, commit:

```bash
git add src
git commit -m "feat: list and delete install methods from the panel"
```

---

### Task 8: Docs

**Files:**
- Modify: `README.md`, `docs/testing-checklist.md`

- [ ] **Step 1:** README: feature bullet for install methods ("pick how a mod installs — built-in methods for UE paks, LogicMods, root merges and EXE-dir drops, or define your own multi-rule method right on the upload page; refuse-by-default overwrite protection with opt-in backup/restore of replaced game files"); "How it works" step 3 mentions choosing the install method; architecture block gains `recipes.py`; roadmap drops the shipped items.
- [ ] **Step 2:** testing-checklist: extend item 5 (pick game AND method), add items: create a custom method on the phone (e.g. lua → `win64_dir/ue4ss/Mods`) and upload a combo zip verifying both destinations; verify a `backup` method restores the original on disable.
- [ ] **Step 3:** Full local verification (pytest, build, `bash -n` both scripts), commit:

```bash
git add README.md docs/testing-checklist.md
git commit -m "docs: install methods in README and on-device checklist"
```

---

## Self-Review Notes

- Spec coverage: §2 model/builtins → T1; anchors → T2; §5 pipeline (tree ingest, wrapper strip) → T3; §3–§4 storage/claim/backup/migration → T4; §6 page/endpoints → T5 + T6; panel → T7; §8 tests distributed per task; docs → T8.
- Suite-green exceptions are explicit: T4 and T5 gate on the suite minus `tests/test_main.py`, which T6 restores — the plan says so in those tasks.
- Type consistency: `installer(path, appid, recipe_id)` (T5) matches `_install_upload` (T6); `recipes_provider`/`recipe_creator` names match; `anchor_map()` (T2) is what T1's `apply_recipe` consumes and T4/T6 pass through; `RecipeStore` API used in T6 matches T1.
- Deliberate simplifications: legacy migration is read-time synthesis (no manifest rewrite); the upload page keeps recipes creation minimal (no edit — delete and recreate).
