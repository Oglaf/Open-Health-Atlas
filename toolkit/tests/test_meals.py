"""Hand-entered meals: validated meal-save / meal-delete (Nutrition > Meals tab).

Fictional data only.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text(encoding="utf-8")

MEAL = {
    "name": "Fictional oat bowl", "meal_type": "breakfast",
    "grams_per_portion": 300, "portions": 2,
    "per_portion": {"kcal": 450, "protein_g": 20, "carbs_g": 60, "fat_g": 12,
                    "fiber_g": 9, "iron_mg": 3.5},
}


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "health.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    vault = tmp_path / "vault"
    vault.mkdir()
    return {"db": db, "vault": vault}


def run(env, *args, stdin=None, expect_ok=True):
    r = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        env={**os.environ, "HEALTH_DB": str(env["db"]), "HEALTH_VAULT": str(env["vault"])},
        input=stdin, capture_output=True, text=True, timeout=30)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def save(env, payload, **kw):
    return run(env, "meal-save", stdin=json.dumps(payload), **kw)


def rows(env, sql, params=()):
    con = sqlite3.connect(env["db"])
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def nutrients(env, rid):
    return {r["nutrient"]: (r["unit"], r["per_gram"]) for r in rows(
        env, "SELECT nutrient, unit, per_gram FROM recipe_nutrients WHERE recipe_id=?", (rid,))}


def test_create_stores_batch_totals_and_metadata(env):
    out = json.loads(save(env, MEAL).stdout)
    assert out["created"] is True and out["recipe_id"] == "fictional-oat-bowl"
    recipe = rows(env, "SELECT * FROM recipes")[0]
    assert (recipe["name"], recipe["meal_type"], recipe["source"]) == ("Fictional oat bowl", "breakfast", "manual")
    assert (recipe["batch_grams"], recipe["grams_per_portion"], recipe["portions"]) == (600, 300, 2)
    stored = nutrients(env, "fictional-oat-bowl")
    # per-portion x portions, in the units the importer/readers expect
    assert stored["Energy"] == ("kcal", 900) and stored["Protein"] == ("g", 40)
    assert stored["Fiber"] == ("g", 18) and stored["Iron"] == ("mg", 7)
    assert "Zinc" not in stored


def test_update_rewrites_supplied_nutrients_and_rescales_the_rest(env):
    save(env, MEAL)
    changed = {**MEAL, "recipe_id": "fictional-oat-bowl", "name": "Fictional oat bowl v2",
               "portions": 4, "per_portion": {"kcal": 500, "protein_g": 25, "carbs_g": 55, "fat_g": 15}}
    out = json.loads(save(env, changed).stdout)
    assert out["created"] is False
    recipe = rows(env, "SELECT * FROM recipes")[0]
    assert (recipe["name"], recipe["batch_grams"], recipe["portions"]) == ("Fictional oat bowl v2", 1200, 4)
    stored = nutrients(env, "fictional-oat-bowl")
    assert stored["Energy"][1] == 2000 and stored["Protein"][1] == 100
    # Fiber/Iron were not supplied: kept, scaled from the 600 g batch to 1200 g
    assert stored["Fiber"][1] == 36 and stored["Iron"][1] == 14


def test_update_needs_no_nutrients_and_keeps_stored_precision(env):
    save(env, MEAL)
    con = sqlite3.connect(env["db"])
    con.execute("UPDATE recipe_nutrients SET per_gram=901.37 WHERE nutrient='Energy'")
    con.commit()
    con.close()
    out = json.loads(save(env, {**MEAL, "recipe_id": "fictional-oat-bowl", "name": "Renamed",
                                "per_portion": {}}).stdout)
    assert out["created"] is False
    assert rows(env, "SELECT name FROM recipes")[0]["name"] == "Renamed"
    assert nutrients(env, "fictional-oat-bowl")["Energy"][1] == 901.37


def test_update_without_meal_type_key_keeps_it(env):
    save(env, MEAL)
    changed = {k: v for k, v in MEAL.items() if k != "meal_type"}
    changed["recipe_id"] = "fictional-oat-bowl"
    save(env, changed)
    assert rows(env, "SELECT meal_type FROM recipes")[0]["meal_type"] == "breakfast"
    save(env, {**changed, "meal_type": None})
    assert rows(env, "SELECT meal_type FROM recipes")[0]["meal_type"] is None


@pytest.mark.parametrize("mutate, message", [
    (lambda m: m["per_portion"].pop("kcal"), "per_portion requires: kcal"),
    (lambda m: m.update(meal_type="brunch"), "meal_type must be one of"),
    (lambda m: m.update(grams_per_portion=0), "grams_per_portion must be between"),
    (lambda m: m.update(portions=1.5), "portions must be a whole number"),
    (lambda m: m.update(name="  "), "name must be 1-120 characters"),
    (lambda m: m.update(surprise=1), "unsupported meal fields"),
    (lambda m: m["per_portion"].update(sodium_mg=5), "unsupported per_portion fields"),
    (lambda m: m["per_portion"].update(kcal=-1), "kcal must be between"),
    (lambda m: m.update(recipe_id="missing"), "meal not found"),
])
def test_validation_refuses_bad_input(env, mutate, message):
    payload = json.loads(json.dumps(MEAL))
    mutate(payload)
    r = save(env, payload, expect_ok=False)
    assert r.returncode != 0 and message in (r.stderr + r.stdout)
    assert rows(env, "SELECT * FROM recipes") == []


def test_duplicate_name_is_refused_case_insensitively(env):
    save(env, MEAL)
    r = save(env, {**MEAL, "name": "FICTIONAL OAT BOWL"}, expect_ok=False)
    assert "already exists" in (r.stderr + r.stdout)


def test_ingredient_lines_write_replace_and_remove_the_sidecar(env):
    save(env, {**MEAL, "ingredients": ["60 g oats", "250 ml milk"]})
    sidecar = env["vault"] / "personal" / "recipes" / "fictional-oat-bowl.ingredients.json"
    doc = json.loads(sidecar.read_text(encoding="utf-8"))
    assert [i["name"] for i in doc["ingredients"]] == ["60 g oats", "250 ml milk"]
    save(env, {**MEAL, "recipe_id": "fictional-oat-bowl", "ingredients": []})
    assert not sidecar.exists()


def test_delete_removes_meal_data_but_keeps_logged_history(env):
    save(env, {**MEAL, "ingredients": ["60 g oats"]})
    con = sqlite3.connect(env["db"])
    con.execute("INSERT INTO meal_inventory(recipe_id, portions_remaining) VALUES('fictional-oat-bowl', 3)")
    con.execute("INSERT INTO nutrition_log(date, recipe_id, food_name, grams, kcal)"
                " VALUES('2026-01-01', 'fictional-oat-bowl', 'Fictional oat bowl', 300, 450)")
    con.commit()
    con.close()
    out = json.loads(run(env, "meal-delete", "fictional-oat-bowl").stdout)
    assert out["deleted"] == "Fictional oat bowl" and out["inventory_rows_removed"] == 1
    assert rows(env, "SELECT * FROM recipes") == []
    assert rows(env, "SELECT * FROM recipe_nutrients") == []
    assert rows(env, "SELECT * FROM meal_inventory") == []
    assert len(rows(env, "SELECT * FROM nutrition_log")) == 1
    assert not (env["vault"] / "personal" / "recipes" / "fictional-oat-bowl.ingredients.json").exists()


def test_delete_unknown_meal_is_refused(env):
    r = run(env, "meal-delete", "nope", expect_ok=False)
    assert r.returncode != 0 and "meal not found" in (r.stderr + r.stdout)
