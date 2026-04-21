"""
pregame.py — Full loading screen briefing for your current game.
Run this as soon as champion select ends.

Usage:
  python pregame.py main
  python pregame.py lab
"""

import sys
import time
import argparse

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

from lol_stats import get_account, get_match_ids, get_match, extract_participant, BASE_SUMMONER, _get, ACCOUNTS
from match_cache import get_match_cached, is_excluded
from comp_check import (
    analyze_comp, generate_matchup_insights, recommend_build,
    build_context, champ_name_from_id, _load_champ_id_map,
    resolve, comp_table, print_insights, ACCOUNTS,
)
from build_advisor import fetch_live_data, aggregate_threats, get_adaptive_recommendations, print_enemy_read, print_recommendations
from runes import build_rune_context, pick_rune_page, print_rune_page
from champ_select import print_champion_pick

load_dotenv()
console = Console()


# ---------------------------------------------------------------------------
# Recent form
# ---------------------------------------------------------------------------

def get_recent_form(puuid: str, n: int = 5) -> list[dict]:
    match_ids = get_match_ids(puuid, count=n + 5)  # fetch extras to cover exclusions
    rows = []
    for mid in match_ids:
        if len(rows) >= n:
            break
        if is_excluded(mid):
            continue
        try:
            match = get_match_cached(mid)
            p = extract_participant(match, puuid)
            if not p: continue
            dur = match["info"]["gameDuration"] / 60
            if dur < 5: continue
            ch = p.get("challenges", {})
            rows.append({
                "champion": p["championName"],
                "win":      p["win"],
                "deaths":   p["deaths"],
                "kp":       ch.get("killParticipation", 0),
                "vision":   p["visionScore"],
            })
        except Exception:
            pass
    return rows


def check_tilt(rows: list[dict]) -> tuple[str, str] | None:
    """
    Returns (level, message) if tilt detected, else None.
    Levels: "stop" (red), "warn" (yellow)
    """
    if len(rows) < 3:
        return None

    recent3 = rows[:3]
    recent5 = rows[:5]

    wins3  = sum(1 for r in recent3 if r["win"])
    wins5  = sum(1 for r in recent5 if r["win"])
    wr5    = wins5 / len(recent5)
    avg_d3 = sum(r["deaths"] for r in recent3) / len(recent3)
    avg_d5 = sum(r["deaths"] for r in recent5) / len(recent5) if recent5 else 0

    # Hard stop: 3 losses in a row with bad stats
    if wins3 == 0 and avg_d3 > 8:
        return ("stop",
            "3 losses in a row with high deaths. You're tilting — stop for today. "
            "Playing more will make it worse, not better.")

    # Hard stop: 0 wins in last 3
    if wins3 == 0:
        return ("stop",
            "0 wins in your last 3 games. Take a break before this game — "
            "come back when you're fresh.")

    # Warning: downward trend
    if wr5 < 0.30 and avg_d5 > 9:
        return ("warn",
            f"WR is {wr5*100:.0f}% with {avg_d5:.1f} avg deaths over last 5 games. "
            "You're in a rough patch — play one more, but stop if you lose.")

    # Warning: deaths spiking
    if len(rows) >= 2 and avg_d3 > avg_d5 * 1.4 and avg_d3 > 10:
        return ("warn",
            f"Deaths trending up — {avg_d3:.1f}/game in last 3 vs {avg_d5:.1f} overall. "
            "Focus on surviving this game, not making plays.")

    return None


def print_tilt_check(rows: list[dict]):
    result = check_tilt(rows)
    if not result:
        return
    level, msg = result
    if level == "stop":
        console.print(Panel(
            f"[bold red]⛔  STOP QUEUING[/bold red]\n\n  {msg}",
            border_style="red", padding=(1, 2)
        ))
    else:
        console.print(Panel(
            f"[bold yellow]⚠  TILT WARNING[/bold yellow]\n\n  {msg}",
            border_style="yellow", padding=(1, 2)
        ))


