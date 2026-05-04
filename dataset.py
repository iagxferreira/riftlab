"""
dataset.py — Fetch ranked games and store them in matches.csv (no database).

Incremental — skips matches already in the CSV.

Usage:
  python dataset.py fetch              # fetch all games for both accounts
  python dataset.py fetch --account main
  python dataset.py fetch --account lab
  python dataset.py stats              # print stats from the CSV
  python dataset.py stats --account main
"""

import csv
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich import box

from lol_stats import get_account, get_match_ids, get_match, extract_participant, ACCOUNTS

load_dotenv()
console = Console()

CSV_PATH = Path("matches.csv")

FIELDNAMES = [
    "match_id", "account", "puuid", "champion", "win",
    "kills", "deaths", "assists", "cs", "damage", "vision", "kp",
    "duration_min", "timestamp", "patch", "queue",
    "double_kills", "triple_kills", "quadra_kills", "penta_kills",
]

EARLIEST_EPOCH = 1450915200


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_stored_ids() -> set[tuple[str, str]]:
    """Return set of (match_id, puuid) already in the CSV."""
    if not CSV_PATH.exists():
        return set()
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        return {(r["match_id"], r["puuid"]) for r in reader}


def load_rows() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="") as f:
        return list(csv.DictReader(f))


def append_rows(rows: list[dict]):
    write_header = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_account(account_label: str):
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    console.print(f"\n[bold cyan]Fetching match IDs for {account_str}...[/bold cyan]")
    match_ids = get_match_ids(puuid, count=0, start_time=EARLIEST_EPOCH)
    console.print(f"[dim]Found {len(match_ids)} ranked games in API history.[/dim]")

    stored = load_stored_ids()
    new_ids = [mid for mid in match_ids if (mid, puuid) not in stored]
    console.print(f"[dim]{len(match_ids) - len(new_ids)} already in CSV, fetching {len(new_ids)} new.[/dim]")

    if not new_ids:
        console.print("[green]All up to date.[/green]")
        return

    new_rows: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Fetching {account_str}", total=len(new_ids))

        for match_id in new_ids:
            try:
                row = _fetch_one(match_id, puuid, account_label)
                if row:
                    new_rows.append(row)
                    append_rows([row])
                time.sleep(0.05)
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    progress.print("[yellow]Rate limited — waiting 70s[/yellow]")
                    time.sleep(70)
                    try:
                        row = _fetch_one(match_id, puuid, account_label)
                        if row:
                            new_rows.append(row)
                            append_rows([row])
                    except Exception:
                        pass
            finally:
                progress.advance(task)

    console.print(f"[green]Added {len(new_rows)} new games to {CSV_PATH}.[/green]")


def _fetch_one(match_id: str, puuid: str, account_label: str) -> dict | None:
    match = get_match(match_id)
    p     = extract_participant(match, puuid)
    if not p:
        return None
    info = match["info"]
    dur  = info["gameDuration"] / 60
    if dur < 5:
        return None

    total_cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
    team     = [x for x in info["participants"] if x["teamId"] == p["teamId"]]
    team_k   = sum(x["kills"] for x in team) or 1
    kp       = (p["kills"] + p["assists"]) / team_k
    patch    = ".".join(info.get("gameVersion", "0.0").split(".")[:2])

    return {
        "match_id":     match_id,
        "account":      account_label,
        "puuid":        puuid,
        "champion":     p["championName"],
        "win":          int(p["win"]),
        "kills":        p["kills"],
        "deaths":       p["deaths"],
        "assists":      p["assists"],
        "cs":           total_cs,
        "damage":       p["totalDamageDealtToChampions"],
        "vision":       p["visionScore"],
        "kp":           round(kp, 3),
        "duration_min": round(dur, 1),
        "timestamp":    info["gameCreation"] // 1000,
        "patch":        patch,
        "queue":        info.get("queueId", 420),
        "double_kills": p.get("doubleKills", 0),
        "triple_kills": p.get("tripleKills", 0),
        "quadra_kills": p.get("quadraKills", 0),
        "penta_kills":  p.get("pentaKills", 0),
    }


# ---------------------------------------------------------------------------
# Stats display
# ---------------------------------------------------------------------------

