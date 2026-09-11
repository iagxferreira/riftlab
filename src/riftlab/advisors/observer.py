"""
observer.py — Live game observer. Runs pregame then fires phase reports
at ~5 min (early), ~14 min (mid), ~25 min (late).

Usage:
  python -m riftlab.advisors.observer main
  python -m riftlab.advisors.observer main --enemy-items "Gnar:Trinity+Steelcaps,Jinx:Kraken"
"""

import time
import argparse

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

from riftlab.riot import get_account, BASE_SUMMONER, _get, ACCOUNTS
from riftlab.advisors.comp_check import analyze_comp, champ_name_from_id, _load_champ_id_map
from riftlab.advisors.build_advisor import (
    fetch_live_data, aggregate_threats,
    get_adaptive_recommendations, print_recommendations,
    KEYSTONES, HEALING_CHAMPS, SHIELD_CHAMPS,
)
from riftlab.advisors.pregame import (
    get_recent_form, print_recent_form,
    generate_loading_tips, print_loading_tips,
    print_bans, recommend_bans,
)
from riftlab.advisors.champ_select import print_champion_pick
from riftlab.advisors.runes import build_rune_context, pick_rune_page, print_rune_page

load_dotenv()
console = Console()

# Phase thresholds in seconds
PHASE_EARLY = 5  * 60   # 5 min
PHASE_MID   = 14 * 60   # 14 min
PHASE_LATE  = 25 * 60   # 25 min
POLL_INTERVAL = 20       # seconds between spectator polls


# ---------------------------------------------------------------------------
# Spectator helpers
# ---------------------------------------------------------------------------

def get_game_time(puuid: str) -> int | None:
    """Return current game length in seconds, or None if game not found."""
    try:
        data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}")
        return data.get("gameLength", 0)
    except Exception:
        return None


def get_live_game_data(puuid: str) -> dict | None:
    try:
        return _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{puuid}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Expected item builds per role/class at each timing
# ---------------------------------------------------------------------------

EXPECTED_ITEMS = {
    # (class, phase) -> expected core items  [patch 16.8.1]
    ("tank",              "mid"):  ["Sunfire Aegis or Heartsteel", "Jak'Sho or Iceborn Gauntlet", "Steelcaps or Mercs"],
    ("tank",              "late"): ["Randuin's Omen / Thornmail / Hollow Radiance"],
    ("fighter",           "mid"):  ["Trinity Force or Stridebreaker", "Black Cleaver or Death's Dance"],
    ("fighter",           "late"): ["Sterak's Gage", "Overlord's Bloodmail or Spear of Shojin"],
    ("assassin",          "mid"):  ["Eclipse or Youmuu's Ghostblade", "Edge of Night or Serpent's Fang"],
    ("assassin",          "late"): ["Profane Hydra", "Opportunity or Axiom Arc"],
    ("mage",              "mid"):  ["Shadowflame or Luden's Echo", "Sorcerer's Shoes", "Cryptbloom or Void Staff"],
    ("mage",              "late"): ["Rabadon's Deathcap", "Zhonya's Hourglass"],
    ("marksman",          "mid"):  ["Kraken Slayer or Galeforce", "Berserker's Greaves"],
    ("marksman",          "late"): ["Infinity Edge", "Lord Dominik's / Mortal Reminder"],
    ("support_engage",    "mid"):  ["Locket or Shurelya's", "Knight's Vow"],
    ("support_engage",    "late"): ["Zeke's Convergence", "Bandlepipes or Abyssal Mask"],
    ("support_peel",      "mid"):  ["Moonstone Renewer or Echoes of Helia", "Redemption or Ardent Censer"],
    ("support_peel",      "late"): ["Staff of Flowing Water", "Mikael's Blessing"],
    ("support_utility",   "mid"):  ["Imperial Mandate or Shurelya's", "Redemption"],
}

