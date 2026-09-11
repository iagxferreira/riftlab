"""
comp_check.py — Analyze your comp vs the enemy comp and get role-specific tips.

Usage:
  python -m riftlab.advisors.comp_check Rakan --ally "Vayne,Orianna,JarvanIV,Mordekaiser" --enemy "Fizz,Khazix,Tryndamere,Jhin,Nautilus"
  python -m riftlab.advisors.comp_check --from-last main
"""

import sys
import argparse

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from riftlab.riot import get_account, get_match_ids, get_match, extract_participant, ACCOUNTS, BASE_SUMMONER, _get, console
from riftlab.champion_loader import get_comp_data, get_aliases, resolve_name

load_dotenv()

# ---------------------------------------------------------------------------
# Champion database — loaded from data/comp.json
# ---------------------------------------------------------------------------

CHAMPS: dict[str, dict] = get_comp_data()
NAME_ALIASES: dict[str, str] = get_aliases()

# ---------------------------------------------------------------------------
# Comp analysis
# ---------------------------------------------------------------------------


def resolve(name: str) -> dict | None:
    """Case-insensitive champion lookup with alias support."""
    normalized = name.lower().strip()
    canonical  = NAME_ALIASES.get(normalized, None)
    if canonical:
        return (canonical, CHAMPS[canonical]) if canonical in CHAMPS else None
    key = next((k for k in CHAMPS if k.lower() == normalized), None)
    return (key, CHAMPS[key]) if key else None