def print_stats(account_label: str | None):
    rows = load_rows()
    if account_label:
        rows = [r for r in rows if r["account"] == account_label]

    if not rows:
        console.print("[yellow]No data in CSV yet — run fetch first.[/yellow]")
        return

    total = len(rows)
    wins  = sum(int(r["win"]) for r in rows)
    wr    = wins / total * 100

    timestamps = [int(r["timestamp"]) for r in rows if r["timestamp"]]
    first_dt = datetime.fromtimestamp(min(timestamps), tz=timezone.utc).strftime("%b %Y") if timestamps else "?"
    last_dt  = datetime.fromtimestamp(max(timestamps), tz=timezone.utc).strftime("%b %Y") if timestamps else "?"

    label = account_label or "all accounts"
    wr_c  = "green" if wr >= 55 else ("red" if wr < 45 else "yellow")
    console.print(f"\n[bold]{label}[/bold]  {total} games  [{wr_c}]{wins}W/{total-wins}L  {wr:.1f}% WR[/{wr_c}]  [{first_dt} → {last_dt}]")

    # Multikill totals
    mk = {k: sum(int(r.get(k, 0) or 0) for r in rows) for k in ("double_kills", "triple_kills", "quadra_kills", "penta_kills")}
    mk_parts = []
    if mk["double_kills"]: mk_parts.append(f"[cyan]{mk['double_kills']}[/cyan] doubles")
    if mk["triple_kills"]: mk_parts.append(f"[yellow]{mk['triple_kills']}[/yellow] triples")
    if mk["quadra_kills"]: mk_parts.append(f"[magenta]{mk['quadra_kills']}[/magenta] quadras")
    if mk["penta_kills"]:  mk_parts.append(f"[bold red]{mk['penta_kills']}[/bold red] pentas")
    if mk_parts:
        console.print("  Multikills: " + "  /  ".join(mk_parts))

    # Per-champion breakdown
    from collections import defaultdict
    champs: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        champs[r["champion"]].append(r)

    t = Table(box=box.ROUNDED, show_header=True)
    t.add_column("Champion",   style="cyan", min_width=14)
    t.add_column("Games",      justify="right")
    t.add_column("WR%",        justify="right")
    t.add_column("KDA",        justify="right")
    t.add_column("Avg CS",     justify="right")
    t.add_column("Avg Dmg",    justify="right")
    t.add_column("Avg KP",     justify="right")
    t.add_column("Multikills", justify="right")

    champ_rows = sorted(
        [(c, rs) for c, rs in champs.items() if len(rs) >= 3],
        key=lambda x: -len(x[1]),
    )
    for champ, rs in champ_rows:
        g   = len(rs)
        w   = sum(int(r["win"]) for r in rs)
        k   = sum(int(r["kills"])   for r in rs) / g
        d   = sum(int(r["deaths"])  for r in rs) / g or 1
        a   = sum(int(r["assists"]) for r in rs) / g
        cs  = sum(int(r["cs"])      for r in rs) / g
        dmg = sum(int(r["damage"])  for r in rs) / g
        kp  = sum(float(r["kp"])    for r in rs) / g
        db  = sum(int(r.get("double_kills", 0) or 0) for r in rs)
        tr  = sum(int(r.get("triple_kills", 0) or 0) for r in rs)
        qd  = sum(int(r.get("quadra_kills", 0) or 0) for r in rs)
        pt  = sum(int(r.get("penta_kills",  0) or 0) for r in rs)

        wr_c2 = "green" if w/g >= 0.55 else ("red" if w/g < 0.45 else "yellow")
        kda   = f"{(k + a) / d:.2f}"
        mk_str = ""
        if pt: mk_str += f"[bold red]{pt}P[/bold red] "
        if qd: mk_str += f"[magenta]{qd}Q[/magenta] "
        if tr: mk_str += f"[yellow]{tr}T[/yellow] "
        if db: mk_str += f"[cyan]{db}D[/cyan]"
        t.add_row(
            champ, str(g),
            f"[{wr_c2}]{w/g*100:.0f}%[/{wr_c2}]",
            kda, str(int(cs)), f"{int(dmg):,}", f"{kp*100:.0f}%",
            mk_str.strip() or "—",
        )

    console.print(t)

    # Patch trend
    from collections import Counter
    patch_wins:  dict[str, int] = defaultdict(int)
    patch_games: Counter        = Counter()
    for r in rows:
        p = r.get("patch", "0.0")
        if p and p != "0.0":
            patch_games[p] += 1
            patch_wins[p]  += int(r["win"])

    recent = sorted(patch_games, reverse=True)[:5]
    if recent:
        pt = Table(title="Recent patches", box=box.SIMPLE)
        pt.add_column("Patch"); pt.add_column("Games", justify="right"); pt.add_column("WR", justify="right")
        for p in recent:
            g   = patch_games[p]
            wr_p = patch_wins[p] / g * 100
            c   = "green" if wr_p >= 55 else ("red" if wr_p < 45 else "yellow")
            pt.add_row(p, str(g), f"[{c}]{wr_p:.0f}%[/{c}]")
        console.print(pt)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Local match dataset manager (CSV)")
    parser.add_argument("command", choices=["fetch", "stats"])
    parser.add_argument("--account", choices=list(ACCOUNTS.keys()), default=None)
    args = parser.parse_args()

    if args.command == "fetch":
        accounts = [args.account] if args.account else list(ACCOUNTS.keys())
        for label in accounts:
            fetch_account(label)
        print_stats(args.account)

    elif args.command == "stats":
        print_stats(args.account)


if __name__ == "__main__":
    main()
