"""
playstyle.py — Analyze playstyle from match history and suggest champions.
Usage: python playstyle.py [lab|main] [--games N]
"""

import os
import sys
import time
import argparse
from collections import defaultdict

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

import requests

from lol_stats import (
    get_account, get_match_ids, get_match, extract_participant,
    ACCOUNTS, BASE_SUMMONER, _get, console,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Mastery fetching
# ---------------------------------------------------------------------------

def fetch_masteries(puuid: str, top_n: int = 20) -> list[dict]:
    """Return top N champion masteries with name resolved via Data Dragon."""
    try:
        data = _get(f"{BASE_SUMMONER}/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={top_n}")
    except Exception:
        return []

    # Resolve champion IDs via Data Dragon
    try:
        ver   = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5).json()[0]
        champs = requests.get(f"https://ddragon.leagueoflegends.com/cdn/{ver}/data/en_US/champion.json", timeout=5).json()
        id_to_name = {int(v["key"]): k for k, v in champs["data"].items()}
    except Exception:
        id_to_name = {}

    result = []
    for m in data:
        result.append({
            "champion": id_to_name.get(m["championId"], str(m["championId"])),
            "points":   m["championPoints"],
            "level":    m["championLevel"],
            "chest":    m.get("chestGranted", False),
        })
    return result


# ---------------------------------------------------------------------------
# Richer match extraction
# ---------------------------------------------------------------------------

def extract_rich(match: dict, puuid: str) -> dict | None:
    p = extract_participant(match, puuid)
    if not p:
        return None

    duration_m = match["info"]["gameDuration"] / 60
    if duration_m < 5:
        return None  # remakes

    ch = p.get("challenges", {})
    total_cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
    total_dmg = p["totalDamageDealtToChampions"] or 1

    kills, deaths, assists = p["kills"], p["deaths"], p["assists"]
    team = [x for x in match["info"]["participants"] if x["teamId"] == p["teamId"]]
    team_kills = sum(x["kills"] for x in team) or 1
    team_dmg = sum(x["totalDamageDealtToChampions"] for x in team) or 1

    return {
        "champion":          p["championName"],
        "position":          p.get("teamPosition", "UNKNOWN"),
        "win":               p["win"],
        "kills":             kills,
        "deaths":            deaths,
        "assists":           assists,
        "kp":                (kills + assists) / team_kills,           # kill participation
        "dmg_share":         p["totalDamageDealtToChampions"] / team_dmg,
        "magic_ratio":       p["magicDamageDealtToChampions"] / total_dmg,
        "physical_ratio":    p["physicalDamageDealtToChampions"] / total_dmg,
        "cc_score":          p.get("timeCCingOthers", 0),
        "vision_score":      p["visionScore"],
        "wards_placed":      p.get("wardsPlaced", 0),
        "cs_per_min":        total_cs / duration_m,
        "dmg_per_min":       p["totalDamageDealtToChampions"] / duration_m,
        "gold_per_min":      p["goldEarned"] / duration_m,
        "obj_dmg":           p.get("damageDealtToObjectives", 0),
        "solo_kills":        ch.get("soloKills", 0),
        "saved_allies":      ch.get("saveAllyFromDeath", 0),
        "turret_plates":     ch.get("turretPlatesTaken", 0),
        "duration_m":        duration_m,
        "first_blood":       p.get("firstBloodKill", False),
    }


def fetch_profile_data(account_label: str, n_games: int = 30) -> list[dict]:
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)

    console.print(f"\n[bold]Analyzing [cyan]{account_str}[/cyan] — fetching {n_games} ranked games...[/bold]")

    acct = get_account(game_name, tag)
    puuid = acct["puuid"]
    match_ids = get_match_ids(puuid, count=n_games)

    rows = []
    for mid in match_ids:
        try:
            match = get_match(mid)
            row = extract_rich(match, puuid)
            if row:
                rows.append(row)
        except Exception as exc:
            console.print(f"[dim]Skip {mid}: {exc}[/dim]")
        time.sleep(0.05)

    return rows


# ---------------------------------------------------------------------------
# Profile computation
# ---------------------------------------------------------------------------

def avg(lst): return sum(lst) / len(lst) if lst else 0