def analyze_comp(names: list[str]) -> dict:
    resolved, unknown = [], []
    for n in names:
        result = resolve(n)
        if result:
            resolved.append(result)
        else:
            unknown.append(n)

    champs = [c for _, c in resolved]

    hard_cc_count  = sum(1 for c in champs if c["hard_cc"])
    soft_cc_count  = sum(1 for c in champs if c["soft_cc"])
    assassins      = [n for n, c in resolved if c["cls"] == "assassin"]
    frontline      = [n for n, c in resolved if c["cls"] in ("tank", "fighter")]
    high_mobility  = [n for n, c in resolved if c["mobility"] == "high"]
    magic_dmg      = sum(1 for c in champs if c["dmg"] == "magic")
    phys_dmg       = sum(1 for c in champs if c["dmg"] == "physical")
    mixed_dmg      = sum(1 for c in champs if c["dmg"] == "mixed")
    late_scalers   = [n for n, c in resolved if c["scale"] == "late"]
    early_power    = [n for n, c in resolved if c["scale"] == "early"]
    win_cons       = [c["win_con"] for c in champs]
    primary_win_con = max(set(win_cons), key=win_cons.count) if win_cons else "unknown"

    return {
        "resolved":        resolved,
        "unknown":         unknown,
        "hard_cc":         hard_cc_count,
        "soft_cc":         soft_cc_count,
        "assassins":       assassins,
        "frontline":       frontline,
        "high_mobility":   high_mobility,
        "magic_dmg":       magic_dmg,
        "phys_dmg":        phys_dmg,
        "mixed_dmg":       mixed_dmg,
        "late_scalers":    late_scalers,
        "early_power":     early_power,
        "win_cons":        win_cons,
        "primary_win_con": primary_win_con,
        "total":           len(champs),
    }


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def generate_matchup_insights(my_champ: str, ally: dict, enemy: dict) -> list[tuple[str, str]]:
    insights = []
    mc = resolve(my_champ)
    if not mc:
        return [("warn", f"Champion '{my_champ}' not in database — tips will be limited.")]
    _, my_data = mc

    # --- Damage type warning ---
    if enemy["magic_dmg"] >= 4:
        insights.append(("warn",
            f"Enemy has {enemy['magic_dmg']}/5 magic damage sources. "
            "Consider Magic Resistance items if you have any AP items in build."))
    if enemy["phys_dmg"] >= 4:
        insights.append(("warn",
            f"Enemy is heavy physical damage ({enemy['phys_dmg']}/5). "
            "Armor itemization is worth considering."))
    if enemy["magic_dmg"] >= 2 and enemy["phys_dmg"] >= 2:
        insights.append(("info",
            "Enemy has mixed damage — your team can't stack one resist type. "
            "Focus on not getting one-shot rather than building for one damage type."))

    # --- Assassin threat ---
    if enemy["assassins"]:
        assassin_list = ", ".join(enemy["assassins"])
        if my_data["cls"] in ("support_engage", "support_peel", "support_utility"):
            insights.append(("warn",
                f"Enemy has {len(enemy['assassins'])} assassin(s): [bold]{assassin_list}[/bold]. "
                "As support, your carry is their primary target. "
                "Stay between them and your ADC — don't hard engage and leave your ADC alone."))
        elif my_data["mobility"] == "low":
            insights.append(("warn",
                f"You have low mobility and the enemy has [bold]{assassin_list}[/bold]. "
                "Hug your frontline. Don't walk forward unless you're sure they're on cooldown."))

    # --- CC comparison ---
    cc_advantage = ally["hard_cc"] - enemy["hard_cc"]
    if cc_advantage >= 2:
        insights.append(("good",
            f"Your team has significantly more hard CC ({ally['hard_cc']} vs {enemy['hard_cc']}). "
            "Teamfights where you land your chain CC should be winning fights — "
            "look for grouped enemies."))
    elif cc_advantage <= -2:
        insights.append(("warn",
            f"Enemy has more hard CC ({enemy['hard_cc']} vs {ally['hard_cc']}). "
            "Avoid extended fights where they can chain-CC your team. "
            "Play for picks or split."))

    # --- Frontline comparison ---
    if len(ally["frontline"]) == 0:
        insights.append(("warn",
            "Your team has no frontline. You can't engage into a standard fight — "
            "play for poke, pick, or protect your carry until they're ahead enough to clean up."))
    elif len(ally["frontline"]) >= 3:
        insights.append(("good",
            f"Your team has heavy frontline: {', '.join(ally['frontline'])}. "
            "You can force fights — the enemy has to respect your engage."))

    # --- Enemy frontline vs your damage ---
    if len(enemy["frontline"]) >= 3 and ally["magic_dmg"] + ally["phys_dmg"] <= 2:
        insights.append(("warn",
            "Enemy has a lot of frontline and your team is low on damage. "
            "Long fights favor them — look for burst combos or pick off their carries first."))

    # --- Scaling matchup ---
    if len(enemy["late_scalers"]) >= 3 and len(ally["early_power"]) >= 2:
        insights.append(("good",
            "Your team spikes early, enemy scales late. "
            f"Play aggressive early — force fights before {', '.join(enemy['late_scalers'])} come online."))
    elif len(ally["late_scalers"]) >= 3 and len(enemy["early_power"]) >= 2:
        insights.append(("warn",
            "Enemy has early power, your team scales late. "
            "Survive laning phase without giving up objectives — "
            f"the game swings your way once {', '.join(ally['late_scalers'])} have items."))

    # --- Mobility ---
    if len(enemy["high_mobility"]) >= 3:
        insights.append(("warn",
            f"Enemy has {len(enemy['high_mobility'])} high-mobility champions "
            f"({', '.join(enemy['high_mobility'])}). "
            "CC them before they can dash through your team or escape. "
            "Hard engage without landing CC is suicide."))

    # --- Win condition comparison ---
    win_con_str = ", ".join(set(ally["win_cons"]))
    enemy_wc = ", ".join(set(enemy["win_cons"]))
    insights.append(("info",
        f"Your team's win conditions: [bold]{win_con_str}[/bold]. "
        f"Enemy win conditions: [bold]{enemy_wc}[/bold]."))

    # --- Rakan-specific tips ---
    if my_champ.lower() == "rakan":
        adc_names = [n for n, c in ally["resolved"] if c["cls"] == "marksman"]
        if adc_names:
            adc = resolve(adc_names[0])
            if adc:
                _, adc_data = adc
                if adc_data["mobility"] == "low" and adc_data["scale"] == "late":
                    insights.append(("warn",
                        f"[bold]{adc_names[0]}[/bold] is a late-scaling ADC who needs peel. "
                        "Play near them and use your W to intercept dives, "
                        "not to start fights on the enemy backline."))
                elif adc_data["win_con"] in ("teamfight", "pick") and adc_data["scale"] in ("early", "mid"):
                    insights.append(("good",
                        f"[bold]{adc_names[0]}[/bold] wants to fight early — good Rakan pairing. "
                        "Look for level 2 all-ins and river fights."))

        # Engage buddy?
        engage_allies = [n for n, c in ally["resolved"]
                         if c["win_con"] == "engage" and n.lower() != "rakan"]
        if engage_allies:
            insights.append(("good",
                f"You have engage from {', '.join(engage_allies)} too. "
                "Combo your ults — W in after they land their CC for maximum follow-up."))

        if enemy["assassins"]:
            insights.append(("warn",
                "With assassins in enemy comp: don't use R to initiate — "
                "they'll kill you mid-air. Use R as a follow-up after your team goes in, "
                "or save it to knock assassins off your ADC."))

    return insights