# Champion-specific overrides — wins over class lookup
EXPECTED_ITEMS_CHAMP = {
    ("Ekko", "mid"):  ["Shadowflame or Luden's", "Sorcerer's Shoes", "Zhonya's Hourglass"],
    ("Ekko", "late"): ["Rabadon's Deathcap", "Void Staff or Cryptbloom"],
}

def expected_build(cls: str, phase: str, champ: str = "") -> str:
    override = EXPECTED_ITEMS_CHAMP.get((champ, phase))
    if override:
        return ", ".join(override)
    return ", ".join(EXPECTED_ITEMS.get((cls, phase), ["—"]))


# ---------------------------------------------------------------------------
# Objective advice based on comp
# ---------------------------------------------------------------------------

def objective_focus(ally_comp: dict, enemy_comp: dict, phase: str) -> list[str]:
    tips = []
    ally_wc  = ally_comp["primary_win_con"]
    enemy_wc = enemy_comp["primary_win_con"]

    if phase == "early":
        tips.append("First dragon spawns at 5:00 — contest it if your jungler has priority")
        if ally_wc in ("engage", "teamfight"):
            tips.append("Your comp wants early skirmishes — look for a 2v2 or 3v3 around drake")
        elif ally_wc in ("scale",):
            tips.append("Let dragon go if it costs a death — your power comes later")
        if len(enemy_comp["late_scalers"]) >= 2:
            tips.append("Herald at 8 min is critical — take tower gold before they outscale you")

    elif phase == "mid":
        tips.append("Dragon soul starts mattering — prioritize 3rd dragon heavily")
        if ally_wc in ("engage", "teamfight"):
            tips.append("Force 5v5 around objectives — you win grouped fights, don't split up")
        elif ally_wc == "pick":
            tips.append("Look for a pick before baron/dragon spawns, then convert to objective")
        elif ally_wc == "splitpush":
            tips.append("Keep one sidelane pressured — don't group 5 when you have a split threat")
        if enemy_wc == "scale":
            tips.append("They scale — take any tier-2 tower you can before 20 min")
        if len(enemy_comp["late_scalers"]) >= 2:
            tips.append("Avoid baron before 25 if they have 3+ items — fight them away from it")

    elif phase == "late":
        tips.append("Baron is the game-ending objective — only contest with full vision control")
        if ally_wc in ("engage", "teamfight"):
            tips.append("Look for a 5v5 teamfight near baron, win it, then take baron free")
        elif ally_wc == "pick":
            tips.append("One pick near baron = free baron. Don't group until you get one")
        if len(ally_comp["late_scalers"]) >= 2:
            tips.append("You scale — if behind, stall for elder dragon and use it to teamfight")
        else:
            tips.append("You don't scale — be aggressive with baron bounties, end it now")

    return tips


# ---------------------------------------------------------------------------
# Phase reports
# ---------------------------------------------------------------------------