def compute_profile(rows: list[dict]) -> dict:
    if not rows:
        return {}

    # Role distribution
    role_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        role_counts[r["position"]] += 1
    primary_role = max(role_counts, key=role_counts.get)

    # Aggregated metrics
    metrics = {
        "avg_kp":           avg([r["kp"] for r in rows]),
        "avg_dmg_share":    avg([r["dmg_share"] for r in rows]),
        "avg_magic_ratio":  avg([r["magic_ratio"] for r in rows]),
        "avg_physical_ratio": avg([r["physical_ratio"] for r in rows]),
        "avg_cc_score":     avg([r["cc_score"] for r in rows]),
        "avg_vision":       avg([r["vision_score"] for r in rows]),
        "avg_cs_pm":        avg([r["cs_per_min"] for r in rows]),
        "avg_dmg_pm":       avg([r["dmg_per_min"] for r in rows]),
        "avg_gold_pm":      avg([r["gold_per_min"] for r in rows]),
        "avg_solo_kills":   avg([r["solo_kills"] for r in rows]),
        "avg_saved_allies": avg([r["saved_allies"] for r in rows]),
        "first_blood_rate": avg([int(r["first_blood"]) for r in rows]),
        "death_rate":       avg([r["deaths"] for r in rows]),
        "primary_role":     primary_role,
        "role_dist":        dict(sorted(role_counts.items(), key=lambda x: -x[1])),
        "n_games":          len(rows),
        "wr":               avg([int(r["win"]) for r in rows]),
    }

    # Champion diversity
    champs = [r["champion"] for r in rows]
    metrics["unique_champs"] = len(set(champs))
    metrics["top_champs"] = sorted(
        {c: champs.count(c) for c in set(champs)}.items(), key=lambda x: -x[1]
    )[:5]

    return metrics


def classify_playstyle(p: dict) -> list[str]:
    """Return a list of playstyle tags."""
    tags = []

    # Damage type
    if p["avg_magic_ratio"] > 0.65:
        tags.append("ability-damage")
    elif p["avg_physical_ratio"] > 0.65:
        tags.append("auto-attack")
    else:
        tags.append("mixed-damage")

    # Role
    role_map = {
        "MIDDLE": "mid-laner", "UTILITY": "support",
        "JUNGLE": "jungler", "TOP": "top-laner", "BOTTOM": "adc",
    }
    tags.append(role_map.get(p["primary_role"], "roamer"))

    # Carry vs utility
    if p["avg_dmg_share"] > 0.28:
        tags.append("carry")
    elif p["avg_kp"] > 0.70:
        tags.append("playmaker")
    else:
        tags.append("utility")

    # CC
    if p["avg_cc_score"] > 15:
        tags.append("cc-heavy")

    # Vision
    if p["avg_vision"] > 35:
        tags.append("vision-focused")

    # Farm dependency
    if p["avg_cs_pm"] > 6.5:
        tags.append("farm-dependent")
    elif p["avg_cs_pm"] < 3.0:
        tags.append("farm-independent")

    # Aggression
    if p["first_blood_rate"] > 0.15 or p["avg_solo_kills"] > 0.8:
        tags.append("aggressive")

    # Saver/protector
    if p["avg_saved_allies"] > 0.5:
        tags.append("protector")

    # Death-prone
    if p["death_rate"] > 6:
        tags.append("high-risk")

    return tags


# ---------------------------------------------------------------------------
# Champion suggestions
# ---------------------------------------------------------------------------