# ---------------------------------------------------------------------------
# Build recommendations
# ---------------------------------------------------------------------------

# Each item: name, cost, slot (mythic/boots/support/flex), description, conditions (all must match), anti (any blocks it)
# Conditions/anti keys: enemy_magic_gte, enemy_phys_gte, enemy_cc_gte, enemy_assassins_gte,
#                       ally_marksman, ally_fighting_adc, ally_engage_buddy,
#                       ally_late_scalers_gte, enemy_late_scalers_gte, always

BUILD_DB: dict[str, list[dict]] = {
    "Rakan": {
        "mythic": [
            dict(name="Locket of the Iron Solari", cost=2500,
                 why="AoE shield protects your whole team when you land in the middle of them",
                 conditions=["enemy_magic_gte_3"],
                 anti=[]),
            dict(name="Shurelya's Battlesong",     cost=2500,
                 why="Movement speed burst lets your entire team follow your engage instantly",
                 conditions=["ally_fighting_adc"],
                 anti=["enemy_magic_gte_3"]),
            dict(name="Shurelya's Battlesong",     cost=2500,
                 why="Default engage mythic — speeds up your team to follow R",
                 conditions=["always"],
                 anti=["enemy_magic_gte_3"]),
            dict(name="Locket of the Iron Solari", cost=2500,
                 why="When in doubt with a mixed/magic-heavy enemy, Locket wins teamfights",
                 conditions=["always"],
                 anti=[]),
        ],
        "boots": [
            dict(name="Mercury's Treads",     cost=1100,
                 why="Tenacity reduces how long you're CCed after your engage — critical vs CC chains",
                 conditions=["enemy_cc_gte_3"],
                 anti=[]),
            dict(name="Plated Steelcaps",     cost=1100,
                 why="Reduces auto-attack and physical burst damage — buy vs heavy AD comps",
                 conditions=["enemy_phys_gte_4"],
                 anti=[]),
            dict(name="Ionian Boots of Lucidity", cost=950,
                 why="Lower cooldowns means more W engages and more R opportunities",
                 conditions=["always"],
                 anti=[]),
        ],
        "support": [
            dict(name="Knight's Vow",         cost=2300,
                 why="Links you to your carry — damage they take is partially redirected to you, keeping them alive",
                 conditions=["ally_marksman"],
                 anti=[]),
            dict(name="Zeke's Convergence",   cost=2400,
                 why="Your R empowers your linked ADC's attacks — massive damage amp on a fighting ADC",
                 conditions=["ally_fighting_adc"],
                 anti=[]),
            dict(name="Redemption",           cost=2300,
                 why="Use it while dead to still impact the fight — great when team needs healing in extended fights",
                 conditions=["always"],
                 anti=[]),
            dict(name="Mikael's Blessing",    cost=2300,
                 why="Cleanses your ADC from one hard CC — near-mandatory vs Thresh, Nautilus, Morgana",
                 conditions=["enemy_single_hard_cc_support"],
                 anti=[]),
        ],
        "flex": [
            dict(name="Warmog's Armor",        cost=3000,
                 why="Makes you unkillable between engages — lets you keep going in when behind",
                 conditions=["enemy_assassins_gte_2"],
                 anti=[]),
            dict(name="Vigilant Wardstone",    cost=1100,
                 why="Late game vision upgrade — doubles your ward limit when you've hit vision milestones",
                 conditions=["always"],
                 anti=[]),
            dict(name="Rabadon's Deathcap",    cost=3600,
                 why="Meme build only — if you're very ahead and want to one-shot with W+R",
                 conditions=["never"],
                 anti=[]),
        ],
    },
    "Milio": {
        "mythic": [
            dict(name="Moonstone Renewer",     cost=2200,
                 why="Sustained healing on your ADC in extended fights — best when protecting a scaling carry",
                 conditions=["ally_peel_adc"],
                 anti=[]),
            dict(name="Echoes of Helia",       cost=2200,
                 why="Healing on every shield/heal you cast — synergizes with your whole kit",
                 conditions=["always"],
                 anti=["ally_peel_adc"]),
            dict(name="Shurelya's Battlesong", cost=2500,
                 why="Speed burst to disengage or help your team chase after Milio R cleanse",
                 conditions=["ally_fighting_adc"],
                 anti=[]),
        ],
        "boots": [
            dict(name="Mercury's Treads",         cost=1100,
                 why="Tenacity means you survive long enough to R cleanse your team",
                 conditions=["enemy_cc_gte_3"],
                 anti=[]),
            dict(name="Ionian Boots of Lucidity", cost=950,
                 why="Lower cooldowns = more shields, more heals, more R casts",
                 conditions=["always"],
                 anti=[]),
        ],
        "support": [
            dict(name="Ardent Censer",         cost=2200,
                 why="Boosts your ADC's attack speed and on-hit healing after you shield them",
                 conditions=["ally_marksman"],
                 anti=[]),
            dict(name="Staff of Flowing Water", cost=2250,
                 why="AP and haste for your whole team when you heal — amplifies Swain/Yasuo/anyone with AP",
                 conditions=["ally_ap_carry"],
                 anti=[]),
            dict(name="Redemption",            cost=2300,
                 why="Use it while dead or from range — strong in extended teamfights",
                 conditions=["always"],
                 anti=[]),
            dict(name="Mikael's Blessing",     cost=2300,
                 why="Cleanse hard CC off your ADC — pairs with your R for double cleanse",
                 conditions=["enemy_single_hard_cc_support"],
                 anti=[]),
        ],
        "flex": [
            dict(name="Vigilant Wardstone",    cost=1100,
                 why="Vision scaling — late game ward limit upgrade",
                 conditions=["always"],
                 anti=[]),
        ],
    },
    "default_support_engage": {
        "mythic": [
            dict(name="Locket of the Iron Solari", cost=2500,
                 why="AoE shield on engage — standard for frontline supports",
                 conditions=["always"], anti=[]),
        ],
        "boots": [
            dict(name="Mercury's Treads",         cost=1100,
                 why="Tenacity vs CC-heavy enemy",
                 conditions=["enemy_cc_gte_3"], anti=[]),
            dict(name="Ionian Boots of Lucidity", cost=950,
                 why="CDR for more abilities",
                 conditions=["always"], anti=[]),
        ],
        "support": [
            dict(name="Knight's Vow",  cost=2300, why="Protect your carry",       conditions=["ally_marksman"], anti=[]),
            dict(name="Redemption",    cost=2300, why="Team healing in teamfights",conditions=["always"],        anti=[]),
        ],
        "flex": [
            dict(name="Vigilant Wardstone", cost=1100, why="Vision scaling", conditions=["always"], anti=[]),
        ],
    },
    "default_support_peel": {
        "mythic": [
            dict(name="Shurelya's Battlesong", cost=2500, why="Disengage and chase tool", conditions=["always"], anti=[]),
        ],
        "boots": [
            dict(name="Mercury's Treads",         cost=1100, why="Tenacity", conditions=["enemy_cc_gte_3"], anti=[]),
            dict(name="Ionian Boots of Lucidity", cost=950,  why="CDR",     conditions=["always"],          anti=[]),
        ],
        "support": [
            dict(name="Ardent Censer", cost=2300, why="Empowers heals/shields on your ADC", conditions=["ally_marksman"], anti=[]),
            dict(name="Redemption",    cost=2300, why="Healing in extended fights",          conditions=["always"],        anti=[]),
        ],
        "flex": [
            dict(name="Vigilant Wardstone", cost=1100, why="Vision scaling", conditions=["always"], anti=[]),
        ],
    },
    "default_mage": {
        "mythic": [
            dict(name="Luden's Tempest",   cost=3200, why="Poke and roam — for mobile or pick mages",    conditions=["always"], anti=[]),
            dict(name="Shadowflame",       cost=3000, why="Burst into shields — good vs heal/shield comp",conditions=["always"], anti=[]),
        ],
        "boots": [
            dict(name="Sorcerer's Shoes",         cost=1100, why="Magic pen — standard for most mages",  conditions=["always"],          anti=[]),
            dict(name="Mercury's Treads",         cost=1100, why="Tenacity vs CC chains",                conditions=["enemy_cc_gte_3"],  anti=[]),
            dict(name="Ionian Boots of Lucidity", cost=950,  why="CDR for ability-spam mages",           conditions=["always"],          anti=[]),
        ],
        "flex": [
            dict(name="Zhonya's Hourglass",  cost=3250, why="Stasis vs assassins or hard engage",        conditions=["enemy_assassins_gte_1"], anti=[]),
            dict(name="Banshee's Veil",      cost=3100, why="Spell shield vs one hard CC that would kill you", conditions=["enemy_cc_gte_3"], anti=[]),
            dict(name="Rabadon's Deathcap",  cost=3600, why="Max AP when ahead — biggest damage spike",  conditions=["always"],               anti=[]),
        ],
    },
    "default_marksman": {
        "mythic": [
            dict(name="Kraken Slayer",   cost=3400, why="True damage shreds tanks — buy vs 2+ frontline",  conditions=["always"], anti=[]),
            dict(name="Galeforce",       cost=3400, why="Dash + burst — good vs immobile targets",         conditions=["always"], anti=[]),
        ],
        "boots": [
            dict(name="Plated Steelcaps", cost=1100, why="Reduces physical burst and auto damage",   conditions=["enemy_assassins_gte_1"], anti=[]),
            dict(name="Berserker's Greaves", cost=1100, why="Attack speed — standard for most ADCs", conditions=["always"],               anti=[]),
        ],
        "flex": [
            dict(name="Guardian Angel", cost=3200, why="Revive when you're the primary target",   conditions=["enemy_assassins_gte_1"], anti=[]),
            dict(name="Bloodthirster",  cost=3400, why="Shield + lifesteal when ahead",           conditions=["always"],               anti=[]),
        ],
    },
}


