"""
build_advisor.py — Adaptive build recommendations based on live game + enemy items.

Usage:
  # Champion select — predict enemy builds from champion + runes
  python build_advisor.py --live main

  # Mid-game — feed in actual enemy items you can see
  python build_advisor.py --live main --enemy-items "Caitlyn:Kraken+BT,Nautilus:Sunfire+Thornmail"

  # Manual comp (no API needed)
  python build_advisor.py Rakan --ally "Sett,Ambessa,Fizz,Jinx" --enemy "Volibear,Udyr,Lissandra,Caitlyn,Nautilus"
"""

import sys
import argparse
import requests

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from lol_stats import get_account, BASE_SUMMONER, _get, ACCOUNTS, console
from comp_check import resolve, CHAMPS, champ_name_from_id, _load_champ_id_map

load_dotenv()

# ---------------------------------------------------------------------------
# Rune keystone → build tendencies
# ---------------------------------------------------------------------------

KEYSTONES: dict[int, dict] = {
    8008: dict(name="Lethal Tempo",    healing=False, burst=False, sustain=True,  on_hit=True,  tank=False, poke=False),
    8005: dict(name="Press the Attack",healing=False, burst=True,  sustain=False, on_hit=False, tank=False, poke=False),
    8021: dict(name="Fleet Footwork",  healing=True,  burst=False, sustain=True,  on_hit=False, tank=False, poke=False),
    8010: dict(name="Conqueror",       healing=True,  burst=False, sustain=True,  on_hit=False, tank=False, poke=False),
    8112: dict(name="Electrocute",     healing=False, burst=True,  sustain=False, on_hit=False, tank=False, poke=False),
    8128: dict(name="Dark Harvest",    healing=False, burst=True,  sustain=False, on_hit=False, tank=False, poke=False),
    8351: dict(name="Glacial Augment", healing=False, burst=False, sustain=False, on_hit=False, tank=True,  poke=False),
    8369: dict(name="First Strike",    healing=False, burst=False, sustain=False, on_hit=False, tank=False, poke=True),
    9923: dict(name="Hail of Blades",  healing=False, burst=True,  sustain=False, on_hit=True,  tank=False, poke=False),
    8214: dict(name="Summon Aery",     healing=False, burst=False, sustain=False, on_hit=False, tank=False, poke=True),
    8229: dict(name="Arcane Comet",    healing=False, burst=False, sustain=False, on_hit=False, tank=False, poke=True),
    8230: dict(name="Phase Rush",      healing=False, burst=False, sustain=True,  on_hit=False, tank=False, poke=False),
    8437: dict(name="Grasp",           healing=True,  burst=False, sustain=True,  on_hit=False, tank=True,  poke=False),
    8465: dict(name="Guardian",        healing=False, burst=False, sustain=False, on_hit=False, tank=True,  poke=False),
    8439: dict(name="Aftershock",      healing=False, burst=False, sustain=False, on_hit=False, tank=True,  poke=False),
    8299: dict(name="Legend: Alacrity",healing=False, burst=False, sustain=False, on_hit=True,  tank=False, poke=False),
    8462: dict(name="Lethal Tempo",    healing=False, burst=False, sustain=True,  on_hit=True,  tank=False, poke=False),
}

# Champions with innate healing regardless of runes
HEALING_CHAMPS = {
    "Vladimir", "DrMundo", "Aatrox", "Warwick", "Soraka", "Yuumi", "Sona",
    "Nami", "Sylas", "Olaf", "Trundle", "Illaoi", "Swain", "Gragas",
    "Darius", "Sett", "Tahm Kench", "TahmKench", "Nilah", "Samira",
    "Renekton", "Garen", "Fiora", "Irelia", "Jax",
}

# Champions that build shields
SHIELD_CHAMPS = {
    "Janna", "Lulu", "Karma", "Seraphine", "Orianna", "Sona",
    "Thresh", "Blitzcrank", "Morgana", "Rakan", "Bard",
}

# Champions that stack armor
ARMOR_STACK_CHAMPS = {
    "Malphite", "Rammus", "Poppy", "Leona", "Nautilus", "Ornn",
}


# ---------------------------------------------------------------------------
# Item database — known items and their threat types
# ---------------------------------------------------------------------------

