"""
last_match.py — Deep review of your last ranked game with improvement tips.
Usage: python -m riftlab.analysis.last_match [lab|main]
"""

import os
import sys
import time
import argparse

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from riftlab.riot import (
    get_account, get_match_ids, get_match, extract_participant,
    BASE_ACCOUNT, _get, ACCOUNTS, console,
)
from riftlab.cache import get_match_cached, is_excluded
from riftlab.advisors.pregame import get_recent_form, print_tilt_check

load_dotenv()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_timeline(match_id: str) -> dict:
    url = f"{BASE_ACCOUNT}/lol/match/v5/matches/{match_id}/timeline"
    return _get(url)


def get_participant_id(match: dict, puuid: str) -> int:
    for p in match["info"]["participants"]:
        if p["puuid"] == puuid:
            return p["participantId"]
    return -1


def get_cs_at_minute(timeline: dict, participant_id: int, minute: int) -> int:
    """Read CS from the timeline frame closest to `minute`."""
    frames = timeline["info"]["frames"]
    target_ms = minute * 60 * 1000
    frame = min(frames, key=lambda f: abs(f["timestamp"] - target_ms))
    pf = frame["participantFrames"].get(str(participant_id), {})
    return pf.get("minionsKilled", 0) + pf.get("jungleMinionsKilled", 0)


def get_death_timings(timeline: dict, participant_id: int) -> list[dict]:
    """Return list of deaths with timestamp in minutes and game phase."""
    deaths = []
    for frame in timeline["info"]["frames"]:
        for event in frame["events"]:
            if event.get("type") == "CHAMPION_KILL" and event.get("victimId") == participant_id:
                minute = event["timestamp"] // 60000
                if minute <= 14:
                    phase = "early"
                elif minute <= 24:
                    phase = "mid"
                else:
                    phase = "late"
                deaths.append({"minute": minute, "phase": phase})
    return deaths


# ---------------------------------------------------------------------------
# Comparison baseline (last 10 games, same role/champ)
# ---------------------------------------------------------------------------

def build_baseline(puuid: str, current_champ: str, current_position: str, skip_id: str) -> dict | None:
    """Average stats from last 10 games on same champ, fallback to same role, fallback to all."""
    match_ids = get_match_ids(puuid, count=15)
    rows = []
    for mid in match_ids:
        if mid == skip_id:
            continue
        try:
            m = get_match(mid)
            p = extract_participant(m, puuid)
            if not p:
                continue
            dur = m["info"]["gameDuration"] / 60
            if dur < 5:
                continue
            rows.append(p)
        except Exception:
            pass
        time.sleep(0.05)
        if len(rows) >= 10:
            break

    if not rows:
        return None

    def _avg(key):
        vals = [r.get(key, 0) for r in rows]
        return sum(vals) / len(vals)

    def _avg_cs_pm():
        vals = [(r["totalMinionsKilled"] + r["neutralMinionsKilled"]) for r in rows]
        return sum(vals) / len(vals) / 30  # assume ~30 min avg

    ch = [r.get("challenges", {}) for r in rows]

    def _avg_ch(key):
        vals = [c.get(key, 0) for c in ch]
        return sum(vals) / len(vals)

    return {
        "kills":       _avg("kills"),
        "deaths":      _avg("deaths"),
        "assists":     _avg("assists"),
        "vision":      _avg("visionScore"),
        "cs_pm":       _avg_cs_pm(),
        "kp":          _avg_ch("killParticipation"),
        "dmg_pm":      _avg_ch("damagePerMinute"),
        "control_wards": _avg_ch("controlWardsPlaced"),
        "n":           len(rows),
    }


# ---------------------------------------------------------------------------
# Coaching feedback
# ---------------------------------------------------------------------------