def _check_conditions(conditions: list[str], anti: list[str], ctx: dict) -> bool:
    if "never" in conditions:
        return False
    for a in anti:
        if ctx.get(a):
            return False
    if "always" in conditions:
        return True
    return all(ctx.get(c) for c in conditions)


def build_context(my_champ: str, ally: dict, enemy: dict) -> dict:
    adc_names = [n for n, c in ally["resolved"] if c["cls"] == "marksman"]
    fighting_adcs = {"Samira", "Draven", "Jinx", "Tristana", "Kaisa"}
    peel_adcs = {"Vayne", "Ezreal", "Aphelios", "Caitlyn"}
    single_cc_supports = {"Thresh", "Nautilus", "Blitzcrank", "Morgana", "Leona"}

    ally_champ_names = {n for n, _ in ally["resolved"]}
    enemy_champ_names = {n for n, _ in enemy["resolved"]}

    return {
        "enemy_magic_gte_3":          enemy["magic_dmg"] >= 3,
        "enemy_magic_gte_4":          enemy["magic_dmg"] >= 4,
        "enemy_phys_gte_3":           enemy["phys_dmg"] >= 3,
        "enemy_phys_gte_4":           enemy["phys_dmg"] >= 4,
        "enemy_cc_gte_3":             (enemy["hard_cc"] + enemy["soft_cc"]) >= 3,
        "enemy_assassins_gte_1":      len(enemy["assassins"]) >= 1,
        "enemy_assassins_gte_2":      len(enemy["assassins"]) >= 2,
        "enemy_late_scalers_gte_3":   len(enemy["late_scalers"]) >= 3,
        "ally_marksman":              bool(adc_names),
        "ally_fighting_adc":          bool(ally_champ_names & fighting_adcs),
        "ally_peel_adc":              bool(ally_champ_names & peel_adcs),
        "ally_engage_buddy":          any(c["win_con"] == "engage" for n, c in ally["resolved"] if n != my_champ),
        "ally_late_scalers_gte_2":    len(ally["late_scalers"]) >= 2,
        "enemy_single_hard_cc_support": bool(enemy_champ_names & single_cc_supports),
    }


