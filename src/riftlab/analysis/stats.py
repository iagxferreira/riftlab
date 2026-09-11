"""
stats.py — Rank overview and per-champion stats for all configured accounts.

Usage: python -m riftlab.analysis.stats
"""

import sys
import time

from rich.table import Table
from rich import box

from riftlab.riot import (
    API_KEY, ACCOUNTS, console,
    get_account, get_ranked_stats, get_match_ids, get_match, extract_participant,
)

FOCUS_CHAMPS = {"Cassiopeia", "Syndra"}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def format_rank(entries: list[dict]) -> str:
    soloq = next((e for e in entries if e["queueType"] == "RANKED_SOLO_5x5"), None)
    if not soloq:
        return "Unranked"
    wins, losses = soloq["wins"], soloq["losses"]
    total = wins + losses
    wr = f"{wins / total * 100:.1f}%" if total else "N/A"
    return (
        f"{soloq['tier']} {soloq['rank']} {soloq['leaguePoints']} LP  "
        f"({wins}W/{losses}L  {wr} WR)"
    )


def analyze_matches(puuid: str, match_ids: list[str]) -> list[dict]:
    rows = []
    for mid in match_ids:
        try:
            match = get_match(mid)
            p = extract_participant(match, puuid)
            if not p:
                continue
            kda = (
                f"{p['kills']}/{p['deaths']}/{p['assists']}"
            )
            rows.append({
                "champion":   p["championName"],
                "win":        p["win"],
                "kda":        kda,
                "kills":      p["kills"],
                "deaths":     p["deaths"],
                "assists":    p["assists"],
                "cs":         p["totalMinionsKilled"] + p["neutralMinionsKilled"],
                "vision":     p["visionScore"],
                "damage":     p["totalDamageDealtToChampions"],
                "duration_m": match["info"]["gameDuration"] // 60,
                "match_id":   mid,
            })
        except Exception as exc:
            console.print(f"[red]Failed {mid}: {exc}[/red]")
        time.sleep(0.05)  # respect rate limit
    return rows


def champ_summary(rows: list[dict]) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    for r in rows:
        c = r["champion"]
        if c not in summary:
            summary[c] = {"games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0, "cs": 0, "damage": 0}
        s = summary[c]
        s["games"] += 1
        s["wins"] += int(r["win"])
        s["kills"] += r["kills"]
        s["deaths"] += r["deaths"]
        s["assists"] += r["assists"]
        s["cs"] += r["cs"]
        s["damage"] += r["damage"]
    return summary


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_rank_overview():
    table = Table(title="Rank Overview", box=box.ROUNDED)
    table.add_column("Account", style="cyan")
    table.add_column("Summoner")
    table.add_column("Solo/Duo Rank", style="yellow")

    for label, account_str in ACCOUNTS.items():
        game_name, tag = account_str.rsplit("#", 1)
        try:
            acct = get_account(game_name, tag)
            entries = get_ranked_stats(acct["puuid"])
            rank_str = format_rank(entries)
            table.add_row(f"{label} ({account_str})", acct["gameName"], rank_str)
        except Exception as exc:
            table.add_row(account_str, "—", f"[red]Error: {exc}[/red]")

    console.print(table)


def print_champ_stats(account_label: str, count: int = 20):
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)

    console.print(f"\n[bold]Fetching last {count} ranked games for [cyan]{account_str}[/cyan]...[/bold]")

    acct = get_account(game_name, tag)
    puuid = acct["puuid"]
    match_ids = get_match_ids(puuid, count=count)
    rows = analyze_matches(puuid, match_ids)

    if not rows:
        console.print("[red]No match data found.[/red]")
        return

    summary = champ_summary(rows)
    sorted_champs = sorted(summary.items(), key=lambda x: x[1]["games"], reverse=True)

    table = Table(title=f"Champion Stats — {account_str} (last {count} ranked)", box=box.ROUNDED)
    table.add_column("Champion", style="cyan")
    table.add_column("Games", justify="right")
    table.add_column("WR%", justify="right")
    table.add_column("KDA", justify="right")
    table.add_column("Avg CS", justify="right")
    table.add_column("Avg Dmg", justify="right")
    table.add_column("Focus", justify="center")

    for champ, s in sorted_champs:
        g = s["games"]
        wr = f"{s['wins'] / g * 100:.0f}%"
        deaths = s["deaths"] or 1
        kda = f"{(s['kills'] + s['assists']) / deaths:.2f}"
        avg_cs = f"{s['cs'] / g:.0f}"
        avg_dmg = f"{s['damage'] / g:,.0f}"
        focus = "[green]★[/green]" if champ in FOCUS_CHAMPS else ""
        table.add_row(champ, str(g), wr, kda, avg_cs, avg_dmg, focus)

    console.print(table)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not API_KEY or API_KEY.startswith("RGAPI-xxx"):
        console.print("[red]Set RIOT_API_KEY in your .env file.[/red]")
        sys.exit(1)

    console.print("[bold green]RiftLab — Rank & Champion Stats[/bold green]\n")
    print_rank_overview()

    for label in ACCOUNTS:
        try:
            print_champ_stats(label, count=20)
        except Exception as exc:
            console.print(f"[red]Error fetching {label}: {exc}[/red]")


if __name__ == "__main__":
    main()
