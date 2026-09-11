"""
form.py — Recent form over the last few ranked games, plus a simple tilt heuristic.
"""

from rich.panel import Panel

from riftlab.riot import get_match_ids, extract_participant, console
from riftlab.cache import get_match_cached, is_excluded


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