def get_champion_build_pool(my_champ: str, ally: dict) -> dict:
    """Return the build pool for the champion, falling back to role defaults."""
    if my_champ in BUILD_DB:
        return BUILD_DB[my_champ]
    mc = resolve(my_champ)
    if mc:
        _, data = mc
        fallback = {
            "support_engage":  "default_support_engage",
            "support_peel":    "default_support_peel",
            "support_utility": "default_support_engage",
            "mage":            "default_mage",
            "marksman":        "default_marksman",
        }.get(data["cls"])
        if fallback and fallback in BUILD_DB:
            return BUILD_DB[fallback]
    return {}


def recommend_build(my_champ: str, ally: dict, enemy: dict) -> dict[str, list[dict]]:
    pool = get_champion_build_pool(my_champ, ally)
    if not pool:
        return {}
    ctx = build_context(my_champ, ally, enemy)
    result = {}
    for slot, items in pool.items():
        picked = [i for i in items if _check_conditions(i["conditions"], i["anti"], ctx)]
        if picked:
            seen = set()
        deduped = []
        for i in picked:
            if i["name"] not in seen:
                seen.add(i["name"])
                deduped.append(i)
        result[slot] = deduped[:2]  # max 2 suggestions per slot
    return result


def print_build(my_champ: str, build: dict[str, list[dict]]):
    if not build:
        console.print(f"[dim]No build data for {my_champ} yet.[/dim]")
        return

    SLOT_LABEL = {
        "mythic": "Mythic",
        "boots": "Boots",
        "support": "Support items",
        "flex": "Flex / situational",
    }
    SLOT_ORDER = ["mythic", "boots", "support", "flex"]

    t = Table(box=box.ROUNDED, show_header=True, title=f"Recommended Build — {my_champ}")
    t.add_column("Slot",   style="bold", min_width=18)
    t.add_column("Item",   style="cyan", min_width=22)
    t.add_column("Cost",   justify="right")
    t.add_column("Why this game", max_width=55)

    for slot in SLOT_ORDER:
        if slot not in build:
            continue
        items = build[slot]
        label = SLOT_LABEL.get(slot, slot)
        for i, item in enumerate(items):
            slot_cell = f"[bold]{label}[/bold]" if i == 0 else "[dim]or[/dim]"
            t.add_row(slot_cell, item["name"], f"{item['cost']:,}g", item["why"])

    console.print(t)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

