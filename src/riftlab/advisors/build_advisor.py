"""
build_advisor.py — Adaptive build recommendations based on live game + enemy items.

Usage:
  # Champion select — predict enemy builds from champion + runes
  python -m riftlab.advisors.build_advisor --live main

  # Mid-game — feed in actual enemy items you can see
  python -m riftlab.advisors.build_advisor --live main --enemy-items "Caitlyn:Kraken+BT,Nautilus:Sunfire+Thornmail"

  # Manual comp (no API needed)
  python -m riftlab.advisors.build_advisor Rakan --ally "Sett,Ambessa,Fizz,Jinx" --enemy "Volibear,Udyr,Lissandra,Caitlyn,Nautilus"
"""

import sys
import argparse
import requests

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from riftlab.riot import get_account, BASE_SUMMONER, _get, ACCOUNTS, console
from riftlab.advisors.comp_check import resolve, CHAMPS, champ_name_from_id, _load_champ_id_map
from riftlab.champion_loader import get_build_items, eval_build_condition, resolve_name

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

_ADC_CHAMPS = {"caitlyn", "jinx", "ezreal", "kaisa", "twitch", "vayne", "ashe", "jhin",
               "draven", "samira", "kogmaw", "tristana", "aphelios", "xayah", "sivir", "lucian", "missfortune"}

# Generic items that apply to any champion — keyed on threat type, not champion name.
# Champion-specific items live in data/champions/<ChampName>.json (build_items field).
GENERIC_ADAPTIVE_ITEMS = [
    # Grievous Wounds (for champions without their own GW item in JSON)
    dict(name="Oblivion Orb → Morellonomicon", slot="flex", cost=1000,
         condition=lambda t, champ: t["healing"] >= 2,
         reason="Cheap GW component — buy before completing your 3rd item if enemy heals a lot"),

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
    dict(name="Bandlepipes", slot="flex", cost=2300,
         condition=lambda t, champ: t["total_ap"] >= 2 and t["total_ad"] >= 2,
         reason="200 HP + 20 Armor + 20 MR + Haste — best value when enemy has both magic and physical damage"),
]


def get_adaptive_recommendations(my_champ: str, threats: dict, manual_items: dict) -> list[dict]:
    """
    Returns build recommendations for my_champ.
    Champion-specific items are loaded from data/champions/<ChampName>.json.
    Generic items (MR, armor, GW) are applied on top via GENERIC_ADAPTIVE_ITEMS.
    """
    recommendations = []
    seen = set()

    # 1. Champion-specific items from JSON (eval condition DSL vs threats dict)
    canonical = resolve_name(my_champ) or my_champ
    for item in get_build_items(canonical):
        cond = item.get("conditions", {})
        try:
            if eval_build_condition(cond, threats) and item["name"] not in seen:
                seen.add(item["name"])
                recommendations.append({
                    "name":   item["name"],
                    "slot":   item["slot"],
                    "cost":   item["cost"],
                    "reason": item["reason"],
                })
        except Exception:
            pass

    # 2. Generic adaptive items — fill gaps not covered by champion JSON
    for item in GENERIC_ADAPTIVE_ITEMS:
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