def generate_feedback(p: dict, deaths: list[dict], baseline: dict | None, duration_m: float) -> list[tuple[str, str]]:
    feedback = []
    ch = p.get("challenges", {})
    role = p.get("teamPosition", "")
    is_support = role == "UTILITY"
    is_mid = role == "MIDDLE"
    is_jungle = role == "JUNGLE"

    kills, deaths_count, assists = p["kills"], p["deaths"], p["assists"]
    cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
    cs_pm = cs / duration_m
    kp = ch.get("killParticipation", 0)
    dmg_pm = ch.get("damagePerMinute", 0)
    time_dead = p.get("totalTimeSpentDead", 0)
    time_dead_pct = (time_dead / (duration_m * 60)) * 100
    bounty_gold = ch.get("bountyGold", 0)
    control_wards = ch.get("controlWardsPlaced", 0)
    vision_score = p.get("visionScore", 0)
    cs_at_10 = ch.get("laneMinionsFirst10Minutes", 0)
    skillshots_hit = ch.get("skillshotsHit", 0)
    skillshots_dodged = ch.get("skillshotsDodged", 0)
    solo_kills = ch.get("soloKills", 0)
    saved_allies = ch.get("saveAllyFromDeath", 0)
    dmg_taken_pct = ch.get("damageTakenOnTeamPercentage", 0)
    team_dmg_pct = ch.get("teamDamagePercentage", 0)

    # --- Deaths: count ---
    death_threshold = 4 if is_support else 5
    if deaths_count > death_threshold + 2:
        feedback.append(("warn",
            f"[bold]{deaths_count} deaths[/bold] is a lot. "
            f"You spent [bold]{time_dead_pct:.0f}%[/bold] of the game dead "
            f"({time_dead // 60}m{time_dead % 60:02d}s off the map). "
            "Every second dead is a second your team plays 4v5."))
    elif deaths_count > death_threshold:
        feedback.append(("warn",
            f"{deaths_count} deaths — slightly above a clean game. "
            f"That's {time_dead // 60}m{time_dead % 60:02d}s of dead time. "
            "Try to identify one death that was preventable."))
    elif deaths_count <= 2:
        feedback.append(("good", f"Only {deaths_count} deaths — great survivability this game."))

    # --- Deaths: timing ---
    if deaths:
        phase_counts = {"early": 0, "mid": 0, "late": 0}
        for d in deaths:
            phase_counts[d["phase"]] += 1
        if phase_counts["early"] >= 2:
            early_minutes = [d["minute"] for d in deaths if d["phase"] == "early"]
            feedback.append(("warn",
                f"[bold]{phase_counts['early']} early deaths[/bold] "
                f"(at {', '.join(str(m) + 'm' for m in early_minutes)}). "
                "Dying before 15 min sets your entire game behind — "
                "consider playing more conservatively until you have your first item."))
        if phase_counts["late"] >= 2:
            feedback.append(("warn",
                f"{phase_counts['late']} late-game deaths. Late deaths often mean "
                "fighting in bad positions — around baron/dragon without vision, or "
                "staying too long after a fight."))

    # --- Bounty gold given ---
    if bounty_gold > 500:
        feedback.append(("warn",
            f"You gave [bold]{int(bounty_gold)} gold[/bold] in bounties. "
            "That means you were ahead at some point and died, feeding gold back. "
            "When you have a bounty, play safer — one death erases your lead."))

    # --- CS ---
    if not is_support:
        if is_mid or is_jungle:
            target_cs_10 = 70 if is_mid else 45
            if cs_at_10 > 0 and cs_at_10 < target_cs_10 - 10:
                feedback.append(("warn",
                    f"[bold]{cs_at_10} CS at 10 min[/bold] (target ~{target_cs_10}). "
                    "Low early CS usually means dying, roaming too early, or missing last hits under pressure."))
            elif cs_at_10 >= target_cs_10:
                feedback.append(("good", f"{cs_at_10} CS at 10 min — solid laning phase."))

        if cs_pm < 5.5 and not is_support:
            feedback.append(("warn",
                f"[bold]{cs_pm:.1f} CS/min[/bold] overall — below average. "
                "Try to stay at 7+ CS/min on mid, 6+ on other carry roles."))
        elif cs_pm >= 7.5 and not is_support:
            feedback.append(("good", f"{cs_pm:.1f} CS/min — excellent farming."))

    # --- Kill participation ---
    kp_threshold = 0.45 if is_support else 0.40
    if kp < kp_threshold:
        feedback.append(("warn",
            f"[bold]{kp*100:.0f}% kill participation[/bold] is low. "
            "You were absent from too many fights — "
            f"{'check your roam timings' if is_support else 'try to track where the fights are happening'}."))
    elif kp > 0.70:
        feedback.append(("good", f"{kp*100:.0f}% kill participation — you were everywhere."))

    # --- Vision ---
    vision_pm = vision_score / duration_m
    if is_support and vision_pm < 1.5:
        feedback.append(("warn",
            f"[bold]Vision score {vision_score}[/bold] ({vision_pm:.1f}/min) is low for support. "
            "As support you should be driving ward coverage, especially around objectives."))
    elif not is_support and control_wards == 0:
        feedback.append(("warn",
            "You placed [bold]0 control wards[/bold] this game. "
            "1-2 control wards per game is a cheap habit that prevents a lot of deaths."))
    elif vision_score > 50:
        feedback.append(("good", f"Vision score {vision_score} — strong map control."))

    # --- Skillshots (for ability-based champs) ---
    if skillshots_hit + skillshots_dodged > 10:
        if skillshots_hit < skillshots_dodged * 0.5:
            feedback.append(("warn",
                f"Skillshot accuracy: [bold]{skillshots_hit} hit vs {skillshots_dodged} dodged[/bold]. "
                "You're missing more than landing — slow down your ability usage and wait for better angles."))
        elif skillshots_hit > skillshots_dodged:
            feedback.append(("good",
                f"Good skillshot accuracy: {skillshots_hit} hit vs {skillshots_dodged} dodged."))

    # --- Comparison to baseline ---
    if baseline:
        n = baseline["n"]
        if deaths_count > baseline["deaths"] + 2:
            feedback.append(("info",
                f"You died [bold]{deaths_count - baseline['deaths']:.1f} more times[/bold] than your "
                f"recent average ({baseline['deaths']:.1f}/game over {n} games)."))
        if not is_support and cs_pm < baseline["cs_pm"] - 1.0:
            feedback.append(("info",
                f"CS/min was [bold]{baseline['cs_pm'] - cs_pm:.1f} below[/bold] your recent average "
                f"({baseline['cs_pm']:.1f}/min over {n} games)."))
        if kp > baseline["kp"] + 0.10:
            feedback.append(("good",
                f"KP {kp*100:.0f}% — [bold]{(kp - baseline['kp'])*100:.0f}% higher[/bold] than your "
                f"recent average ({baseline['kp']*100:.0f}%)."))

    # --- Damage taken ---
    if dmg_taken_pct > 0.30 and not (role in ("TOP", "JUNGLE")):
        feedback.append(("warn",
            f"You absorbed [bold]{dmg_taken_pct*100:.0f}%[/bold] of your team's damage taken. "
            "For a non-tank, that usually means overextending or standing in AoE too long."))

    # --- Solo kills ---
    if solo_kills >= 2:
        feedback.append(("good", f"{solo_kills} solo kills — you created your own advantages."))

    # --- Saved allies ---
    if saved_allies >= 1:
        feedback.append(("good", f"Saved {saved_allies} ally from death — high-impact support play."))

    # --- Win/loss context ---
    if not p["win"] and deaths_count <= 3 and kp >= 0.50:
        feedback.append(("info",
            "You played a clean game but still lost. "
            "Sometimes the macro loss isn't on you — focus on what you controlled well."))

    return feedback


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_scoreboard(match: dict, puuid: str):
    teams = {100: [], 200: []}
    for p in match["info"]["participants"]:
        teams[p["teamId"]].append(p)

    our_team_id = next(p["teamId"] for p in match["info"]["participants"] if p["puuid"] == puuid)

    for team_id, players in teams.items():
        label = "[green]Your team[/green]" if team_id == our_team_id else "[red]Enemy team[/red]"
        t = Table(title=label, box=box.SIMPLE, show_header=True)
        t.add_column("Champion", style="cyan", min_width=14)
        t.add_column("KDA", justify="center")
        t.add_column("CS", justify="right")
        t.add_column("Dmg dealt", justify="right")
        t.add_column("Dmg taken", justify="right")
        t.add_column("Gold", justify="right")
        t.add_column("Vision", justify="right")
        t.add_column("CC time", justify="right")

        for p in players:
            kda = f"{p['kills']}/{p['deaths']}/{p['assists']}"
            cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
            dmg_dealt = f"{p['totalDamageDealtToChampions']:,}"
            dmg_taken = f"{p['totalDamageTaken']:,}"
            gold = f"{p['goldEarned']:,}"
            vis = str(p["visionScore"])
            cc_time = f"{p.get('timeCCingOthers', 0)}s"
            style = "bold" if p["puuid"] == puuid else ""
            name = f"[bold]{p['championName']}[/bold]" if p["puuid"] == puuid else p["championName"]
            t.add_row(name, kda, str(cs), dmg_dealt, dmg_taken, gold, vis, cc_time, style=style)
        console.print(t)