ITEM_THREATS: dict[str, dict] = {
    # Healing items
    "shieldbow":    dict(healing=True,  shields=True,  crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "ravenous":     dict(healing=True,  shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "hydra":        dict(healing=True,  shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "sterak":       dict(healing=False, shields=True,  crit=False, ad=True,  ap=False, tank=True,  on_hit=False),
    "spirit visage":dict(healing=True,  shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "bloodthirster":dict(healing=True,  shields=True,  crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "lifeline":     dict(healing=True,  shields=True,  crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    # Burst / AP
    "shadowflame":  dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "ludens":       dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "deathcap":     dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "rabadons":     dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "void staff":   dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "stormsurge":   dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    # Crit / AD
    "infinity edge":dict(healing=False, shields=False, crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "ie":           dict(healing=False, shields=False, crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "kraken":       dict(healing=False, shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=True),
    "galeforce":    dict(healing=False, shields=False, crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "phantom dancer":dict(healing=False,shields=True,  crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "bt":           dict(healing=True,  shields=True,  crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    "yun tal":      dict(healing=False, shields=False, crit=True,  ad=True,  ap=False, tank=False, on_hit=False),
    # Dual resist
    "bandlepipes":  dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    # Tank
    "sunfire":      dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "heartsteel":   dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "thornmail":    dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "frozen heart": dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "randuins":     dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "warmogs":      dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "triforce":     dict(healing=False, shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "iceborn":      dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    # New / updated items (patch 16.8.1)
    "jak'sho":      dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "overlord":     dict(healing=True,  shields=False, crit=False, ad=True,  ap=False, tank=True,  on_hit=False),
    "eclipse":      dict(healing=False, shields=True,  crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "profane":      dict(healing=True,  shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "opportunity":  dict(healing=False, shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "voltaic":      dict(healing=False, shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "stridebreaker":dict(healing=False, shields=False, crit=False, ad=True,  ap=False, tank=False, on_hit=False),
    "kaenic":       dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "hollow":       dict(healing=False, shields=False, crit=False, ad=False, ap=False, tank=True,  on_hit=False),
    "cryptbloom":   dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "bloodletter":  dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "liandry":      dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "moonstone":    dict(healing=True,  shields=True,  crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "echoes":       dict(healing=True,  shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
    "mandate":      dict(healing=False, shields=False, crit=False, ad=False, ap=True,  tank=False, on_hit=False),
}

def parse_enemy_items(raw: str) -> dict[str, list[str]]:
    """Parse 'Caitlyn:Kraken+BT,Nautilus:Sunfire+Thornmail' into a dict."""
    result = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" not in entry:
            continue
        champ, items_raw = entry.split(":", 1)
        result[champ.strip()] = [i.strip().lower() for i in items_raw.split("+")]
    return result


# ---------------------------------------------------------------------------
# Threat aggregation
# ---------------------------------------------------------------------------

def aggregate_threats(
    enemy_champs: list[tuple[str, dict | None]],
    keystones: dict[str, int],
    manual_items: dict[str, list[str]],
) -> dict:
    threats = dict(
        healing=0, shields=0,
        burst_magic=0, burst_physical=0,
        on_hit=0, crit=0, poke=0,
        tank_count=0, total_ap=0, total_ad=0,
        names_with_healing=[], names_with_shields=[],
    )

    for name, data in enemy_champs:
        # From champion DB
        if data:
            if data["dmg"] == "magic":    threats["total_ap"] += 1
            if data["dmg"] == "physical": threats["total_ad"] += 1
            if data["cls"] in ("tank", "fighter"): threats["tank_count"] += 1
            if data["cls"] == "assassin":
                if data["dmg"] == "magic":    threats["burst_magic"] += 1
                else:                          threats["burst_physical"] += 1

        # Innate healing
        if name in HEALING_CHAMPS:
            threats["healing"] += 1
            threats["names_with_healing"].append(name)

        # Innate shields
        if name in SHIELD_CHAMPS:
            threats["shields"] += 1
            threats["names_with_shields"].append(name)

        # From rune keystone
        ks_id = keystones.get(name)
        if ks_id and ks_id in KEYSTONES:
            ks = KEYSTONES[ks_id]
            if ks["healing"]: threats["healing"] += 0.5
            if ks["on_hit"]:  threats["on_hit"] += 1
            if ks["poke"]:    threats["poke"] += 1

        # From manually specified items
        for item_raw in manual_items.get(name, []):
            for item_key, item_data in ITEM_THREATS.items():
                if item_key in item_raw:
                    if item_data["healing"]: threats["healing"] += 1
                    if item_data["shields"]: threats["shields"] += 1
                    if item_data["ap"]:      threats["total_ap"] += 0.5
                    if item_data["ad"]:      threats["total_ad"] += 0.5
                    if item_data["crit"]:    threats["crit"] += 1
                    if item_data["on_hit"]:  threats["on_hit"] += 1

    return threats


# ---------------------------------------------------------------------------
# Adaptive item recommendations
# ---------------------------------------------------------------------------

# (item, slot, cost, condition_fn, reason)
_ADC_CHAMPS = {"caitlyn", "jinx", "ezreal", "kaisa", "twitch", "vayne", "ashe", "jhin",
               "draven", "samira", "kogmaw", "tristana", "aphelios", "xayah", "sivir", "lucian", "missfortune"}

ADAPTIVE_ITEMS = [
    # Grievous Wounds
    dict(name="Thornmail", slot="flex", cost=2700,
         condition=lambda t, champ: t["healing"] >= 2 and champ.lower() == "rakan" and t["total_ad"] >= 2,
         reason="Applies GW on being hit — best GW option for Rakan vs healing + AD heavy comps"),
    dict(name="Oblivion Orb → Morellonomicon", slot="flex", cost=1000,
         condition=lambda t, champ: t["healing"] >= 2 and champ.lower() == "rakan" and t["total_ad"] < 2,
         reason="Cheap GW — buy before 3rd item when enemy heals but isn't AD heavy"),
    dict(name="Oblivion Orb → Morellonomicon", slot="flex", cost=1000,
         condition=lambda t, champ: t["healing"] >= 2 and champ.lower() != "rakan",
         reason="Cheap GW component — buy this before completing your 3rd item if enemy heals a lot"),

    # Anti-shield
    dict(name="Serpent's Fang", slot="flex", cost=1200,
         condition=lambda t, champ: t["shields"] >= 2,
         reason="Reduces shields by 50% on the enemy you hit — strong vs Janna/Lulu/Karma"),

    # Magic resistance
    dict(name="Force of Nature", slot="flex", cost=2900,
         condition=lambda t, champ: t["total_ap"] >= 3 and t["burst_magic"] == 0 and champ.lower() not in _ADC_CHAMPS,
         reason="Stacks MR per magic hit — best sustained MR item vs multiple magic sources"),
    dict(name="Banshee's Veil", slot="flex", cost=3100,
         condition=lambda t, champ: t["burst_magic"] >= 1 and champ.lower() not in _ADC_CHAMPS,
         reason="Spell shield blocks the one-shot setup from burst mages/assassins"),
    dict(name="Mercury's Treads", slot="boots", cost=1100,
         condition=lambda t, champ: t["total_ap"] >= 3 and champ.lower() not in _ADC_CHAMPS,
         reason="Tenacity + MR — essential when enemy has 3+ magic sources"),

    # Armor
    dict(name="Plated Steelcaps", slot="boots", cost=1100,
         condition=lambda t, champ: t["total_ad"] >= 3 and t["crit"] == 0 and champ.lower() not in _ADC_CHAMPS,
         reason="Reduces all physical and auto-attack damage — buy vs heavy AD"),
    dict(name="Randuin's Omen", slot="flex", cost=2700,
         condition=lambda t, champ: t["crit"] >= 2,
         reason="Reduces crit damage by 20% — mandatory when 2+ enemies are crit-building"),
    dict(name="Frozen Heart", slot="flex", cost=2500,
         condition=lambda t, champ: t["on_hit"] >= 2,
         reason="Reduces attack speed of nearby enemies — counters on-hit and attack-speed heavy comps"),

    # Dual resist
    dict(name="Bandlepipes",               slot="flex",    cost=2300,
         condition=lambda t, champ: t["total_ap"] >= 2 and t["total_ad"] >= 2,
         reason="200 HP + 20 Armor + 20 MR + Haste — best value when enemy has both magic and physical damage"),

    # Rakan-specific
    dict(name="Locket of the Iron Solari", slot="mythic", cost=2500,
         condition=lambda t, champ: champ.lower() == "rakan" and t["total_ap"] >= 3,
         reason="AoE shield counters magic burst when you land in the middle of the enemy"),
    dict(name="Shurelya's Battlesong", slot="mythic", cost=2500,
         condition=lambda t, champ: champ.lower() == "rakan" and t["total_ap"] < 3,
         reason="Speed burst lets your whole team follow your engage — right call vs physical-heavy enemy"),
    dict(name="Mikael's Blessing", slot="support", cost=2300,
         condition=lambda t, champ: champ.lower() == "rakan" and any(
             n in {"Nautilus", "Morgana", "Thresh", "Blitzcrank", "Leona"}
             for n in t.get("names_with_shields", []) + []
         ),
         reason="Cleanse your ADC from the one CC that would kill them"),
    dict(name="Knight's Vow", slot="support", cost=2300,
         condition=lambda t, champ: champ.lower() == "rakan",
         reason="Link to your ADC — damage they take partially redirected to you"),
    dict(name="Zeke's Convergence", slot="support", cost=2400,
         condition=lambda t, champ: champ.lower() == "rakan" and t["total_ad"] <= 2,
         reason="Empowers linked ADC's damage — strong when enemy isn't stacking armor"),
    dict(name="Warmog's Armor", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "rakan" and t["burst_magic"] + t["burst_physical"] >= 2,
         reason="Makes you hard to kill between engages — keeps you going in vs assassin-heavy enemy"),

    # ── Kayn — Shadow Assassin (< 2 tanks) ────────────────────────────────
    dict(name="Profane Hydra", slot="mythic", cost=2850,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] < 2,
         reason="SA first item — burst active resets on kill and chains with your E; AH for more Q/E rotations"),
    dict(name="Edge of Night", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] < 2,
         reason="SA 2nd item — lethality + spell shield; blocks the CC/nuke that stops your dive"),
    dict(name="Axiom Arc", slot="flex", cost=2750,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] < 2 and t["healing"] == 0,
         reason="SA 3rd — refunds R on kills; R is your gap-close, more casts = more dive windows per fight"),
    dict(name="Ionian Boots of Lucidity", slot="boots", cost=950,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] < 2,
         reason="Haste boots for SA — more E dashes and W wall phases per fight"),
    # ── Kayn — Rhaast (≥ 2 tanks) ─────────────────────────────────────────
    dict(name="Sundered Sky", slot="mythic", cost=3100,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] >= 2,
         reason="Rhaast first item — guaranteed crit heal on first hit in fight; Rhaast passive makes this heal for enormous amounts"),
    dict(name="Black Cleaver", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] >= 2,
         reason="Rhaast 2nd item — stacks 6% armor shred per hit up to 30%; your repeated Q/E hits shred tanks fast"),
    dict(name="Death's Dance", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] >= 2,
         reason="Rhaast 3rd — converts 30% physical damage to a bleed; Rhaast heals it off before it kills you"),
    dict(name="Plated Steelcaps", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "kayn" and t["tank_count"] >= 2,
         reason="Armor boots for Rhaast — you fight in melee range; reduces all physical/auto damage"),
    # ── Kayn situational (both paths) ─────────────────────────────────────
    dict(name="Spirit Visage", slot="flex", cost=2900,
         condition=lambda t, champ: champ.lower() == "kayn" and t["total_ap"] >= 3 and t["tank_count"] >= 2,
         reason="Rhaast + AP-heavy enemy — amplifies Rhaast passive heals by 25%; you become nearly unkillable vs magic damage"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "kayn" and (t["burst_magic"] >= 1 or t["burst_physical"] >= 1),
         reason="SA — you dive their backline alone; GA gives a second window if they burst you mid-combo"),
    dict(name="Serpent's Fang", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "kayn" and t["shields"] >= 2,
         reason="Reduces shields by 50% on hit — buy early when enemy has Lulu/Nami/Janna protecting their carries"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "kayn" and t["healing"] >= 2,
         reason="GW on every hit — buy before 3rd item vs Soraka, Warwick, Yuumi; their healing shuts down your all-ins"),

    # Kha'Zix core — first item choices
    dict(name="Profane Hydra", slot="mythic", cost=2850,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] < 2,
         reason="Core first item — active burst resets on kill, AH for more Q casts, lethality spikes your one-shot pattern"),
    dict(name="Hubris", slot="mythic", cost=2800,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] >= 2,
         reason="Stacks AD on kills — snowballs hard in games where you're ahead; each stack increases your Q isolation damage"),
    # Boots
    dict(name="Ionian Boots of Lucidity", slot="boots", cost=950,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["total_ad"] < 3,
         reason="Cheap haste boots — more Q casts per fight, faster E cooldown, better jungle clear tempo"),
    dict(name="Plated Steelcaps", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["total_ad"] >= 3,
         reason="Armor boots vs AD-heavy — reduces auto-attack damage when you're diving in"),
    # Kha'Zix situational
    dict(name="Serylda's Grudge", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] >= 2,
         reason="30% armor pen + slow on Q — buy 2nd when enemy has 2+ tanks or bruisers stacking armor"),
    dict(name="Axiom Arc", slot="flex", cost=2750,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] < 2,
         reason="Refunds R ultimate on kills — more ult casts = more isolation resets and stealth windows per fight"),
    dict(name="Edge of Night", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["burst_magic"] >= 1,
         reason="Spell shield blocks the CC/nuke that stops your engage — buy vs Lux, Syndra, Veigar, Lissandra"),
    dict(name="Youmuu's Ghostblade", slot="flex", cost=2800,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] == 0,
         reason="Lethality + MS active — use active before E leap for gap closing on fast targets"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["burst_physical"] >= 1,
         reason="Revive lets you dive freely — die into their burst, come back up, finish with E reset"),
    dict(name="Serpent's Fang", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["shields"] >= 2,
         reason="Reduces shields by 50% on hit — buy early when enemy has Janna/Lulu/Milio shielding their carries"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["healing"] >= 2,
         reason="GW on your Q hits — buy before 3rd item when enemy has 2+ healers (Soraka, Aatrox, Sylas)"),
    dict(name="Lord Dominik's Regards", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() in ("khazix", "kha'zix") and t["tank_count"] >= 2 and t["healing"] == 0,
         reason="35% armor pen + bonus damage vs high-HP — buy when tanks stack armor but don't heal"),

    # Caitlyn core
    dict(name="Kraken Slayer", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["tank_count"] >= 2,
         reason="True damage on every 3rd hit — mandatory when enemy has 2+ tanks or bruisers stacking HP/armor"),
    dict(name="Statikk Shiv", slot="mythic", cost=2700,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["tank_count"] < 2,
         reason="Wave clear + chain lightning procs on headshots — standard first item, spikes your poke pattern early"),
    dict(name="Berserker's Greaves", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "caitlyn",
         reason="Attack speed boots — more headshot procs per fight, increases trap setup speed"),
    # Caitlyn 2nd/3rd items
    dict(name="Infinity Edge", slot="flex", cost=3500,
         condition=lambda t, champ: champ.lower() == "caitlyn",
         reason="Core 2nd item — 25% crit + 40% bonus crit damage; every headshot crits for massive damage at 2 items"),
    dict(name="Rapid Firecannon", slot="flex", cost=2650,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["tank_count"] < 2,
         reason="Extends your range on the empowered shot — headshot from fog of war, hit traps from extreme range, poke safely"),
    dict(name="Runaan's Hurricane", slot="flex", cost=2650,
         condition=lambda t, champ: champ.lower() == "caitlyn" and len([1 for _ in range(t.get("tank_count", 0))]) == 0 and t.get("total_ad", 0) < 3,
         reason="Bolts proc headshots on side targets — shreds grouped teamfights, great when enemy clusters"),
    # Caitlyn situational
    dict(name="Lord Dominik's Regards", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["tank_count"] >= 2,
         reason="35% armor pen — buy 3rd when enemy tanks are stacking armor; pairs with Kraken to delete frontline"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["healing"] >= 2,
         reason="GW on every headshot — buy before 3rd item when enemy has 2+ healers (Soraka, Aatrox, Yuumi)"),
    dict(name="Mercurial Scimitar", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["hard_cc"] >= 3,
         reason="Cleanse active removes the CC that gets you killed — buy vs Nautilus/Leona/Lissandra hard-engage"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["burst_physical"] >= 1,
         reason="Revive keeps you alive after a dive — buy vs Zed, Talon, or any assassin that hard-focuses you"),
    dict(name="The Collector", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "caitlyn" and t["tank_count"] == 0 and t["healing"] == 0,
         reason="Execute threshold on crit — headshot + Collector finishes low-HP targets they would otherwise escape"),

    # Draven core
    dict(name="Immortal Shieldbow", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "draven" and (t["burst_physical"] >= 1 or t["burst_magic"] >= 1),
         reason="Lifeline shield saves you from assassin one-shots — Draven is a kill target, this gives you the window to fight back"),
    dict(name="Kraken Slayer", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "draven" and t["tank_count"] >= 2 and t["burst_physical"] == 0,
         reason="True damage on every 3rd hit — mandatory when they have 2+ tanks; your axes hit like trucks with this"),
    dict(name="The Collector", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "draven" and t["tank_count"] == 0 and t["burst_physical"] == 0,
         reason="Execute on crit — snowball item for when you're ahead; finishes low-HP targets before they escape"),
    dict(name="Berserker's Greaves", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "draven",
         reason="Attack speed boots — more axes spinning, more Adoration stacks, faster headshot tempo"),
    # Draven 2nd/3rd
    dict(name="Infinity Edge", slot="flex", cost=3500,
         condition=lambda t, champ: champ.lower() == "draven",
         reason="Core 2nd item — 40% bonus crit damage makes every crit axe hit for massive burst"),
    dict(name="Lord Dominik's Regards", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() == "draven" and t["tank_count"] >= 2,
         reason="35% armor pen — buy 3rd when tanks are stacking armor; your Q axes shred them"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "draven" and t["healing"] >= 2,
         reason="GW on every axe hit — buy before 3rd item when enemy has 2+ healers"),
    dict(name="Mercurial Scimitar", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "draven" and t["hard_cc"] >= 3,
         reason="Cleanse the CC that stops your axes — you die the moment you're locked down, this is your escape button"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "draven" and t["burst_physical"] >= 1,
         reason="Revive lets you fight aggressively — dive in knowing you get a second chance to cash out your Adoration stacks"),
    dict(name="Navori Flickerblade", slot="flex", cost=2650,
         condition=lambda t, champ: champ.lower() == "draven" and t["tank_count"] == 0 and t["healing"] == 0,
         reason="Crit reduces Q cooldown — more frequent axe refreshes, higher DPS with spinning axes up constantly"),

    # Diana core
    dict(name="Evenshroud → Rabadon's", slot="mythic", cost=2500,
         condition=lambda t, champ: champ.lower() == "diana" and t["tank_count"] >= 2,
         reason="Evenshroud + tank shred — reduces enemy MR after R, hugely amplifies your follow-up"),
    dict(name="Shadowflame → Rabadon's", slot="mythic", cost=3200,
         condition=lambda t, champ: champ.lower() == "diana" and t["tank_count"] < 2,
         reason="Shadowflame flat magic pen one-shots squishies on your dive. Rush Rabadon's 3rd"),
    dict(name="Sorcerer's Shoes", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "diana" and t["total_ad"] <= 2,
         reason="Magic pen boots — increases every spell's damage early"),
    dict(name="Plated Steelcaps", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "diana" and t["total_ad"] >= 3,
         reason="Armor boots vs AD-heavy enemy — you're diving into their team"),

    # Diana situational
    dict(name="Zhonya's Hourglass", slot="flex", cost=2600,
         condition=lambda t, champ: champ.lower() == "diana" and (t["burst_physical"] >= 1 or t["total_ad"] >= 3),
         reason="Dive in with R, pop Zhonya's — their burst misses, you come out full combo"),
    dict(name="Void Staff", slot="flex", cost=2800,
         condition=lambda t, champ: champ.lower() == "diana" and t["tank_count"] >= 2,
         reason="40% magic pen — enemy building MR, this turns your burst back on. Buy 3rd"),
    dict(name="Banshee's Veil", slot="flex", cost=3100,
         condition=lambda t, champ: champ.lower() == "diana" and t["burst_magic"] >= 1,
         reason="Spell shield blocks the CC that stops your engage — buy vs Lux/Syndra/Fizz"),
    dict(name="Morellonomicon", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "diana" and t["healing"] >= 2,
         reason="GW on your Q/E hits — buy before 3rd item when enemy has 2+ healers"),
    dict(name="Rabadon's Deathcap", slot="flex", cost=3600,
         condition=lambda t, champ: champ.lower() == "diana" and t["burst_magic"] == 0 and t["tank_count"] < 2,
         reason="30% amp on all AP — buy 3rd when nobody threatens your dive"),

    # Ekko core
    dict(name="Shadowflame → Rabadon's", slot="mythic", cost=3200,
         condition=lambda t, champ: champ.lower() == "ekko" and t["total_ad"] >= t["total_ap"],
         reason="Shadowflame first — flat magic pen one-shots squishies. Rush Rabadon's 3rd to double your AP"),
    dict(name="Luden's Tempest → Rabadon's", slot="mythic", cost=3200,
         condition=lambda t, champ: champ.lower() == "ekko" and t["total_ap"] > t["total_ad"],
         reason="Luden's first vs magic-heavy enemy — extra mobility and poke before you can dive safely"),
    dict(name="Sorcerer's Shoes", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "ekko",
         reason="Magic pen boots are core — increases every spell's damage by ~10% early"),

    # Ekko situational
    dict(name="Zhonya's Hourglass", slot="flex", cost=2600,
         condition=lambda t, champ: champ.lower() == "ekko" and (t["burst_physical"] >= 1 or t["total_ad"] >= 3),
         reason="Rush this 2nd vs assassins/AD heavy — Hourglass active buys the 2.5s you need for R to rewind"),
    dict(name="Banshee's Veil", slot="flex", cost=3100,
         condition=lambda t, champ: champ.lower() == "ekko" and t["burst_magic"] >= 1,
         reason="Spell shield blocks the setup that kills you before R — buy vs Syndra, Orianna, Lissandra"),
    dict(name="Void Staff", slot="flex", cost=2800,
         condition=lambda t, champ: champ.lower() == "ekko" and t["tank_count"] >= 2,
         reason="40% magic pen — enemy building MR, this turns your burst back on. Buy 3rd or 4th"),
    dict(name="Cosmic Drive", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "ekko" and t["total_ap"] >= 3 and t["burst_magic"] == 0,
         reason="AP + move speed + haste — lets you stick to targets and cast more rotations in extended fights"),
    dict(name="Morellonomicon", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "ekko" and t["healing"] >= 2,
         reason="GW on your W/Q hits — buy before 3rd item when enemy has 2+ healers"),

    # ── Akali core ────────────────────────────────────────────────────────
    dict(name="Hextech Rocketbelt", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "akali" and t["tank_count"] < 2,
         reason="First item standard — dash active closes gap on mobile targets; AP + magic pen for early burst pattern"),
    dict(name="Lich Bane", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "akali" and t["tank_count"] >= 2,
         reason="First item vs tanks — empowered auto after every spell procs massive magic damage; W reset loops proc it repeatedly"),
    dict(name="Sorcerer's Shoes", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "akali" and t["total_ad"] <= 2,
         reason="Magic pen boots — increases every ability's damage by ~10% early; standard on AP assassins"),
    dict(name="Plated Steelcaps", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "akali" and t["total_ad"] >= 3,
         reason="Armor boots vs AD-heavy — you dive into their team, reduce physical damage taken"),
    # Akali 2nd/3rd
    dict(name="Shadowflame", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "akali" and t["shields"] == 0 and t["tank_count"] < 2,
         reason="2nd item standard — flat magic pen one-shots squishies at 2 items; bonus pen vs low-HP targets syncs with your kill pattern"),
    dict(name="Serpent's Fang", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "akali" and t["shields"] >= 2,
         reason="Reduces shields 50% on hit — buy early when enemy has Lulu/Seraphine/Milio protecting their carries"),
    dict(name="Rabadon's Deathcap", slot="flex", cost=3600,
         condition=lambda t, champ: champ.lower() == "akali" and t["burst_magic"] == 0 and t["tank_count"] < 2,
         reason="30% AP amplifier — buy 3rd when nobody threatens your dive; doubles your burst damage at full build"),
    dict(name="Void Staff", slot="flex", cost=2800,
         condition=lambda t, champ: champ.lower() == "akali" and t["tank_count"] >= 2,
         reason="40% magic pen — enemy building MR, this keeps your burst relevant; buy 3rd when tanks stack MR"),
    dict(name="Zhonya's Hourglass", slot="flex", cost=2600,
         condition=lambda t, champ: champ.lower() == "akali" and (t["burst_physical"] >= 1 or t["total_ad"] >= 3),
         reason="Dive in, burst, pop Zhonya's while W shroud is on cooldown — their burst misses, you come out with full combo ready"),
    dict(name="Banshee's Veil", slot="flex", cost=3100,
         condition=lambda t, champ: champ.lower() == "akali" and t["burst_magic"] >= 1,
         reason="Spell shield blocks the CC that stops your combo — buy vs Ahri, Lux, Syndra, Lissandra"),
    dict(name="Morellonomicon", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "akali" and t["healing"] >= 2,
         reason="GW on every ability hit — buy before 3rd item vs Soraka, Aatrox, Sylas"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "akali" and t["burst_physical"] >= 1,
         reason="Revive lets you dive aggressively — die into their burst, come back up mid-shroud with full combo"),

    # ── Viego core ────────────────────────────────────────────────────────
    dict(name="Kraken Slayer", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] >= 2,
         reason="First item vs tanks — true damage every 3rd hit shreds HP/armor stacks; Viego's passive lets him proc it repeatedly on each target"),
    dict(name="Immortal Shieldbow", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] < 2 and (t["burst_magic"] >= 1 or t["burst_physical"] >= 1),
         reason="First item vs burst — lifeline shield saves you mid-possession combo; you need to stay alive long enough to chain possessions"),
    dict(name="Ravenous Hydra", slot="mythic", cost=3300,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] < 2 and t["burst_magic"] == 0 and t["burst_physical"] == 0,
         reason="First item standard — healing scales with your AD, active resets on kills; chains possessions much faster in fights"),
    dict(name="Berserker's Greaves", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "viego",
         reason="Attack speed boots — Viego's passive (possession) procs faster with more autos; more W stacks too"),
    # Viego 2nd/3rd
    dict(name="Infinity Edge", slot="flex", cost=3500,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] < 2,
         reason="Core 2nd item — 40% bonus crit damage; every crit auto while possessing an enemy deals massive damage"),
    dict(name="Blade of the Ruined King", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] >= 2,
         reason="2nd item vs tanks — % current HP damage on autos; on-hit syncs with Kraken for massive tank shred"),
    dict(name="Wit's End", slot="flex", cost=2900,
         condition=lambda t, champ: champ.lower() == "viego" and t["total_ap"] >= 3,
         reason="MR + on-hit magic damage — buy 2nd vs AP-heavy; gives MR to survive dives and adds on-hit damage to every auto"),
    dict(name="Phantom Dancer", slot="flex", cost=2600,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] < 2 and t["burst_physical"] == 0,
         reason="AS + crit + ghosting — lets you walk through minions/tanks to chase during possession windows"),
    dict(name="Lord Dominik's Regards", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() == "viego" and t["tank_count"] >= 2,
         reason="35% armor pen — buy 3rd when tanks stack armor; pairs with Kraken + BotRK to delete frontline"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "viego" and (t["burst_physical"] >= 1 or t["burst_magic"] >= 1),
         reason="Revive while you chain possessions — die into their burst, come back up mid-fight with a new body to pilot"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "viego" and t["healing"] >= 2,
         reason="GW on every auto — buy before 3rd item vs Soraka, Yuumi, Aatrox; their healing cancels your kill windows"),
    dict(name="Serpent's Fang", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "viego" and t["shields"] >= 2,
         reason="Reduces shields 50% on hit — buy early when enemy has Lulu/Seraphine shielding their carries"),

    # ── Zed core ──────────────────────────────────────────────────────────
    dict(name="Youmuu's Ghostblade", slot="mythic", cost=2800,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] < 2,
         reason="First item vs squishies — lethality + MS active lets you close the gap before they can react to shadow"),
    dict(name="Serylda's Grudge", slot="mythic", cost=3000,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] >= 2,
         reason="First item vs tanks/bruisers — 30% armor pen + W slow on every ability; your burst actually hurts through armor"),
    dict(name="Ionian Boots of Lucidity", slot="boots", cost=950,
         condition=lambda t, champ: champ.lower() == "zed" and t["total_ap"] < 3,
         reason="Standard haste boots — lower R cooldown means more ult dives, more W shadow casts per fight"),
    dict(name="Sorcerer's Shoes", slot="boots", cost=1100,
         condition=lambda t, champ: champ.lower() == "zed" and t["total_ap"] >= 3,
         reason="Magic resist penetration boots vs AP-heavy — Zed takes a lot of magic damage diving in, this is defensive value"),
    # Zed 2nd/3rd
    dict(name="The Collector", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] < 2 and t["healing"] == 0,
         reason="Execute threshold on crit — targets below 5% HP die instantly; pairs with your R mark for guaranteed kills"),
    dict(name="Edge of Night", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "zed" and t["burst_magic"] >= 1,
         reason="Spell shield blocks the CC or nuke that stops your R combo — must-buy vs Mel, Lux, Syndra, Lissandra"),
    dict(name="Opportunity", slot="flex", cost=2700,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] < 2,
         reason="Lethality + pre-fight MS steroid; active gives bonus damage on your first ability out of stealth — huge R opener"),
    dict(name="Axiom Arc", slot="flex", cost=2750,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] < 2 and t["healing"] == 0,
         reason="Refunds R on kills — more ult windows per fight; once ahead you can R every skirmish"),
    dict(name="Serpent's Fang", slot="flex", cost=2500,
         condition=lambda t, champ: champ.lower() == "zed" and t["shields"] >= 2,
         reason="Reduces shields by 50% on hit — buy early when enemy has Lulu/Janna/Milio protecting their carries"),
    dict(name="Lord Dominik's Regards", slot="flex", cost=3300,
         condition=lambda t, champ: champ.lower() == "zed" and t["tank_count"] >= 2,
         reason="35% armor pen + bonus damage vs high-HP targets — buy 2nd or 3rd when tanks are building armor stacks"),
    dict(name="Guardian Angel", slot="flex", cost=3200,
         condition=lambda t, champ: champ.lower() == "zed" and t["burst_magic"] >= 1,
         reason="Revive gives you a second window after diving — you die into their burst, come back up, finish with shadows"),
    dict(name="Mortal Reminder", slot="flex", cost=3000,
         condition=lambda t, champ: champ.lower() == "zed" and t["healing"] >= 2,
         reason="GW on every auto and ability hit — buy before 3rd item when enemy has 2+ healers"),
]


