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
            except (ValueError, OSError, RecipeError, AttributeError, TypeError):
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