def print_match_header(p: dict, duration_m: float, match_id: str):
    result = "[green]WIN[/green]" if p["win"] else "[red]LOSS[/red]"
    ch = p.get("challenges", {})
    kda_ratio = ch.get("kda", 0)
    kp = ch.get("killParticipation", 0)
    cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
    cs_pm = cs / duration_m

    lines = [
        f"{result}  —  [bold cyan]{p['championName']}[/bold cyan]  "
        f"({p.get('teamPosition', '?')})   {duration_m:.0f} min",
        "",
        f"[bold]KDA:[/bold] {p['kills']}/{p['deaths']}/{p['assists']}  "
        f"({kda_ratio:.2f} ratio)   "
        f"[bold]KP:[/bold] {kp*100:.0f}%",
        f"[bold]CS:[/bold] {cs} ({cs_pm:.1f}/min)   "
        f"[bold]Damage:[/bold] {p['totalDamageDealtToChampions']:,}   "
        f"[bold]Vision:[/bold] {p['visionScore']}",
        f"[bold]Control wards:[/bold] {p.get('detectorWardsPlaced', 0) + ch.get('controlWardsPlaced', 0)}   "
        f"[bold]Dead time:[/bold] {p.get('totalTimeSpentDead', 0) // 60}m{p.get('totalTimeSpentDead', 0) % 60:02d}s   "
        f"[bold]Bounty given:[/bold] {int(ch.get('bountyGold', 0))}g",
    ]
    border = "green" if p["win"] else "red"
    console.print(Panel("\n".join(lines), title="[bold]Last Match[/bold]", border_style=border))