def get_adaptive_recommendations(my_champ: str, threats: dict, manual_items: dict) -> list[dict]:
    recommendations = []
    seen = set()
    for item in ADAPTIVE_ITEMS:
        try:
            if item["condition"](threats, my_champ) and item["name"] not in seen:
                seen.add(item["name"])
                recommendations.append(item)
        except Exception:
            pass
    return recommendations


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

SLOT_LABEL = {
    "mythic": "[bold yellow]Mythic[/bold yellow]",
    "boots":  "[bold cyan]Boots[/bold cyan]",
    "support":"[bold green]Support[/bold green]",
    "flex":   "[bold magenta]Flex/Situational[/bold magenta]",
}

def print_enemy_read(
    enemy_champs: list[tuple[str, dict | None]],
    keystones: dict[str, int],
    manual_items: dict[str, list[str]],
):
    t = Table(title="Enemy Read", box=box.ROUNDED, show_header=True)
    t.add_column("Champion", style="cyan", min_width=14)
    t.add_column("Keystone")
    t.add_column("Predicted threat")
    t.add_column("Items seen")

    for name, data in enemy_champs:
        ks_id  = keystones.get(name, 0)
        ks     = KEYSTONES.get(ks_id, {})
        ks_name = ks.get("name", "—")

        threats_list = []
        if name in HEALING_CHAMPS or ks.get("healing"): threats_list.append("[red]healing[/red]")
        if name in SHIELD_CHAMPS:                        threats_list.append("[blue]shields[/blue]")
        if ks.get("burst"):                              threats_list.append("[magenta]burst[/magenta]")
        if ks.get("tank"):                               threats_list.append("[yellow]tank[/yellow]")
        if ks.get("on_hit"):                             threats_list.append("[green]on-hit[/green]")
        if ks.get("poke"):                               threats_list.append("poke")
        threat_str = ", ".join(threats_list) if threats_list else "[dim]—[/dim]"

        items_seen = ", ".join(manual_items.get(name, [])) or "[dim]none specified[/dim]"

        t.add_row(name, ks_name, threat_str, items_seen)

    console.print(t)


