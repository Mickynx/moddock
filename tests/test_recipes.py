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


def test_store_survives_well_formed_json_of_the_wrong_shape(tmp_path):
    # Valid JSON that is not the expected object still counts as corrupt: the
    # store is built at plugin start, so it must never raise.
    for payload in ("[]", "null", '"nope"', '{"recipes": 7}'):
        path = tmp_path / "recipes.json"
        path.write_text(payload, encoding="utf-8")
        assert [r.id for r in RecipeStore(path).list()] == [
            b.id for b in BUILTIN_RECIPES
        ]