def print_feedback(feedback: list[tuple[str, str]]):
    if not feedback:
        console.print("[dim]No specific feedback generated.[/dim]")
        return

    ICONS = {"good": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "info": "[blue]→[/blue]"}
    lines = []
    for level, msg in feedback:
        icon = ICONS.get(level, "·")
        lines.append(f"{icon}  {msg}")

    console.print(Panel(
        "\n\n".join(lines),
        title="[bold]Improvement Notes[/bold]",
        border_style="magenta",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Last match deep review")
    parser.add_argument("account", choices=list(ACCOUNTS.keys()))
    args = parser.parse_args()

    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)

    console.print(f"\n[bold]Fetching last ranked game for [cyan]{account_str}[/cyan]...[/bold]\n")

    acct = get_account(game_name, tag)
    puuid = acct["puuid"]

    match_ids = get_match_ids(puuid, count=10)
    if not match_ids:
        console.print("[red]No ranked games found.[/red]")
        return

    mid = next((m for m in match_ids if not is_excluded(m)), None)
    if not mid:
        console.print("[red]All recent games are excluded.[/red]")
        return
    match = get_match_cached(mid)
    p = extract_participant(match, puuid)
    if not p:
        console.print("[red]Could not find your data in the match.[/red]")
        return

    duration_m = match["info"]["gameDuration"] / 60
    participant_id = get_participant_id(match, puuid)

    console.print("[dim]Fetching timeline...[/dim]")
    timeline = get_timeline(mid)
    death_timings = get_death_timings(timeline, participant_id)
    cs_at_10 = get_cs_at_minute(timeline, participant_id, 10)

    # Inject cs@10 into challenges so feedback can read it
    p.setdefault("challenges", {})["laneMinionsFirst10Minutes"] = cs_at_10

    console.print("[dim]Fetching recent games for comparison...[/dim]\n")
    baseline = build_baseline(puuid, p["championName"], p.get("teamPosition", ""), mid)

    print_match_header(p, duration_m, mid)
    print_scoreboard(match, puuid)
    feedback = generate_feedback(p, death_timings, baseline, duration_m)
    print_feedback(feedback)

    # Tilt check after the review
    recent_form = get_recent_form(puuid, n=5)
    print_tilt_check(recent_form)


if __name__ == "__main__":
    main()