# (champion, reason, difficulty 1-3)
CHAMP_DB: list[tuple[str, list[str], str, int]] = [
    # name, matching tags, reason, difficulty
    ("Orianna",      ["ability-damage", "mid-laner", "carry", "cc-heavy"],
     "Ball control rewards map awareness; strong teamfight shield+CC combo", 2),
    ("Viktor",       ["ability-damage", "mid-laner", "carry", "farm-dependent"],
     "Safe scaling mage; punishes immobile targets like Syndra but with more self-peel", 2),
    ("Lissandra",    ["ability-damage", "mid-laner", "cc-heavy", "playmaker"],
     "High CC mage with self-peel ult; great for roaming mid players", 2),
    ("Taliyah",      ["ability-damage", "mid-laner", "playmaker"],
     "Roam-focused mage; walling creates picks and enables objectives", 3),
    ("Vex",          ["ability-damage", "mid-laner", "carry"],
     "Anti-dash mage that punishes mobile meta; straightforward for Syndra players", 1),
    ("Hwei",         ["ability-damage", "mid-laner", "utility", "cc-heavy"],
     "Highest ability variety in the game; rewards deep champion mastery", 3),
    ("Neeko",        ["ability-damage", "mid-laner", "cc-heavy", "carry"],
     "Deceptive burst mage; root combo similar to Syndra's stun setup", 2),
    ("Aurelion Sol", ["ability-damage", "mid-laner", "carry", "farm-dependent"],
     "Massive teamfight presence once stacked; rewards patient farm-first play", 2),
    ("Karma",        ["ability-damage", "support", "utility", "protector"],
     "High agency support that can also flex mid; strong early and scales", 2),
    ("Zilean",       ["support", "utility", "protector", "playmaker"],
     "Ult gives your carry a free life; time bombs reward ability combos like Bard Q", 2),
    ("Rakan",        ["support", "playmaker", "cc-heavy", "aggressive"],
     "Most mobile engage support; high KP ceiling for playmaking supports", 2),
    ("Senna",        ["support", "farm-independent", "carry", "vision-focused"],
     "Support that scales into a semi-carry; rewards vision and positioning", 2),
    ("Nami",         ["support", "utility", "cc-heavy", "protector"],
     "Reactive CC + heals; punishes aggressive divers protecting Bard-style KP", 1),
    ("Cassiopeia",   ["ability-damage", "mid-laner", "carry", "farm-dependent"],
     "Already in your pool — sustained DPS mage that punishes positional errors", 3),
    ("Vel'Koz",      ["ability-damage", "support", "carry", "vision-focused"],
     "Support that deals carry-level damage; no peel needed, pure poke+burst", 2),
    ("Morgana",      ["support", "mid-laner", "cc-heavy", "protector"],
     "Black Shield counters hard engage; root+ult creates easy kill setups", 1),
    ("Seraphine",    ["ability-damage", "support", "utility", "cc-heavy"],
     "AoE CC chain with heal/shield; scales to teamfight win conditions", 2),
    # Carry / fighter entries for mastery-boosted suggestions
    ("Ezreal",       ["ability-damage", "adc", "carry", "farm-dependent"],
     "Safe poke ADC with high skill ceiling; Iceborn Gauntlet or Trinity into crit is strong this patch", 2),
    ("Riven",        ["auto-attack", "top-laner", "carry", "aggressive", "high-risk"],
     "Eclipse → Sterak's snowballs hard; high mastery = free LP if you know the combos", 3),
    ("Khazix",       ["ability-damage", "jungler", "carry", "aggressive", "high-risk"],
     "One-shots isolated targets; evolve Q first, snowballs out of control from early kills", 2),
    ("LeeSin",       ["auto-attack", "jungler", "carry", "aggressive", "playmaker"],
     "Early game king; high mastery is mandatory but you clearly have it — insec plays = free wins", 3),
    ("Vayne",        ["auto-attack", "adc", "carry", "high-risk"],
     "Late game hypercarry with true damage; Guardian Angel + Kraken shreds any frontline", 3),
    ("Fiora",        ["auto-attack", "top-laner", "carry", "aggressive"],
     "Splitpush carry with true damage; true damage on vitals counters any tank comp", 3),
    ("Karthus",      ["ability-damage", "jungler", "carry", "farm-dependent"],
     "Passive farm jungler with global ult pressure; Shadowflame + Rabadon's one-shots after items", 1),
    ("Morgana",      ["ability-damage", "mid-laner", "support", "cc-heavy"],
     "Black Shield counters engage; Q root + ult creates easy kill setups in mid or support", 1),
    ("Ekko",         ["ability-damage", "mid-laner", "jungler", "carry", "aggressive", "playmaker", "high-risk"],
     "Assassin with an undo button — aggressive plays punished less; R makes diving safe; W bubble stuns grouped enemies", 2),
]


