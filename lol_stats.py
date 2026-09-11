"""
lol_stats.py — RiftLab rank overview, per-champion stats, and shared Riot API client.
Accounts loaded from accounts.json — add/rename freely.
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv()
console = Console()

API_KEY = os.getenv("RIOT_API_KEY", "")

# ---------------------------------------------------------------------------
# Account loading — edit accounts.json to add/rename/change region
# ---------------------------------------------------------------------------

def _load_accounts() -> dict:
    path = Path(__file__).parent / "accounts.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    # fallback: env vars (legacy)
    return {
        "main": {"riot_id": os.getenv("ACCOUNT_MAIN", ""), "region": "br1", "routing": "americas"},
        "lab":  {"riot_id": os.getenv("ACCOUNT_LAB",  ""), "region": "br1", "routing": "americas"},
    }

_ACCOUNTS_RAW: dict = _load_accounts()

# ACCOUNTS maps label → "GameName#TAG" (used throughout codebase)
ACCOUNTS: dict[str, str] = {label: data["riot_id"] for label, data in _ACCOUNTS_RAW.items()}

# Default region/routing from first account (used for module-level URL constants)
_first = next(iter(_ACCOUNTS_RAW.values()), {})
REGION  = _first.get("region",  os.getenv("REGION",          "br1"))
ROUTING = _first.get("routing", os.getenv("REGION_ROUTING",  "americas"))

BASE_ACCOUNT  = f"https://{ROUTING}.api.riotgames.com"
BASE_SUMMONER = f"https://{REGION}.api.riotgames.com"


def get_account_urls(label: str) -> tuple[str, str]:
    """Return (BASE_ACCOUNT, BASE_SUMMONER) for a specific account label."""
    data    = _ACCOUNTS_RAW.get(label, _first)
    region  = data.get("region",  REGION)
    routing = data.get("routing", ROUTING)
    return f"https://{routing}.api.riotgames.com", f"https://{region}.api.riotgames.com"

FOCUS_CHAMPS = {"Cassiopeia", "Syndra"}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> dict:
    headers = {"X-Riot-Token": API_KEY}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        console.print(f"[yellow]Rate limited — waiting {retry_after}s[/yellow]")
        time.sleep(retry_after)
        return _get(url, params)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Riot Account v1
# ---------------------------------------------------------------------------

def get_account(game_name: str, tag_line: str) -> dict:
    url = f"{BASE_ACCOUNT}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    return _get(url)


def get_summoner_by_puuid(puuid: str) -> dict:
    url = f"{BASE_SUMMONER}/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return _get(url)


# ---------------------------------------------------------------------------
# Ranked stats
# ---------------------------------------------------------------------------

def get_ranked_stats(puuid: str) -> list[dict]:
    url = f"{BASE_SUMMONER}/lol/league/v4/entries/by-puuid/{puuid}"
    return _get(url)


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


# ---------------------------------------------------------------------------
# Match history
# ---------------------------------------------------------------------------

def get_match_ids(puuid: str, count: int = 20, queue: int = 420,
                  start_time: int = None, end_time: int = None) -> list[str]:
    """queue 420 = Solo/Duo ranked. Paginates automatically.
    count=0 fetches ALL available games (slow). start_time/end_time are epoch seconds."""
    url = f"{BASE_ACCOUNT}/lol/match/v5/matches/by-puuid/{puuid}/ids"
    fetch_all = (count == 0)
    ids = []
    start = 0
    while True:
        params = {"queue": queue, "count": 100 if fetch_all else min(100, count - len(ids)), "start": start}
        if start_time: params["startTime"] = start_time
        if end_time:   params["endTime"]   = end_time
        batch = _get(url, params)
        if not batch:
            break
        ids.extend(batch)
        if len(batch) < 100:
            break
        if not fetch_all and len(ids) >= count:
            break
        start += len(batch)
        time.sleep(0.05)
    return ids


def get_match(match_id: str) -> dict:
    url = f"{BASE_ACCOUNT}/lol/match/v5/matches/{match_id}"
    return _get(url)


def extract_participant(match: dict, puuid: str) -> dict | None:
    for p in match["info"]["participants"]:
        if p["puuid"] == puuid:
            return p
    return None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

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
