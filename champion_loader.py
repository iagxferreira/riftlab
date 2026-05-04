"""
champion_loader.py — Loads champion data from JSON files in data/.

Provides:
  get_comp_data()       → full CHAMPS dict (all champions)
  get_aliases()         → name alias dict
  get_champion(name)    → resolves alias + returns champion JSON data
  get_rune_pages(name)  → rune pages list for champion
  get_build_items(name) → build items list
  get_bans(name)        → bans dict
  get_champ_select(name)→ champ_select block
  eval_build_condition(conditions, threats) → bool
  eval_champ_condition(condition_name, ally_comp, enemy_comp, ctx) → bool
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
_CHAMPS_DIR = _DATA_DIR / "champions"

# ---------------------------------------------------------------------------
# Load static data at module level (once)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


_comp_data:   dict = _load_json(_DATA_DIR / "comp.json")
_aliases:     dict = _load_json(_DATA_DIR / "aliases.json")
_champ_pool:  dict = {}  # canonical_name → full champion JSON

def _load_pool():
    global _champ_pool
    if _champ_pool:
        return
    for path in _CHAMPS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            _champ_pool[data["name"]] = data
            for alias in data.get("aliases", []):
                _aliases[alias.lower()] = data["name"]
        except Exception:
            pass

_load_pool()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_comp_data() -> dict:
    return _comp_data


def get_aliases() -> dict:
    return _aliases


def resolve_name(name: str) -> str | None:
    """Return canonical champion name from any spelling/alias, or None."""
    lower = name.lower().strip()
    # direct alias lookup
    if lower in _aliases:
        canonical = _aliases[lower]
        if canonical in _comp_data:
            return canonical
        if canonical in _champ_pool:
            return canonical
    # case-insensitive match in comp data
    for key in _comp_data:
        if key.lower() == lower:
            return key
    # case-insensitive match in pool
    for key in _champ_pool:
        if key.lower() == lower:
            return key
    return None


def get_champion(name: str) -> dict | None:
    """Return full champion JSON data for a playable-pool champion."""
    canonical = resolve_name(name)
    return _champ_pool.get(canonical) if canonical else None


def get_rune_pages(name: str) -> list:
    champ = get_champion(name)
    return champ.get("rune_pages", []) if champ else []


def get_build_items(name: str) -> list:
    champ = get_champion(name)
    return champ.get("build_items", []) if champ else []


def get_bans(name: str) -> dict:
    champ = get_champion(name)
    return champ.get("bans", {}) if champ else {}


def get_champ_select(name: str) -> dict:
    champ = get_champion(name)
    return champ.get("champ_select", {}) if champ else {}


def get_pool_names() -> list[str]:
    """Return all champion names in the playable pool."""
    return list(_champ_pool.keys())


# ---------------------------------------------------------------------------
# Build condition evaluator
# ---------------------------------------------------------------------------

def eval_build_condition(conditions: dict, threats: dict) -> bool:
    """
    Evaluate a condition dict against a threats dict.

    Supported formats:
      {}                                 → always true
      {"field": {"op": value}}           → field op value (lt/lte/gt/gte/eq/ne)
      {"any": [cond1, cond2, ...]}       → any sub-condition is true
      Multiple keys at top level         → all must be true (AND)
    """
    if not conditions:
        return True

    _OPS = {
        "lt":  lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "gt":  lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "eq":  lambda a, b: a == b,
        "ne":  lambda a, b: a != b,
    }

    for key, val in conditions.items():
        if key == "any":
            # OR: at least one sub-condition must be true
            if not any(eval_build_condition(sub, threats) for sub in val):
                return False
        elif isinstance(val, dict):
            field_val = threats.get(key, 0)
            for op, threshold in val.items():
                fn = _OPS.get(op)
                if fn and not fn(field_val, threshold):
                    return False
        else:
            # boolean flag
            if not threats.get(key) == val:
                return False

    return True


# ---------------------------------------------------------------------------
# Champ select condition evaluator
# ---------------------------------------------------------------------------

def _build_condition_fns(ally_comp: dict, enemy_comp: dict, ctx: dict) -> dict:
    """Map condition name → bool for champ_select scoring."""
    enemy_names = [n for n, _ in enemy_comp.get("resolved", [])]
    ally_names  = [n for n, _ in ally_comp.get("resolved", [])]

    return {
        # Context-based
        "fighting_adc":           ctx.get("fighting_adc", False),
        "peel_adc":               ctx.get("peel_adc", False),
        "no_peel_adc":            not ctx.get("peel_adc", False),
        "has_assassin":           ctx.get("has_assassin", False),
        "ally_ap_carry":          ctx.get("ally_ap_carry", False),
        "heavy_cc_enemy":         ctx.get("heavy_cc_enemy", False),
        # Ally-based
        "engage_or_teamfight_ally":  ally_comp.get("primary_win_con") in ("engage", "teamfight"),
        "pick_or_engage_ally":       ally_comp.get("primary_win_con") in ("pick", "engage"),
        "ally_poke_or_scale":        ally_comp.get("primary_win_con") in ("scale", "poke"),
        "no_frontline_ally":         len(ally_comp.get("frontline", [])) == 0,
        "ally_has_frontline":        len(ally_comp.get("frontline", [])) >= 2,
        "magic_heavy_ally":          ally_comp.get("magic_dmg", 0) >= 3,
        # Enemy-based
        "tankline_enemy_2plus":      len(enemy_comp.get("frontline", [])) >= 2,
        "no_assassins_enemy":        len(enemy_comp.get("assassins", [])) == 0,
        "two_assassins_enemy":       len(enemy_comp.get("assassins", [])) >= 2,
        "poke_wincon_enemy":         enemy_comp.get("primary_win_con") == "poke",
        "no_poke_wincon_enemy":      enemy_comp.get("primary_win_con") != "poke",
        "enemy_teamfight_or_scale":  enemy_comp.get("primary_win_con") in ("teamfight", "scale"),
        "low_magic_dmg_enemy":       enemy_comp.get("magic_dmg", 0) <= 1,
        "immobile_enemy_2plus":      len([c for _, c in enemy_comp.get("resolved", []) if c.get("mobility") == "low"]) >= 2,
        # Specific champion checks
        "no_morgana_enemy":          "Morgana" not in enemy_names,
        "morgana_enemy":             "Morgana" in enemy_names,
        "nautilus_enemy":            "Nautilus" in enemy_names,
        "leona_enemy":               "Leona" in enemy_names,
        # Compound
        "peel_adc_no_fighting":      ctx.get("peel_adc", False) and not ctx.get("fighting_adc", False),
        "fighting_adc_no_peel":      ctx.get("fighting_adc", False) and not ctx.get("peel_adc", False),
    }


def score_champ_select(champ_name: str, ally_comp: dict, enemy_comp: dict, ctx: dict) -> float:
    """Score a champion for champ select based on JSON favor/against conditions."""
    data = get_champ_select(champ_name)
    if not data:
        return 0.0

    cond_map = _build_condition_fns(ally_comp, enemy_comp, ctx)

    favor   = sum(cond_map.get(c, False) for c in data.get("favor", []))
    against = sum(cond_map.get(c, False) for c in data.get("against", []))
    return favor - against * 1.5
