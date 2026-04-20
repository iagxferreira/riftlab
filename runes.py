"""
runes.py — Recommended rune pages based on your champion and enemy comp.

Usage:
  python runes.py main           # pull from live game
  python runes.py main --last    # pull from last game
  python runes.py Rakan --enemy "Nautilus,Caitlyn,Yasuo,Zed,Viktor"  # manual
"""

import sys
import argparse
import time

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

from lol_stats import get_account, get_match_ids, get_match, extract_participant, BASE_SUMMONER, _get, ACCOUNTS
from comp_check import analyze_comp, resolve, champ_name_from_id, _load_champ_id_map

load_dotenv()
console = Console()

# ---------------------------------------------------------------------------
# Rune definitions (ID + metadata)
# ---------------------------------------------------------------------------

RUNES = {
    # Resolve
    8437: dict(name="Grasp of the Undying", tree="Resolve",     slot="keystone"),
    8439: dict(name="Aftershock",           tree="Resolve",     slot="keystone"),
    8465: dict(name="Guardian",             tree="Resolve",     slot="keystone"),
    8446: dict(name="Demolish",             tree="Resolve",     slot="row1"),
    8463: dict(name="Font of Life",         tree="Resolve",     slot="row1"),
    8401: dict(name="Shield Bash",          tree="Resolve",     slot="row1"),
    8429: dict(name="Conditioning",         tree="Resolve",     slot="row2"),
    8444: dict(name="Second Wind",          tree="Resolve",     slot="row2"),
    8473: dict(name="Bone Plating",         tree="Resolve",     slot="row2"),
    8451: dict(name="Overgrowth",           tree="Resolve",     slot="row3"),
    8453: dict(name="Revitalize",           tree="Resolve",     slot="row3"),
    8242: dict(name="Unflinching",          tree="Resolve",     slot="row3"),
    # Sorcery
    8214: dict(name="Summon Aery",          tree="Sorcery",     slot="keystone"),
    8229: dict(name="Arcane Comet",         tree="Sorcery",     slot="keystone"),
    8230: dict(name="Phase Rush",           tree="Sorcery",     slot="keystone"),
    8226: dict(name="Manaflow Band",        tree="Sorcery",     slot="row1"),
    8275: dict(name="Nimbus Cloak",         tree="Sorcery",     slot="row1"),
    8224: dict(name="Axiom Arcanist",       tree="Sorcery",     slot="row1"),
    8210: dict(name="Transcendence",        tree="Sorcery",     slot="row2"),
    8234: dict(name="Celerity",             tree="Sorcery",     slot="row2"),
    8233: dict(name="Absolute Focus",       tree="Sorcery",     slot="row2"),
    8237: dict(name="Scorch",               tree="Sorcery",     slot="row3"),
    8232: dict(name="Waterwalking",         tree="Sorcery",     slot="row3"),
    8236: dict(name="Gathering Storm",      tree="Sorcery",     slot="row3"),
    # Inspiration
    8351: dict(name="Glacial Augment",      tree="Inspiration", slot="keystone"),
    8360: dict(name="Unsealed Spellbook",   tree="Inspiration", slot="keystone"),
    8369: dict(name="First Strike",         tree="Inspiration", slot="keystone"),
    8306: dict(name="Hextech Flashtraption",tree="Inspiration", slot="row1"),
    8304: dict(name="Magical Footwear",     tree="Inspiration", slot="row1"),
    8321: dict(name="Cash Back",            tree="Inspiration", slot="row1"),
    8313: dict(name="Triple Tonic",         tree="Inspiration", slot="row2"),
    8352: dict(name="Time Warp Tonic",      tree="Inspiration", slot="row2"),
    8345: dict(name="Biscuit Delivery",     tree="Inspiration", slot="row2"),
    8347: dict(name="Cosmic Insight",       tree="Inspiration", slot="row3"),
    8410: dict(name="Approach Velocity",    tree="Inspiration", slot="row3"),
    8316: dict(name="Jack Of All Trades",   tree="Inspiration", slot="row3"),
    # Precision
    8005: dict(name="Press the Attack",     tree="Precision",   slot="keystone"),
    8008: dict(name="Lethal Tempo",         tree="Precision",   slot="keystone"),
    8021: dict(name="Fleet Footwork",       tree="Precision",   slot="keystone"),
    8010: dict(name="Conqueror",            tree="Precision",   slot="keystone"),
    9101: dict(name="Absorb Life",          tree="Precision",   slot="row1"),
    9111: dict(name="Triumph",              tree="Precision",   slot="row1"),
    8009: dict(name="Presence of Mind",     tree="Precision",   slot="row1"),
    9104: dict(name="Legend: Alacrity",     tree="Precision",   slot="row2"),
    9105: dict(name="Legend: Haste",        tree="Precision",   slot="row2"),
    9103: dict(name="Legend: Bloodline",    tree="Precision",   slot="row2"),
    8014: dict(name="Coup de Grace",        tree="Precision",   slot="row3"),
    8017: dict(name="Cut Down",             tree="Precision",   slot="row3"),
    8299: dict(name="Last Stand",           tree="Precision",   slot="row3"),
    # Domination
    8112: dict(name="Electrocute",          tree="Domination",  slot="keystone"),
    8128: dict(name="Dark Harvest",         tree="Domination",  slot="keystone"),
    9923: dict(name="Hail of Blades",       tree="Domination",  slot="keystone"),
    8126: dict(name="Cheap Shot",           tree="Domination",  slot="row1"),
    8139: dict(name="Taste of Blood",       tree="Domination",  slot="row1"),
    8143: dict(name="Sudden Impact",        tree="Domination",  slot="row1"),
    8137: dict(name="Sixth Sense",          tree="Domination",  slot="row2"),
    8140: dict(name="Grisly Mementos",      tree="Domination",  slot="row2"),
    8141: dict(name="Deep Ward",            tree="Domination",  slot="row2"),
    8135: dict(name="Treasure Hunter",      tree="Domination",  slot="row3"),
    8105: dict(name="Relentless Hunter",    tree="Domination",  slot="row3"),
    8106: dict(name="Ultimate Hunter",      tree="Domination",  slot="row3"),
}