def print_recommendations(my_champ: str, recommendations: list[dict]):
    if not recommendations:
        console.print("[dim]No specific adaptive items found.[/dim]")
        return

    t = Table(title=f"Adaptive Build — {my_champ}", box=box.ROUNDED, show_lines=True)
    t.add_column("Slot",   min_width=18)
    t.add_column("Item",   style="cyan", min_width=22)
    t.add_column("Cost",   justify="right")
    t.add_column("Why this game", max_width=55)

    for item in recommendations:
        t.add_row(
            SLOT_LABEL.get(item["slot"], item["slot"]),
            item["name"],
            f"{item['cost']:,}g",
            item["reason"],
        )

    console.print(t)


# ---------------------------------------------------------------------------
# Live game fetch
# ---------------------------------------------------------------------------

def fetch_live_data(account_label: str) -> tuple[str, list[tuple], dict[str, int]]:
    """Returns (my_champ, [(enemy_name, data|None)], {enemy_name: keystone_id})"""
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct = get_account(game_name, tag)
    puuid = acct["puuid"]

    try:
        data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}")
    except Exception as e:
        if "404" in str(e):
            console.print("[red]You're not currently in a game.[/red]")
        else:
            console.print(f"[red]Spectator API error: {e}[/red]")
        sys.exit(1)

    _load_champ_id_map()

    my_team_id = next(p["teamId"] for p in data["participants"] if p["puuid"] == puuid)
    my_champ   = champ_name_from_id(next(p["championId"] for p in data["participants"] if p["puuid"] == puuid))

    enemy_champs = []
    keystones    = {}
    for p in data["participants"]:
        if p["teamId"] == my_team_id or p["puuid"] == puuid:
            continue
        name = champ_name_from_id(p["championId"])
        champ_data = resolve(name)
        enemy_champs.append((name, champ_data[1] if champ_data else None))
        ks_id = p.get("perks", {}).get("perkIds", [None])[0]
        if ks_id:
            keystones[name] = ks_id

    return my_champ, enemy_champs, keystones


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Adaptive build advisor")
    parser.add_argument("champion",      nargs="?", help="Your champion (manual mode)")
    parser.add_argument("--ally",        help="Your allies (manual mode)")
    parser.add_argument("--enemy",       help="Enemy champions, comma-separated (manual mode)")
    parser.add_argument("--live",        metavar="ACCOUNT", choices=list(ACCOUNTS.keys()),
                        help="Pull from current live game")
    parser.add_argument("--enemy-items", metavar="ITEMS",
                        help='Enemy items mid-game: "Caitlyn:Kraken+BT,Nautilus:Sunfire+Thornmail"')
    args = parser.parse_args()

    manual_items = parse_enemy_items(args.enemy_items) if args.enemy_items else {}

    if args.live:
        console.print(f"\n[bold]Reading live game for [cyan]{ACCOUNTS[args.live]}[/cyan]...[/bold]\n")
        my_champ, enemy_champs, keystones = fetch_live_data(args.live)
    else:
        if not args.champion or not args.enemy:
            parser.error("Provide --champion + --enemy or use --live ACCOUNT")
        my_champ = args.champion
        enemy_names = [n.strip() for n in args.enemy.split(",")]
        enemy_champs = [(n, (resolve(n)[1] if resolve(n) else None)) for n in enemy_names]
        keystones = {}

    console.print(f"[bold]Your champion:[/bold] [cyan]{my_champ}[/cyan]\n")

    threats = aggregate_threats(enemy_champs, keystones, manual_items)

    # Add hook/CC support names for Mikael's check
    threats["names_with_shields"] = [n for n, _ in enemy_champs if n in SHIELD_CHAMPS]

    print_enemy_read(enemy_champs, keystones, manual_items)
    recommendations = get_adaptive_recommendations(my_champ, threats, manual_items)
    print_recommendations(my_champ, recommendations)


if __name__ == "__main__":
    main()