SCALE_COLOR = {"early": "red", "mid": "yellow", "late": "green"}
WC_COLOR = {
    "engage": "red", "pick": "magenta", "teamfight": "yellow",
    "poke": "blue", "peel": "cyan", "splitpush": "green",
    "scale": "green", "unknown": "white",
}

def comp_table(title: str, comp: dict, border_color: str) -> str:
    """Return a Rich table as a Panel."""
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("Champion", style="cyan", min_width=14)
    t.add_column("Class")
    t.add_column("Dmg")
    t.add_column("Hard CC", justify="center")
    t.add_column("Mobility", justify="center")
    t.add_column("Spike")
    t.add_column("Win con")

    for name, c in comp["resolved"]:
        hcc = "[green]✓[/green]" if c["hard_cc"] else "[dim]—[/dim]"
        mob_color = {"high": "green", "med": "yellow", "low": "red"}[c["mobility"]]
        sc = c["scale"]
        wc = c["win_con"]
        t.add_row(
            name, c["cls"], c["dmg"], hcc,
            f"[{mob_color}]{c['mobility']}[/{mob_color}]",
            f"[{SCALE_COLOR[sc]}]{sc}[/{SCALE_COLOR[sc]}]",
            f"[{WC_COLOR.get(wc, 'white')}]{wc}[/{WC_COLOR.get(wc, 'white')}]",
        )

    summary = (
        f"Hard CC: [bold]{comp['hard_cc']}[/bold]   "
        f"Frontline: [bold]{len(comp['frontline'])}[/bold]   "
        f"Assassins: [bold]{len(comp['assassins'])}[/bold]   "
        f"Win con: [bold]{comp['primary_win_con']}[/bold]"
    )
    if comp["unknown"]:
        summary += f"\n[yellow]Unknown: {', '.join(comp['unknown'])}[/yellow]"

    from rich.console import Group
    from rich.text import Text
    return Panel(Group(t, Text.from_markup(summary)), title=f"[bold]{title}[/bold]", border_style=border_color)