def phase_report(
    phase: str,
    my_champ: str,
    ally_comp: dict,
    enemy_comp: dict,
    enemy_champs: list,
    keystones: dict,
    manual_items: dict,
    game_time_s: int,
):
    minutes = game_time_s // 60
    phase_labels = {"early": "Early Game (5 min)", "mid": "Mid Game (14 min)", "late": "Late Game (25 min+)"}
    console.print()
    console.print(Rule(f"[bold yellow]⏱  {phase_labels[phase]} — {minutes}m into game[/bold yellow]"))
    console.print()

    # Objective focus
    objs = objective_focus(ally_comp, enemy_comp, phase)
    obj_lines = "\n".join(f"  [cyan]→[/cyan]  {o}" for o in objs)
    console.print(Panel(obj_lines, title="[bold]Objective Focus[/bold]", border_style="yellow", padding=(1, 2)))

    # Enemy expected state
    if phase in ("mid", "late"):
        t = Table(title="Enemy Expected Builds", box=box.SIMPLE, show_header=True)
        t.add_column("Champion",  style="cyan")
        t.add_column("Class")
        t.add_column("Expected items by now")
        t.add_column("Watch for")

        for name, data in enemy_champs:
            if not data:
                continue
            cls   = data.get("cls", "—")
            exp   = expected_build(cls, phase, champ=name)
            ks_id = keystones.get(name, 0)
            ks    = KEYSTONES.get(ks_id, {})
            warns = []
            if name in HEALING_CHAMPS or ks.get("healing"): warns.append("[red]healing[/red]")
            if name in SHIELD_CHAMPS:                        warns.append("[blue]shields[/blue]")
            if ks.get("burst"):                              warns.append("[magenta]burst[/magenta]")
            if ks.get("on_hit"):                             warns.append("[green]on-hit[/green]")
            t.add_row(name, cls, exp, ", ".join(warns) or "—")
        console.print(t)

    # Adaptive build update
    threats = aggregate_threats(enemy_champs, keystones, manual_items)
    threats["names_with_shields"] = []
    recommendations = get_adaptive_recommendations(my_champ, threats, manual_items)

    if manual_items:
        console.print(Rule("[bold]Updated Build (with enemy items)[/bold]"))
        print_recommendations(my_champ, recommendations)

    # Phase-specific champion tips
    PHASE_TIPS = {
        "Rakan": {
            "early": [
                "Roam mid after level 3 if bot lane has priority — your W range covers the whole lane",
                "Don't use R unless you're sure you hit 3+ people or saving someone from death",
                "Knight's Vow on your carry as soon as you recall",
            ],
            "mid": [
                "Stay grouped — your R is only impactful in 5v5, not skirmishes",
                "Locket active the instant R lands — don't wait to see if they're in range",
                "Ward enemy jungle entrances before every objective fight",
            ],
            "late": [
                "In late game teamfights, W is a save tool first, engage tool second",
                "One bad R misclick ends the game — only ult when you'll hit 3+",
                "You should have full items — if you don't, prioritize completing over upgrading",
            ],
        },
        "Ekko": {
            "early": [
                "Farm to level 6 before forcing anything — your kill pattern needs R as backup",
                "AA between every spell to keep Z-Drive passive stacking (every 3rd hit = bonus damage + slow)",
                "W bubble: drop it WHERE they're running to, not where they are — they'll walk into it",
                "First roam window opens at level 6 with ult up — not before unless you're snowballing",
            ],
            "mid": [
                "Carry loop: push wave → roam → burn Flash on one kill → back → buy → repeat every 3 min",
                "R is your safety net — go for dives you wouldn't take without it. If it goes wrong, rewind",
                "Enemy grouping for dragon? W bubble hits multiple people — Flash into their backline, W, burst carry, R out",
                "Every kill/assist stacks Dark Harvest — prioritize being in the fight even if you don't get the kill",
            ],
            "late": [
                "Teamfight pattern: flank from the side → E onto their carry → W bubble on grouped enemies → burst → R if needed",
                "Zhonya's active + R = unkillable combo. Use Hourglass to bait their abilities, then rewind before it ends",
                "W bubble at baron/drag pit is lethal — they're grouped and can't dodge it. Wait for the cluster, then go in",
                "If behind: don't 1v1, wait for 5v5 where W can hit 2+ people and you can R out safely",
            ],
        },
    }

    champ_tips = PHASE_TIPS.get(my_champ, {})
    tips = champ_tips.get(phase, [])
    if tips:
        lines = "\n".join(f"  [blue]→[/blue]  {t}" for t in tips)
        console.print(Panel(lines, title=f"[bold]{my_champ} — {phase.capitalize()} Phase[/bold]", border_style="blue", padding=(1, 2)))

    console.print()