def suggest_champions(tags: list[str], role: str, masteries: list[dict], top_n: int = 6) -> list[tuple]:
    mastery_pts = {m["champion"]: m["points"] for m in masteries}
    max_pts = max(mastery_pts.values(), default=1)

    scored = []
    for champ, champ_tags, reason, diff in CHAMP_DB:
        tag_score  = sum(1 for t in tags if t in champ_tags)
        if tag_score == 0:
            continue
        # Mastery bonus: up to +2 for highly mastered champs
        pts        = mastery_pts.get(champ, 0)
        mas_bonus  = round((pts / max_pts) * 2, 2)
        total      = tag_score + mas_bonus
        scored.append((total, tag_score, mas_bonus, champ, reason, diff))

    scored.sort(key=lambda x: -x[0])
    return scored[:top_n]


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def generate_insights(p: dict, tags: list[str]) -> list[tuple[str, str]]:
    """Return list of (level, message) where level is 'good'|'warn'|'info'."""
    insights = []
    role = p["primary_role"]

    # --- Deaths ---
    if p["death_rate"] > 8:
        insights.append(("warn",
            f"You're dying {p['death_rate']:.1f} times per game on average — "
            "that's very high regardless of role. Even aggressive playmakers average ~5. "
            "Try asking: did I die before or after the play happened?"))
    elif p["death_rate"] > 5:
        insights.append(("warn",
            f"{p['death_rate']:.1f} deaths/game is above average. "
            "One less death per game compounds across a session."))

    # --- CC score vs WR mismatch (support) ---
    if role == "UTILITY" and p["avg_cc_score"] > 30 and p["wr"] < 0.48:
        insights.append(("warn",
            f"Your CC score is high ({p['avg_cc_score']:.0f}) but WR is only {p['wr']*100:.0f}%. "
            "Landing CC isn't converting to wins — check if you're engaging at the wrong time "
            "or if your team isn't following up."))

    # --- Low KP for a carry ---
    if "carry" in tags and p["avg_kp"] < 0.40:
        insights.append(("warn",
            f"Your kill participation ({p['avg_kp']*100:.0f}%) is low for a carry role. "
            "You may be farming while fights happen elsewhere — consider tracking where "
            "your team is before splitpushing or staying in lane."))

    # --- High KP but low dmg share ---
    if p["avg_kp"] > 0.55 and p["avg_dmg_share"] < 0.18 and role != "UTILITY":
        insights.append(("info",
            f"You have high KP ({p['avg_kp']*100:.0f}%) but low damage share ({p['avg_dmg_share']*100:.0f}%). "
            "You're involved in kills but not dealing the damage — "
            "you might be playing more like an enabler than a carry."))

    # --- Good vision ---
    if p["avg_vision"] > 50:
        insights.append(("good",
            f"Vision score of {p['avg_vision']:.0f} is strong — "
            "you're controlling map information well, which is rare at this elo."))

    # --- Support with good vision but still losing ---
    if role == "UTILITY" and p["avg_vision"] > 50 and p["wr"] < 0.48:
        insights.append(("info",
            "You have great vision but aren't winning. Vision alone doesn't close games — "
            "focus on whether your roam timings align with your ADC's ability to survive alone."))

    # --- Farm for mid laners ---
    if role == "MIDDLE" and p["avg_cs_pm"] > 7.5:
        insights.append(("good",
            f"CS/min of {p['avg_cs_pm']:.1f} is excellent for mid lane — "
            "you're not giving away free gold in lane."))
    elif role == "MIDDLE" and p["avg_cs_pm"] < 6.0:
        insights.append(("warn",
            f"CS/min of {p['avg_cs_pm']:.1f} is below average for mid. "
            "Even on roam-heavy games, hitting 6+ CS/min is achievable with better wave management before leaving."))

    # --- Damage share for mid carry ---
    if role == "MIDDLE" and p["avg_dmg_share"] > 0.27:
        insights.append(("good",
            f"You're outputting {p['avg_dmg_share']*100:.0f}% of your team's damage — "
            "that's carry-level contribution for a mid laner."))

    # --- Narrow champion pool ---
    if p["unique_champs"] <= 3 and p["n_games"] >= 15:
        top_champ, top_count = p["top_champs"][0]
        insights.append(("info",
            f"You're playing {p['unique_champs']} unique champions in {p['n_games']} games "
            f"({top_champ} in {top_count} of them). "
            "One-tricking is valid, but having a 2nd option for bad matchups reduces tilt losses."))

    # --- High aggression + high deaths ---
    if "aggressive" in tags and p["death_rate"] > 6:
        insights.append(("warn",
            "You play aggressively (first bloods / solo kills) but also die a lot. "
            "Aggression is good — the goal is converting leads before you give them back."))

    # --- WR above 50 ---
    if p["wr"] >= 0.55:
        insights.append(("good",
            f"{p['wr']*100:.0f}% WR means you're actively climbing — keep the pool tight "
            "and avoid off-meta experiments on this account."))

    return insights