def print_insights(insights: list[tuple[str, str]]):
    ICONS = {"good": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "info": "[blue]→[/blue]"}
    lines = [f"{ICONS.get(lvl, '·')}  {msg}" for lvl, msg in insights]
    console.print(Panel(
        "\n\n".join(lines),
        title="[bold]Matchup Analysis[/bold]",
        border_style="magenta",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Champion ID → name mapping (Data Dragon)
# ---------------------------------------------------------------------------

_champ_id_map: dict[int, str] = {}

def _load_champ_id_map():
    global _champ_id_map
    if _champ_id_map:
        return
    try:
        versions = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5).json()
        latest = versions[0]
        data = requests.get(
            f"https://ddragon.leagueoflegends.com/cdn/{latest}/data/en_US/champion.json",
            timeout=10,
        ).json()
        _champ_id_map = {int(v["key"]): v["name"] for v in data["data"].values()}
    except Exception as e:
        console.print(f"[yellow]Could not load champion map: {e}[/yellow]")


def champ_name_from_id(champ_id: int) -> str:
    _load_champ_id_map()
    return _champ_id_map.get(champ_id, f"Unknown({champ_id})")


# ---------------------------------------------------------------------------
# Pull comp from live game
# ---------------------------------------------------------------------------

def comps_from_live_game(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)

    acct = get_account(game_name, tag)
    puuid = acct["puuid"]

    url = f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}"
    try:
        data = _get(url)
    except Exception as e:
        if "404" in str(e):
            console.print("[red]You're not currently in a game.[/red]")
        else:
            console.print(f"[red]Spectator API error: {e}[/red]")
        sys.exit(1)

    _load_champ_id_map()

    my_team_id = next(p["teamId"] for p in data["participants"] if p["puuid"] == puuid)
    my_champ   = champ_name_from_id(next(p["championId"] for p in data["participants"] if p["puuid"] == puuid))

    allies, enemies = [], []
    for p in data["participants"]:
        name = champ_name_from_id(p["championId"])
        if p["puuid"] == puuid:
            continue
        if p["teamId"] == my_team_id:
            allies.append(name)
        else:
            enemies.append(name)

    return my_champ, allies, enemies