# ---------------------------------------------------------------------------
# Rune page database — keyed by champion + scenario
# ---------------------------------------------------------------------------

# Each page: primary tree, keystone, rows 1-3, secondary tree, 2 secondaries, shards
# shards: [offense, flex, defense] — common values: "AS"/"AD"/"AP"/"AH"/"Armor"/"MR"/"HP"

RUNE_PAGES: dict[str, list[dict]] = {
    "Rakan": [
        dict(
            name="Standard Engage",
            scenario="Default page — use in most games",
            conditions=["always"],
            anti=[],
            primary="Resolve",
            keystone=8439,    # Aftershock
            row1=8463,        # Font of Life
            row2=8473,        # Bone Plating
            row3=8453,        # Revitalize
            secondary="Sorcery",
            sec1=8234,        # Celerity
            sec2=8232,        # Waterwalking
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Aftershock — massive armor/MR buff when you land W, lets you survive inside the enemy team after R",
                "row1":     "Font of Life — heals your ADC every time you CC an enemy, constant passive sustain",
                "row2":     "Bone Plating — reduces burst from the first 3 hits after you engage, keeps you alive",
                "row3":     "Revitalize — amplifies all heals and shields including your W and Redemption",
                "secondary":"Sorcery for extra mobility",
                "sec1":     "Celerity — more speed = more W range, more engage opportunities",
                "sec2":     "Waterwalking — speed in river is free roam power on Rakan",
                "shards":   "Double AH for more W/R casts, Armor to survive the early lane",
            }
        ),
        dict(
            name="Guardian (Peel ADC)",
            scenario="ADC is Vayne, Ezreal, Jhin or other peel-dependent carries",
            conditions=["peel_adc"],
            anti=[],
            primary="Resolve",
            keystone=8465,    # Guardian
            row1=8463,        # Font of Life
            row2=8473,        # Bone Plating
            row3=8453,        # Revitalize
            secondary="Inspiration",
            sec1=8345,        # Biscuit Delivery
            sec2=8347,        # Cosmic Insight
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Guardian — shared shield with your ADC when you're near them, protects them from burst without you having to react",
                "row1":     "Font of Life — heals your ADC passively when you peel for them",
                "row2":     "Bone Plating — reduces the burst that hits your ADC when you body-block",
                "row3":     "Revitalize — amplifies Guardian shield and all heals",
                "secondary":"Inspiration for lane sustain and CDR",
                "sec1":     "Biscuit Delivery — sustain through the poke lanes that bully peel ADCs",
                "sec2":     "Cosmic Insight — lower summoner spell CD means more Flash/Ignite plays",
                "shards":   "Double AH to peel faster, Armor for laning phase",
            }
        ),
        dict(
            name="Aftershock + Inspiration (Long game)",
            scenario="Enemy has heavy CC chain or game will go late (Seraphine, Sona, Azir comps)",
            conditions=["heavy_cc_enemy", "late_game_enemy"],
            anti=[],
            primary="Resolve",
            keystone=8439,    # Aftershock
            row1=8463,        # Font of Life
            row2=8444,        # Second Wind
            row3=8453,        # Revitalize
            secondary="Inspiration",
            sec1=8345,        # Biscuit Delivery
            sec2=8347,        # Cosmic Insight
            shards=["AH", "AH", "MR"],
            why={
                "keystone": "Aftershock — survive inside their peel/CC long enough for your team to follow",
                "row1":     "Font of Life — passive healing even in poke-heavy games",
                "row2":     "Second Wind — sustain through poke in long laning phases",
                "row3":     "Revitalize — amplifies Second Wind and all heals/shields",
                "secondary":"Inspiration for extra survivability over time",
                "sec1":     "Biscuit Delivery — sustain through long poky laning phases",
                "sec2":     "Cosmic Insight — lower CD on Shurelya's and Knight's Vow actives",
                "shards":   "Double AH, MR instead of Armor vs magic-heavy poke",
            }
        ),
        dict(
            name="Summon Aery (Aggressive lane)",
            scenario="Fighting ADC (Draven, Samira, Jinx) — look to dominate early",
            conditions=["fighting_adc"],
            anti=["heavy_cc_enemy"],
            primary="Sorcery",
            keystone=8214,    # Summon Aery
            row1=8226,        # Manaflow Band
            row2=8234,        # Celerity
            row3=8237,        # Scorch
            secondary="Resolve",
            sec1=8473,        # Bone Plating
            sec2=8453,        # Revitalize
            shards=["AH", "AH", "Armor"],
            why={
                "keystone": "Summon Aery — pokes with every W and auto, extra damage in early trades and also shields allies",
                "row1":     "Manaflow Band — restores mana when you poke, keeps you from going oom in aggressive early games",
                "row2":     "Celerity — more movement speed = better W positioning in lane",
                "row3":     "Scorch — extra burn damage on your poke, strong in early all-ins",
                "secondary":"Resolve for durability after you engage",
                "sec1":     "Bone Plating — survive the first burst from their ADC after diving",
                "sec2":     "Revitalize — amplifies W shield value",
                "shards":   "Double AH for more W pokes, Armor to trade in lane",
            }
        ),
    ]
}


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def build_rune_context(ally_comp: dict, enemy_comp: dict) -> dict:
    fighting_adcs = {"Samira", "Draven", "Jinx", "Tristana", "Kaisa"}
    peel_adcs     = {"Vayne", "Ezreal", "Aphelios", "Caitlyn", "Jhin"}
    ally_names    = {n for n, _ in ally_comp["resolved"]}

    return {
        "always":           True,
        "fighting_adc":     bool(ally_names & fighting_adcs),
        "peel_adc":         bool(ally_names & peel_adcs),
        "heavy_cc_enemy":   (enemy_comp["hard_cc"] + enemy_comp["soft_cc"]) >= 4,
        "late_game_enemy":  len(enemy_comp["late_scalers"]) >= 2,
        "assassin_enemy":   len(enemy_comp["assassins"]) >= 1,
        "magic_heavy":      enemy_comp["magic_dmg"] >= 3,
        "physical_heavy":   enemy_comp["phys_dmg"] >= 3,
    }