def print_insights(insights: list[tuple[str, str]]):
    if not insights:
        return

    ICONS = {"good": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "info": "[blue]→[/blue]"}
    lines = []
    for level, msg in insights:
        icon = ICONS.get(level, "·")
        lines.append(f"{icon}  {msg}")

    console.print(Panel(
        "\n\n".join(lines),
        title="[bold]Coaching Insights[/bold]",
        border_style="magenta",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

ROLE_EMOJI = {
    "MIDDLE": "⚔  Mid", "UTILITY": " Support", "JUNGLE": " Jungle",
    "TOP": " Top", "BOTTOM": " ADC", "UNKNOWN": "? Unknown",
}

DIFF_LABEL = {1: "[green]Easy[/green]", 2: "[yellow]Medium[/yellow]", 3: "[red]Hard[/red]"}


def print_masteries(masteries: list[dict]):
    if not masteries:
        return
    t = Table(title="Champion Mastery (Top 10)", box=box.SIMPLE, show_header=True)
    t.add_column("Champion",  style="cyan", min_width=16)
    t.add_column("Level",     justify="center")
    t.add_column("Points",    justify="right")
    t.add_column("Chest",     justify="center")

    for m in masteries[:10]:
        pts_color = "green" if m["points"] >= 100_000 else ("yellow" if m["points"] >= 50_000 else "white")
        chest_str = "[green]✓[/green]" if m["chest"] else "[dim]—[/dim]"
        t.add_row(
            m["champion"],
            str(m["level"]),
            f"[{pts_color}]{m['points']:,}[/{pts_color}]",
            chest_str,
        )
    console.print(t)


def print_profile(account_label: str, p: dict, tags: list[str]):
    role_str = ROLE_EMOJI.get(p["primary_role"], p["primary_role"])
    role_dist = "  ".join(f"{ROLE_EMOJI.get(r, r)}: {n}" for r, n in p["role_dist"].items())

    top_champs_str = ", ".join(f"{c}({n})" for c, n in p["top_champs"])

    lines = [
        f"[bold]Games analyzed:[/bold] {p['n_games']}   [bold]WR:[/bold] {p['wr']*100:.0f}%",
        f"[bold]Primary role:[/bold] {role_str}   ({role_dist})",
        f"[bold]Top champs:[/bold] {top_champs_str}   ([bold]Unique:[/bold] {p['unique_champs']})",
        "",
        f"[bold]Avg KP:[/bold] {p['avg_kp']*100:.0f}%   "
        f"[bold]Dmg share:[/bold] {p['avg_dmg_share']*100:.0f}%   "
        f"[bold]CS/min:[/bold] {p['avg_cs_pm']:.1f}",
        f"[bold]Avg CC score:[/bold] {p['avg_cc_score']:.1f}   "
        f"[bold]Vision:[/bold] {p['avg_vision']:.0f}   "
        f"[bold]Deaths/game:[/bold] {p['death_rate']:.1f}",
        f"[bold]Magic dmg:[/bold] {p['avg_magic_ratio']*100:.0f}%   "
        f"[bold]Physical dmg:[/bold] {p['avg_physical_ratio']*100:.0f}%",
        "",
        f"[bold]Playstyle tags:[/bold] {', '.join(f'[cyan]{t}[/cyan]' for t in tags)}",
    ]

    console.print(Panel("\n".join(lines), title=f"[bold]Playstyle Profile[/bold]", border_style="blue"))


def print_suggestions(suggestions: list[tuple]):
    table = Table(title="Champion Suggestions", box=box.ROUNDED, show_lines=True)
    table.add_column("Champion",   style="cyan", min_width=14)
    table.add_column("Match",      justify="center")
    table.add_column("Mastery",    justify="right")
    table.add_column("Difficulty", justify="center")
    table.add_column("Why you'd like it", max_width=55)

    for total, tag_score, mas_bonus, champ, reason, diff in suggestions:
        stars     = "★" * min(4, tag_score) + "☆" * max(0, 4 - tag_score)
        if mas_bonus >= 1.5:
            mas_str = f"[green]{mas_bonus:+.1f} (high)[/green]"
        elif mas_bonus >= 0.5:
            mas_str = f"[yellow]{mas_bonus:+.1f}[/yellow]"
        else:
            mas_str = f"[dim]{mas_bonus:+.1f}[/dim]"
        table.add_row(champ, f"[yellow]{stars}[/yellow]", mas_str, DIFF_LABEL[diff], reason)

    console.print(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Playstyle analyzer")
    parser.add_argument("account", choices=list(ACCOUNTS.keys()), help="Account label")
    parser.add_argument("--games", type=int, default=30, help="Number of games to analyze")
    args = parser.parse_args()

    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    console.print(f"\n[bold]Fetching masteries for [cyan]{account_str}[/cyan]...[/bold]")
    masteries = fetch_masteries(puuid, top_n=20)

    rows = fetch_profile_data(args.account, n_games=args.games)
    if not rows:
        console.print("[red]No data.[/red]")
        return

    profile     = compute_profile(rows)
    tags        = classify_playstyle(profile)
    suggestions = suggest_champions(tags, profile["primary_role"], masteries)
    insights    = generate_insights(profile, tags)

    print_profile(args.account, profile, tags)
    print_masteries(masteries)
    print_insights(insights)
    print_suggestions(suggestions)


if __name__ == "__main__":
    main()