# ---------------------------------------------------------------------------
# Pull comp from last game
# ---------------------------------------------------------------------------

def comps_from_last_game(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)

    from riftlab.riot import get_account, get_match_ids, get_match, extract_participant
    acct = get_account(game_name, tag)
    puuid = acct["puuid"]
    mid = get_match_ids(puuid, count=1)[0]
    match = get_match(mid)

    p = extract_participant(match, puuid)
    my_champ = p["championName"]
    my_team_id = p["teamId"]

    allies, enemies = [], []
    for participant in match["info"]["participants"]:
        name = participant["championName"]
        if participant["teamId"] == my_team_id:
            if participant["puuid"] != puuid:
                allies.append(name)
        else:
            enemies.append(name)

    return my_champ, allies, enemies


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Comp check — your team vs enemy team")
    parser.add_argument("champion", nargs="?", help="Your champion")
    parser.add_argument("--ally",   help='Your 4 teammates, comma-separated: "Vayne,Orianna,JarvanIV,Mordekaiser"')
    parser.add_argument("--enemy",  help='Enemy team, comma-separated: "Fizz,Khazix,Tryndamere,Jhin,Nautilus"')
    parser.add_argument("--from-last", metavar="ACCOUNT", choices=list(ACCOUNTS.keys()),
                        help="Pull comps automatically from last ranked game")
    parser.add_argument("--live", metavar="ACCOUNT", choices=list(ACCOUNTS.keys()),
                        help="Pull comps from your current live game")
    args = parser.parse_args()

    if args.live:
        console.print(f"\n[bold]Fetching live game for [cyan]{ACCOUNTS[args.live]}[/cyan]...[/bold]\n")
        my_champ, ally_names, enemy_names = comps_from_live_game(args.live)
        console.print(f"[dim]Your champion: {my_champ}[/dim]")
        console.print(f"[dim]Allies: {', '.join(ally_names)}[/dim]")
        console.print(f"[dim]Enemies: {', '.join(enemy_names)}[/dim]\n")
    elif args.from_last:
        console.print(f"\n[bold]Fetching last game for [cyan]{ACCOUNTS[args.from_last]}[/cyan]...[/bold]\n")
        my_champ, ally_names, enemy_names = comps_from_last_game(args.from_last)
        console.print(f"[dim]Your champion: {my_champ}[/dim]")
        console.print(f"[dim]Allies: {', '.join(ally_names)}[/dim]")
        console.print(f"[dim]Enemies: {', '.join(enemy_names)}[/dim]\n")
    else:
        if not args.champion or not args.ally or not args.enemy:
            parser.error("Provide --champion, --ally, and --enemy, or use --from-last ACCOUNT")
        my_champ = args.champion
        ally_names = [n.strip() for n in args.ally.split(",")]
        enemy_names = [n.strip() for n in args.enemy.split(",")]

    all_ally_names = [my_champ] + ally_names
    ally_comp  = analyze_comp(all_ally_names)
    enemy_comp = analyze_comp(enemy_names)

    console.print(comp_table("Your Team", ally_comp, "green"))
    console.print(comp_table("Enemy Team", enemy_comp, "red"))

    insights = generate_matchup_insights(my_champ, ally_comp, enemy_comp)
    print_insights(insights)

    build = recommend_build(my_champ, ally_comp, enemy_comp)
    print_build(my_champ, build)


if __name__ == "__main__":
    main()