def pick_rune_page(champion: str, ctx: dict) -> dict | None:
    pages = RUNE_PAGES.get(champion, [])
    for page in pages:
        if any(ctx.get(a) for a in page["anti"]):
            continue
        if "always" in page["conditions"] or all(ctx.get(c) for c in page["conditions"]):
            return page
    # Fallback to first page
    return pages[0] if pages else None


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

TREE_COLOR = {
    "Resolve":     "green",
    "Sorcery":     "blue",
    "Inspiration": "cyan",
    "Precision":   "yellow",
    "Domination":  "red",
}

def rune_name(rune_id: int) -> str:
    return RUNES.get(rune_id, {}).get("name", f"Unknown({rune_id})")

def print_rune_page(page: dict, champion: str):
    tree_color = TREE_COLOR.get(page["primary"], "white")
    sec_color  = TREE_COLOR.get(page["secondary"], "white")

    lines = [
        f"[bold]Page:[/bold] {page['name']}",
        f"[bold]When:[/bold] {page['scenario']}",
        "",
        f"[{tree_color}][bold]PRIMARY — {page['primary']}[/bold][/{tree_color}]",
        f"  Keystone : [bold]{rune_name(page['keystone'])}[/bold]",
        f"             [dim]{page['why']['keystone']}[/dim]",
        f"  Row 1    : [bold]{rune_name(page['row1'])}[/bold]",
        f"             [dim]{page['why']['row1']}[/dim]",
        f"  Row 2    : [bold]{rune_name(page['row2'])}[/bold]",
        f"             [dim]{page['why']['row2']}[/dim]",
        f"  Row 3    : [bold]{rune_name(page['row3'])}[/bold]",
        f"             [dim]{page['why']['row3']}[/dim]",
        "",
        f"[{sec_color}][bold]SECONDARY — {page['secondary']}[/bold][/{sec_color}]",
        f"  {rune_name(page['sec1'])} + {rune_name(page['sec2'])}",
        f"  [dim]{page['why']['secondary']} — {page['why']['sec1']} / {page['why']['sec2']}[/dim]",
        "",
        f"[bold]SHARDS:[/bold] {' | '.join(page['shards'])}",
        f"[dim]{page['why']['shards']}[/dim]",
    ]

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Recommended Runes — {champion}[/bold]",
        border_style=tree_color,
        padding=(1, 2),
    ))