def print_recent_form(rows: list[dict]):
    t = Table(title="Recent Form (last 5)", box=box.SIMPLE, show_header=True)
    t.add_column("Champion", style="cyan")
    t.add_column("Result",   justify="center")
    t.add_column("Deaths",   justify="center")
    t.add_column("KP%",      justify="right")
    t.add_column("Vision",   justify="right")

    for r in rows:
        result = "[green]W[/green]" if r["win"] else "[red]L[/red]"
        d_col  = "green" if r["deaths"] <= 5 else ("yellow" if r["deaths"] <= 8 else "red")
        t.add_row(
            r["champion"], result,
            f"[{d_col}]{r['deaths']}[/{d_col}]",
            f"{r['kp']*100:.0f}%",
            str(r["vision"]),
        )

    # Summary line
    wr    = sum(1 for r in rows if r["win"]) / len(rows) * 100 if rows else 0
    avg_d = sum(r["deaths"] for r in rows) / len(rows) if rows else 0
    avg_ed_note = "[green]On a good streak[/green]" if wr >= 60 else ("[yellow]Mixed form[/yellow]" if wr >= 40 else "[red]Rough patch[/red]")

    console.print(t)
    console.print(f"  {avg_ed_note}   WR: [bold]{wr:.0f}%[/bold]   Avg deaths: [bold]{avg_d:.1f}[/bold]\n")


# ---------------------------------------------------------------------------
# Loading screen tips — the main coaching block
# ---------------------------------------------------------------------------