# ---------------------------------------------------------------------------
# Main observer loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Live game observer with phase reports")
    parser.add_argument("account", choices=list(ACCOUNTS.keys()))
    parser.add_argument("--enemy-items", default="",
                        help='Enemy items seen: "Gnar:Trinity+Steelcaps,Jinx:Kraken"')
    args = parser.parse_args()

    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    # Parse manual items
    manual_items: dict[str, list[str]] = {}
    if args.enemy_items:
        for entry in args.enemy_items.split(","):
            if ":" in entry:
                champ, items = entry.split(":", 1)
                manual_items[champ.strip()] = [i.strip() for i in items.split("+")]

    # ── Pregame ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule(f"[bold cyan]Observer — {account_str}[/bold cyan]"))
    console.print()

    console.print("[dim]Reading live game...[/dim]")
    _load_champ_id_map()
    my_champ, enemy_champs, keystones = fetch_live_data(args.account)

    live_data = get_live_game_data(puuid)
    ally_names = []
    if live_data:
        my_team_id = next(
            (p["teamId"] for p in live_data["participants"]
             if champ_name_from_id(p["championId"]) == my_champ),
            None
        )
        if my_team_id:
            ally_names = [
                champ_name_from_id(p["championId"])
                for p in live_data["participants"]
                if p["teamId"] == my_team_id and champ_name_from_id(p["championId"]) != my_champ
            ]

    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp([n for n, _ in enemy_champs])
    threats    = aggregate_threats(enemy_champs, keystones, manual_items)
    threats["names_with_shields"] = []

    recent_form = get_recent_form(puuid, n=5)

    # Print pregame sections
    console.print(Rule("[bold]Champion Pick[/bold]"))
    rune_ctx_pre = build_rune_context(ally_comp, enemy_comp)
    print_champion_pick(ally_comp, enemy_comp, rune_ctx_pre)

    console.print(Rule("[bold]Ban Recommendations[/bold]"))
    print_bans(recommend_bans(my_champ, recent_form))

    console.print(Rule("[bold]Recent Form[/bold]"))
    print_recent_form(recent_form)

    from riftlab.advisors.comp_check import comp_table
    console.print(Rule("[bold]Comp Overview[/bold]"))
    console.print(comp_table("Your Team",  ally_comp,  "green"))
    console.print(comp_table("Enemy Team", enemy_comp, "red"))

    console.print(Rule("[bold]Build[/bold]"))
    recommendations = get_adaptive_recommendations(my_champ, threats, manual_items)
    print_recommendations(my_champ, recommendations)

    console.print(Rule("[bold]Runes[/bold]"))
    rune_ctx  = build_rune_context(ally_comp, enemy_comp)
    rune_page = pick_rune_page(my_champ, rune_ctx)
    if rune_page:
        print_rune_page(rune_page, my_champ)

    console.print(Rule("[bold]Game Plan[/bold]"))
    tips = generate_loading_tips(my_champ, ally_comp, enemy_comp, threats, recent_form)
    print_loading_tips(tips)

    console.print(Rule("[bold green]GL HF — Observer active[/bold green]"))
    console.print("[dim]Phase reports will fire automatically at 5, 14, and 25 minutes.[/dim]")
    console.print("[dim]Press Ctrl+C to exit.\n[/dim]")

    # ── Observation loop ─────────────────────────────────────────────────────
    fired = {"early": False, "mid": False, "late": False}

    while True:
        try:
            game_time = get_game_time(puuid)

            if game_time is None:
                console.print("\n[dim]Game ended or not found. Observer closing.[/dim]")
                break

            if not fired["early"] and game_time >= PHASE_EARLY:
                phase_report("early", my_champ, ally_comp, enemy_comp, enemy_champs, keystones, manual_items, game_time)
                fired["early"] = True

            elif not fired["mid"] and game_time >= PHASE_MID:
                phase_report("mid", my_champ, ally_comp, enemy_comp, enemy_champs, keystones, manual_items, game_time)
                fired["mid"] = True

            elif not fired["late"] and game_time >= PHASE_LATE:
                phase_report("late", my_champ, ally_comp, enemy_comp, enemy_champs, keystones, manual_items, game_time)
                fired["late"] = True

            elif all(fired.values()):
                console.print("[dim]All phases fired. Observer standing by until game ends...[/dim]")
                time.sleep(60)
                continue

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            console.print("\n[dim]Observer stopped.[/dim]")
            break


if __name__ == "__main__":
    main()