def print_all_pages(champion: str):
    """Show all available pages for reference."""
    pages = RUNE_PAGES.get(champion, [])
    if not pages:
        console.print(f"[yellow]No rune pages defined for {champion}.[/yellow]")
        return

    t = Table(title=f"All Rune Pages — {champion}", box=box.ROUNDED)
    t.add_column("Page",      style="cyan", min_width=28)
    t.add_column("Keystone",  min_width=22)
    t.add_column("Secondary", min_width=12)
    t.add_column("When to use")

    for p in pages:
        t.add_row(
            p["name"],
            rune_name(p["keystone"]),
            p["secondary"],
            p["scenario"],
        )
    console.print(t)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_comp_from_live(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    try:
        data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}")
    except Exception as e:
        if "404" in str(e):
            console.print("[red]Not currently in a game.[/red]")
        else:
            console.print(f"[red]API error: {e}[/red]")
        sys.exit(1)

    _load_champ_id_map()
    my_team_id = next(p["teamId"] for p in data["participants"] if p["puuid"] == puuid)
    my_champ   = champ_name_from_id(next(p["championId"] for p in data["participants"] if p["puuid"] == puuid))
    allies, enemies = [], []
    for p in data["participants"]:
        name = champ_name_from_id(p["championId"])
        if p["puuid"] == puuid: continue
        if p["teamId"] == my_team_id: allies.append(name)
        else: enemies.append(name)

    return my_champ, allies, enemies


def get_comp_from_last(account_label: str) -> tuple[str, list[str], list[str]]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]
    mid   = get_match_ids(puuid, count=1)[0]
    match = get_match(mid)
    p     = extract_participant(match, puuid)
    my_champ   = p["championName"]
    my_team_id = p["teamId"]
    allies, enemies = [], []
    for part in match["info"]["participants"]:
        name = part["championName"]
        if part["puuid"] == puuid: continue
        if part["teamId"] == my_team_id: allies.append(name)
        else: enemies.append(name)
    return my_champ, allies, enemies


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Rune page recommendations")
    parser.add_argument("account_or_champ", help="Account label (main/lab) or champion name (manual mode)")
    parser.add_argument("--last",    action="store_true", help="Use last game instead of live")
    parser.add_argument("--all",     action="store_true", help="Show all available pages for the champion")
    parser.add_argument("--ally",    help="Allies (manual): 'Jinx,Yasuo,Garen,Viego'")
    parser.add_argument("--enemy",   help="Enemies (manual): 'Nautilus,Caitlyn,Zed,Morgana,Viktor'")
    args = parser.parse_args()

    # Determine mode
    if args.account_or_champ in ACCOUNTS:
        account_label = args.account_or_champ
        if args.last:
            console.print("[dim]Fetching last game...[/dim]")
            my_champ, ally_names, enemy_names = get_comp_from_last(account_label)
        else:
            console.print("[dim]Fetching live game...[/dim]")
            my_champ, ally_names, enemy_names = get_comp_from_live(account_label)
    else:
        # Manual mode
        if not args.ally or not args.enemy:
            parser.error("Manual mode requires --ally and --enemy")
        my_champ    = args.account_or_champ
        ally_names  = [n.strip() for n in args.ally.split(",")]
        enemy_names = [n.strip() for n in args.enemy.split(",")]

    console.print(f"\n[bold]Champion:[/bold] [cyan]{my_champ}[/cyan]")
    console.print(f"[dim]Allies:  {', '.join(ally_names)}[/dim]")
    console.print(f"[dim]Enemies: {', '.join(enemy_names)}[/dim]\n")

    if args.all:
        print_all_pages(my_champ)
        return

    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp(enemy_names)
    ctx        = build_rune_context(ally_comp, enemy_comp)

    page = pick_rune_page(my_champ, ctx)
    if not page:
        console.print(f"[yellow]No rune pages defined for {my_champ} yet.[/yellow]")
        return

    print_rune_page(page, my_champ)

    # Show other available pages as reference
    other_pages = [p for p in RUNE_PAGES.get(my_champ, []) if p["name"] != page["name"]]
    if other_pages:
        console.print()
        t = Table(title="Other available pages", box=box.SIMPLE)
        t.add_column("Page",     style="dim cyan")
        t.add_column("Keystone", style="dim")
        t.add_column("When")
        for p in other_pages:
            t.add_row(p["name"], rune_name(p["keystone"]), p["scenario"])
        console.print(t)


if __name__ == "__main__":
    main()