def generate_loading_tips(
    my_champ: str,
    ally_comp: dict,
    enemy_comp: dict,
    threats: dict,
    recent_form: list[dict],
) -> list[tuple[str, str]]:
    tips = []

    mc = resolve(my_champ)
    my_data = mc[1] if mc else {}
    role = my_data.get("cls", "")

    # --- Early death reminder (personal pattern) ---
    if recent_form:
        avg_deaths = sum(r["deaths"] for r in recent_form) / len(recent_form)
        recent_wins = [r for r in recent_form if r["win"]]
        recent_losses = [r for r in recent_form if not r["win"]]
        if avg_deaths > 7:
            tips.append(("warn",
                f"You've been averaging [bold]{avg_deaths:.1f} deaths[/bold] recently. "
                "Your #1 priority this game is surviving the first 15 minutes. "
                "Don't fight unless you're sure you win."))

    # --- Win condition ---
    ally_wc  = ally_comp["primary_win_con"]
    enemy_wc = enemy_comp["primary_win_con"]
    wc_advice = {
        "engage":     "Look for grouped enemies and chain CC — your team wants to start fights.",
        "pick":       "Wait for someone to overextend, then punish. Don't force teamfights.",
        "teamfight":  "Stay grouped from 15 min onward. Your comp wins 5v5, not skirmishes.",
        "poke":       "Poke them below 60% before committing to a fight. Don't all-in at full HP.",
        "scale":      "Survive early, don't trade objectives for kills. Your power comes at 3 items.",
        "splitpush":  "Apply pressure in two lanes — don't group mindlessly.",
    }
    tips.append(("info",
        f"[bold]Your win condition:[/bold] {ally_wc.upper()} — {wc_advice.get(ally_wc, 'play to your comp strengths.')}"))

    # --- Enemy win condition counter ---
    counter_advice = {
        "engage":    "Don't cluster in one spot — spread out so their engage can't hit everyone.",
        "pick":      "Ward tribush and river before walking anywhere. Don't face-check bushes.",
        "teamfight": "Avoid 5v5 if they're ahead. Look for picks on their carries instead.",
        "poke":      "Don't fight at half HP. Recall when poked down, then come back full.",
        "scale":     "Force early objectives — don't let this go to 30+ min.",
        "splitpush": "Match their split or collapse with 3+. Never send one person to answer.",
    }
    tips.append(("warn",
        f"[bold]Enemy win condition:[/bold] {enemy_wc.upper()} — {counter_advice.get(enemy_wc, 'respect their game plan.')}"))

    # --- Frontline check ---
    if len(ally_comp["frontline"]) == 0:
        tips.append(("warn",
            "No frontline on your team. You can't hard engage into a standard fight — "
            "play for picks and protect your carries."))

    # --- Scaling window ---
    if len(enemy_comp["late_scalers"]) >= 2:
        scalers = ", ".join(enemy_comp["late_scalers"])
        tips.append(("warn",
            f"[bold]{scalers}[/bold] scale hard — force a decision before 25 min. "
            "Take objectives, don't let them farm."))

    if len(ally_comp["late_scalers"]) >= 2:
        tips.append(("info",
            f"Your team scales late ({', '.join(ally_comp['late_scalers'])}). "
            "Don't throw a win by forcing bad early fights. Play safe and spike with items."))

    # --- High mobility enemy ---
    if len(enemy_comp["high_mobility"]) >= 3:
        tips.append(("warn",
            f"Enemy has {len(enemy_comp['high_mobility'])} mobile champions "
            f"({', '.join(enemy_comp['high_mobility'])}). "
            "Land CC before they can dash away — don't chase after a missed engage."))

    # --- Assassin threat ---
    if enemy_comp["assassins"]:
        tips.append(("warn",
            f"[bold]{', '.join(enemy_comp['assassins'])}[/bold] will go for your squishiest carry. "
            "As Rakan: save W to knock them away from your ADC, don't use it to initiate when assassins are alive."))

    # --- GW reminder ---
    if threats.get("healing", 0) >= 2:
        tips.append(("warn",
            "Enemy has healing — buy [bold]Thornmail[/bold] (vs AD heavy) or [bold]Morellonomicon[/bold] (vs AP heavy) before your 3rd item."))

    # --- Rakan-specific mental checklist ---
    if my_champ.lower() == "rakan":
        adc_names = [n for n, c in ally_comp["resolved"] if c["cls"] == "marksman"]
        fighting_adcs = {"Samira", "Draven", "Jinx", "Tristana", "Kaisa"}
        peel_adcs = {"Vayne", "Ezreal", "Aphelios", "Caitlyn", "Jhin"}

        if adc_names:
            adc = adc_names[0]
            if adc in fighting_adcs:
                tips.append(("good",
                    f"[bold]{adc}[/bold] wants to fight — this is a good Rakan pairing. "
                    "Look for level 2 all-in. Use R to start, let them follow."))
            elif adc in peel_adcs:
                tips.append(("info",
                    f"[bold]{adc}[/bold] needs peel, not all-ins. "
                    "Play near them, use W to intercept dives. Don't leave them alone."))

        engage_allies = [n for n, c in ally_comp["resolved"]
                         if c["win_con"] == "engage" and n.lower() != "rakan"]
        if engage_allies:
            tips.append(("good",
                f"[bold]{', '.join(engage_allies)}[/bold] also engages — "
                "let them go in first, then R as follow-up for max impact."))

        tips.append(("info",
            "[bold]Rakan checklist:[/bold] "
            "① Don't die level 1-2  "
            "② Pop Locket the moment you land R  "
            "③ Knight's Vow on your ADC before first back  "
            "④ W to save, not to initiate, when assassins are alive"))

    # --- Ekko-specific coaching ---
    if my_champ.lower() == "ekko":
        # R philosophy
        tips.append(("good",
            "[bold]R is your safety net, not a panic button.[/bold] "
            "Use it to enable dives you wouldn't normally take — go in hard, "
            "W bubble, burst, and only R if they turn. "
            "If you save R for emergencies you'll never use it aggressively enough."))

        # Mid carry loop
        tips.append(("info",
            "[bold]Mid carry loop:[/bold] "
            "① Level 3 — shove the wave fast  "
            "② Roam to whoever is winning their lane — burn Flash on one kill  "
            "③ Back, buy, return to mid, repeat every 3 minutes  "
            "④ Never roam if you're behind — farm and look for solo kill first"))

        # W usage
        grouped = len(enemy_comp["frontline"]) >= 2 or enemy_comp["primary_win_con"] in ("teamfight", "engage")
        if grouped:
            tips.append(("good",
                "Enemy groups up — your W bubble does AoE stun. "
                "Flash into their backline, drop W on 2+ people, burst the carry. "
                "This is your win condition teamfight."))
        else:
            tips.append(("info",
                "W is a single-target stun here — use it to lock down one carry, "
                "not to zone. Drop it where they're running to, not where they are."))

        # Passive reminder
        tips.append(("warn",
            "[bold]Passive (Z-Drive):[/bold] Every 3rd hit deals bonus magic damage + slows. "
            "AA between abilities to keep the stack building. "
            "Q → AA → E → AA is more damage than skipping autos."))

        # Matchup check
        assassin_enemies = enemy_comp.get("assassins", [])
        if assassin_enemies:
            tips.append(("warn",
                f"[bold]{', '.join(assassin_enemies)}[/bold] can one-shot you before R casts. "
                "Build [bold]Zhonya's[/bold] as your 2nd or 3rd item — "
                "Hourglass active buys the 2.5s you need for R to rewind."))

        tips.append(("info",
            "[bold]Ekko checklist:[/bold] "
            "① Farm to 6 — don't force kills before ult  "
            "② First roam after Level 6 with ult up  "
            "③ AA between spells to proc passive  "
            "④ R to rewind bad dives, not to escape after inting"))

    return tips


# ---------------------------------------------------------------------------
# Ban recommendations
# ---------------------------------------------------------------------------

# (champion, reason, threat_type)
# threat_type: "rakan_counter" | "lane_bully" | "broken" | "snowball"
RAKAN_COUNTERS = {
    "Nautilus":  ("Hard-engages over your W, chains you into his team before you can act",         "rakan_counter"),
    "Leona":     ("Burst CC at level 2 kills you before Xayah/Jinx can follow up",                 "rakan_counter"),
    "Blitzcrank":("Hook pulls your ADC out of your W shield range instantly",                      "rakan_counter"),
    "Lux":       ("E root interrupts mid-R, Q snare cancels your engage window",                   "rakan_counter"),
    "Morgana":   ("Black Shield makes your W useless on the enemy ADC for 5 seconds",              "rakan_counter"),
    "Mel":       ("Reflects your W damage back — punishes aggressive Rakan plays",                 "rakan_counter"),
    "Zilean":    ("Double bomb + ult completely nullifies your all-in",                            "rakan_counter"),
    "Janna":     ("Ult knocks your team away mid-engage every time",                               "rakan_counter"),
    "Thresh":    ("Lantern gives ADC free escape from your R; hook punishes your engage timing",   "rakan_counter"),
}

LANE_BULLIES = {
    "Caitlyn":   ("Outranges Jinx/Xayah, zone them under tower before you hit 6",                 "lane_bully"),
    "Draven":    ("Kills your ADC at level 1 trade before you have items",                        "lane_bully"),
    "Miss Fortune":("Bullet Time through your team before you can R out",                         "lane_bully"),
}

BROKEN_OR_SNOWBALL = {
    "Zed":       ("If fed he one-shots your ADC before you can W — hard to protect against",      "snowball"),
    "Katarina":  ("Resets through your R knockup — hard to CC long enough to kill her",           "snowball"),
    "Shaco":     ("Level 2 invade kills Rakan instantly; boxes interrupt your R mid-cast",        "snowball"),
    "Twitch":    ("Invisible ADC with stealth resets — your W can't save what you can't see",     "snowball"),
    "Vayne":     ("True damage shreds your frontline late; invisible E makes her hard to peel off","snowball"),
}

ALL_BAN_REASONS = {**RAKAN_COUNTERS, **LANE_BULLIES, **BROKEN_OR_SNOWBALL}


def recommend_bans(my_champ: str, recent_form: list[dict]) -> list[tuple[str, str, str]]:
    """Return top 3 ban suggestions as (champion, reason, type)."""
    bans = []

    # Always prioritize direct Rakan counters
    for champ, (reason, typ) in RAKAN_COUNTERS.items():
        bans.append((champ, reason, typ))

    # Add snowball/broken picks
    for champ, (reason, typ) in BROKEN_OR_SNOWBALL.items():
        bans.append((champ, reason, typ))

    for champ, (reason, typ) in LANE_BULLIES.items():
        bans.append((champ, reason, typ))

    # Prioritize: Morgana and Nautilus first (hardest counters),
    # then Mel (user's current ban), then situational
    priority = ["Nautilus", "Morgana", "Mel", "Blitzcrank", "Shaco", "Zed", "Leona", "Lux"]
    ordered = sorted(bans, key=lambda x: priority.index(x[0]) if x[0] in priority else 99)

    return ordered[:5]


def print_bans(bans: list[tuple[str, str, str]]):
    TYPE_COLOR = {
        "rakan_counter": "[red]Rakan counter[/red]",
        "lane_bully":    "[yellow]Lane bully[/yellow]",
        "snowball":      "[magenta]Snowball threat[/magenta]",
    }

    t = Table(title="Ban Recommendations", box=box.ROUNDED)
    t.add_column("Priority", justify="center")
    t.add_column("Champion",  style="cyan", min_width=14)
    t.add_column("Type",      min_width=16)
    t.add_column("Why ban")

    for i, (champ, reason, typ) in enumerate(bans, 1):
        priority = "[bold red]MUST BAN[/bold red]" if i == 1 else f"#{i}"
        t.add_row(priority, champ, TYPE_COLOR.get(typ, typ), reason)

    console.print(t)


def print_loading_tips(tips: list[tuple[str, str]]):
    ICONS = {"good": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "info": "[blue]→[/blue]"}
    lines = [f"{ICONS.get(lvl, '·')}  {msg}" for lvl, msg in tips]
    console.print(Panel(
        "\n\n".join(lines),
        title="[bold]Loading Screen Briefing[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Ally stats & roam priority
# ---------------------------------------------------------------------------

def fetch_ally_stats(ally_participants: list[dict]) -> list[dict]:
    """Fetch recent form for each ally using their PUUIDs from spectator data."""
    result = []
    for p in ally_participants:
        puuid = p["puuid"]
        name  = p.get("riotIdGameName") or p.get("summonerName", "?")
        champ = champ_name_from_id(p["championId"])

        try:
            time.sleep(0.1)
            form = get_recent_form(puuid, n=5)
        except Exception:
            form = []

        if not form:
            result.append({"name": name, "champion": champ, "form": [], "tag": "NO DATA"})
            continue

        wr         = sum(1 for r in form if r["win"]) / len(form)
        avg_d      = sum(r["deaths"] for r in form) / len(form)
        avg_kp     = sum(r["kp"] for r in form) / len(form)
        hot_streak = all(r["win"] for r in form[:3])
        cold       = not any(r["win"] for r in form[:3])

        if wr >= 0.60 and avg_d <= 5:
            tag = "CAMP"
        elif wr <= 0.35 or avg_d >= 9:
            tag = "AVOID"
        else:
            tag = "NEUTRAL"

        result.append({
            "name":       name,
            "champion":   champ,
            "wr":         wr,
            "avg_deaths": avg_d,
            "avg_kp":     avg_kp,
            "hot_streak": hot_streak,
            "cold":       cold,
            "form":       form,
            "tag":        tag,
        })

    return result


def print_ally_stats(ally_stats: list[dict]):
    t = Table(title="Teammate Recent Form (last 5 games)", box=box.ROUNDED)
    t.add_column("Player",     style="cyan", min_width=12)
    t.add_column("Champion",   min_width=14)
    t.add_column("WR",         justify="center")
    t.add_column("Avg Deaths", justify="center")
    t.add_column("Avg KP",     justify="center")
    t.add_column("Tag",        justify="center")

    for a in ally_stats:
        if a["tag"] == "NO DATA":
            t.add_row(a["name"], a["champion"], "—", "—", "—", "[dim]no data[/dim]")
            continue

        wr_c  = "green" if a["wr"] >= 0.6 else ("yellow" if a["wr"] >= 0.4 else "red")
        d_c   = "green" if a["avg_deaths"] <= 4 else ("yellow" if a["avg_deaths"] <= 7 else "red")
        tag_c = "green" if a["tag"] == "CAMP" else ("red" if a["tag"] == "AVOID" else "white")
        streak = " [bold yellow]HOT[/bold yellow]" if a.get("hot_streak") else (" [bold blue]COLD[/bold blue]" if a.get("cold") else "")

        t.add_row(
            a["name"],
            f"{a['champion']}{streak}",
            f"[{wr_c}]{a['wr']*100:.0f}%[/{wr_c}]",
            f"[{d_c}]{a['avg_deaths']:.1f}[/{d_c}]",
            f"{a['avg_kp']*100:.0f}%",
            f"[bold {tag_c}]{a['tag']}[/bold {tag_c}]",
        )

    console.print(t)


def print_roam_priority(ally_stats: list[dict], my_team_id: int, my_champ: str, ally_comp: dict):
    camps  = [a for a in ally_stats if a["tag"] == "CAMP"]
    avoids = [a for a in ally_stats if a["tag"] == "AVOID"]

    side_label = "Blue side" if my_team_id == 100 else "Red side"
    side_tip = (
        "Blue side — after winning bot, path mid through river (shorter). "
        "Dragon is your primary objective; push wave first, then take it."
        if my_team_id == 100 else
        "Red side — roam mid via tri-bush. Baron becomes priority post-14 min. "
        "After a fight, rotate top through mid to snowball the map."
    )

    lines = []

    if camps:
        names = ", ".join(
            f"[bold green]{a['champion']}[/bold green] ({a['wr']*100:.0f}% WR)"
            for a in camps
        )
        lines.append(
            f"[green]CAMP →[/green]  {names}\n"
            "  They're in form — invest early resources here and snowball their lead."
        )

    if avoids:
        names = ", ".join(
            f"[bold red]{a['champion']}[/bold red] ({a['wr']*100:.0f}% WR, {a['avg_deaths']:.1f} avg deaths)"
            for a in avoids
        )
        lines.append(
            f"[red]AVOID →[/red]  {names}\n"
            "  Don't waste wards or roams here. Let them play safe and don't tilt over it."
        )

    lines.append(f"[blue]Side:[/blue]  {side_label} — {side_tip}")

    if my_champ.lower() == "rakan":
        if camps:
            camp_champ = camps[0]["champion"]
            lines.append(
                f"[cyan]Rakan roam:[/cyan]  Win bot level 2-3, push wave, then burn Flash "
                f"on {camp_champ}'s lane to get a kill. Return immediately. "
                "Only roam if you're equal or ahead — never from behind."
            )
        else:
            lines.append(
                "[cyan]Rakan roam:[/cyan]  No standout lane to camp. "
                "Play for bot lane vision, Dragon control, and peel your ADC. "
                "Roam only after shoving a wave under their tower."
            )

    console.print(Panel(
        "\n\n".join(lines),
        title="[bold]Roam Priority[/bold]",
        border_style="blue",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Loading screen pregame briefing")
    parser.add_argument("account", choices=list(ACCOUNTS.keys()))
    args = parser.parse_args()

    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)

    console.print()
    console.print(Rule(f"[bold cyan]Pregame Briefing — {account_str}[/bold cyan]"))
    console.print()

    # Fetch live game
    console.print("[dim]Reading live game...[/dim]")
    my_champ, enemy_champs, keystones = fetch_live_data(args.account)

    _load_champ_id_map()
    my_team_id       = 100
    ally_names       = []
    ally_participants = []
    try:
        data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{(lambda: get_account(game_name, tag)['puuid'])()}")
        my_team_id = next(p["teamId"] for p in data["participants"]
                          if champ_name_from_id(p["championId"]) == my_champ)
        ally_names = [champ_name_from_id(p["championId"]) for p in data["participants"]
                      if p["teamId"] == my_team_id and champ_name_from_id(p["championId"]) != my_champ]
        ally_participants = [p for p in data["participants"]
                             if p["teamId"] == my_team_id
                             and champ_name_from_id(p["championId"]) != my_champ]
    except Exception:
        ally_names        = []
        ally_participants = []

    # Fetch recent form in parallel with comp analysis
    console.print("[dim]Fetching recent form...[/dim]")
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]
    recent_form = get_recent_form(puuid, n=5)

    # Comp analysis
    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp([n for n, _ in enemy_champs])
    threats    = aggregate_threats(enemy_champs, keystones, {})
    threats["names_with_shields"] = []

    # Print sections
    console.print(Rule("[bold]Champion Pick[/bold]"))
    rune_ctx_pre = build_rune_context(ally_comp, enemy_comp)
    print_champion_pick(ally_comp, enemy_comp, rune_ctx_pre)

    console.print(Rule("[bold]Ban Recommendations[/bold]"))
    bans = recommend_bans(my_champ, recent_form)
    print_bans(bans)

    console.print(Rule("[bold]Recent Form[/bold]"))
    print_recent_form(recent_form)
    print_tilt_check(recent_form)

    console.print(Rule("[bold]Comp Overview[/bold]"))
    console.print(comp_table("Your Team",  ally_comp,  "green"))
    console.print(comp_table("Enemy Team", enemy_comp, "red"))

    if ally_participants:
        console.print(Rule("[bold]Teammate Stats[/bold]"))
        console.print("[dim]Fetching ally recent form...[/dim]")
        ally_stats = fetch_ally_stats(ally_participants)
        print_ally_stats(ally_stats)
        print_roam_priority(ally_stats, my_team_id, my_champ, ally_comp)

    console.print(Rule("[bold]Enemy Keystones[/bold]"))
    print_enemy_read(enemy_champs, keystones, {})

    console.print(Rule("[bold]Build[/bold]"))
    recommendations = get_adaptive_recommendations(my_champ, threats, {})
    print_recommendations(my_champ, recommendations)

    console.print(Rule("[bold]Runes[/bold]"))
    rune_ctx  = build_rune_context(ally_comp, enemy_comp)
    rune_page = pick_rune_page(my_champ, rune_ctx)
    if rune_page:
        print_rune_page(rune_page, my_champ)

    console.print(Rule("[bold]Game Plan[/bold]"))
    tips = generate_loading_tips(my_champ, ally_comp, enemy_comp, threats, recent_form)
    print_loading_tips(tips)

    console.print(Rule(f"[bold green]GL HF[/bold green]"))
    console.print()


if __name__ == "__main__":
    main()
