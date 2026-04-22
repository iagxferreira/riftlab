"""
dataset.py — Build and query a local SQLite dataset of all ranked games.

Fetches all available ranked history for both accounts and stores it in
matches.db. Incremental — skips matches already in the database.

Usage:
  python dataset.py fetch              # fetch all games for both accounts
  python dataset.py fetch --account main
  python dataset.py fetch --account lab
  python dataset.py stats              # print stats from the database
  python dataset.py stats --account main
  python dataset.py export             # export to matches.csv
"""

import sqlite3
import time
import argparse
import csv
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

DB_PATH = Path("matches.db")


# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            match_id     TEXT NOT NULL,
            account      TEXT NOT NULL,
            puuid        TEXT NOT NULL,
            champion     TEXT,
            win          INTEGER,
            kills        INTEGER,
            deaths       INTEGER,
            assists      INTEGER,
            cs           INTEGER,
            damage       INTEGER,
            vision       INTEGER,
            kp           REAL,
            duration_min REAL,
            timestamp    INTEGER,
            patch        TEXT,
            queue        INTEGER,
            double_kills INTEGER DEFAULT 0,
            triple_kills INTEGER DEFAULT 0,
            quadra_kills INTEGER DEFAULT 0,
            penta_kills  INTEGER DEFAULT 0,
            PRIMARY KEY (match_id, puuid)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account ON matches(account)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_champion ON matches(champion)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON matches(timestamp)")
    # Migrate existing DB — add columns if missing
    existing = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    for col, default in [("double_kills", 0), ("triple_kills", 0), ("quadra_kills", 0), ("penta_kills", 0)]:
        if col not in existing:
            conn.execute(f"ALTER TABLE matches ADD COLUMN {col} INTEGER DEFAULT {default}")
    conn.commit()
    return conn


def already_stored(conn: sqlite3.Connection, match_id: str, puuid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM matches WHERE match_id=? AND puuid=?", (match_id, puuid)
    ).fetchone()
    return row is not None


def insert_match(conn: sqlite3.Connection, row: dict):
    conn.execute("""
        INSERT OR IGNORE INTO matches
        (match_id, account, puuid, champion, win, kills, deaths, assists,
         cs, damage, vision, kp, duration_min, timestamp, patch, queue,
         double_kills, triple_kills, quadra_kills, penta_kills)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        row["match_id"], row["account"], row["puuid"],
        row["champion"], row["win"],
        row["kills"], row["deaths"], row["assists"],
        row["cs"], row["damage"], row["vision"], row["kp"],
        row["duration_min"], row["timestamp"], row["patch"], row["queue"],
        row.get("double_kills", 0), row.get("triple_kills", 0),
        row.get("quadra_kills", 0), row.get("penta_kills", 0),
    ))


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

# Dec 24, 2015 in epoch — API will cap at its earliest available data (~2021)
EARLIEST_EPOCH = 1450915200


def fetch_account(account_label: str, conn: sqlite3.Connection):
    account_str = ACCOUNTS[account_label]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    console.print(f"\n[bold cyan]Fetching match IDs for {account_str}...[/bold cyan]")
    match_ids = get_match_ids(puuid, count=0, start_time=EARLIEST_EPOCH)
    console.print(f"[dim]Found {len(match_ids)} ranked games in API history.[/dim]")

    # Filter to ones not yet stored
    new_ids = [mid for mid in match_ids if not already_stored(conn, mid, puuid)]
    console.print(f"[dim]{len(match_ids) - len(new_ids)} already in DB, fetching {len(new_ids)} new.[/dim]")

    if not new_ids:
        console.print("[green]All up to date.[/green]")
        return

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
                match = get_match(match_id)
                p     = extract_participant(match, puuid)
                if not p:
                    progress.advance(task)
                    continue

                info  = match["info"]
                dur   = info["gameDuration"] / 60
                if dur < 5:
                    progress.advance(task)
                    continue

                ch       = p.get("challenges", {})
                total_cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
                team     = [x for x in info["participants"] if x["teamId"] == p["teamId"]]
                team_k   = sum(x["kills"] for x in team) or 1
                kp       = (p["kills"] + p["assists"]) / team_k

                patch = ".".join(info.get("gameVersion", "0.0").split(".")[:2])

                insert_match(conn, {
                    "match_id":    match_id,
                    "account":     account_label,
                    "puuid":       puuid,
                    "champion":    p["championName"],
                    "win":         int(p["win"]),
                    "kills":       p["kills"],
                    "deaths":      p["deaths"],
                    "assists":     p["assists"],
                    "cs":          total_cs,
                    "damage":      p["totalDamageDealtToChampions"],
                    "vision":      p["visionScore"],
                    "kp":          round(kp, 3),
                    "duration_min": round(dur, 1),
                    "timestamp":   info["gameCreation"] // 1000,
                    "patch":       patch,
                    "queue":       info.get("queueId", 420),
                    "double_kills": p.get("doubleKills", 0),
                    "triple_kills": p.get("tripleKills", 0),
                    "quadra_kills": p.get("quadraKills", 0),
                    "penta_kills":  p.get("pentaKills", 0),
                })
                conn.commit()
                time.sleep(0.05)

            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    progress.print("[yellow]Rate limited — waiting 70s[/yellow]")
                    time.sleep(70)
                    # Retry
                    try:
                        match = get_match(match_id)
                        p     = extract_participant(match, puuid)
                        if p:
                            info  = match["info"]
                            dur   = info["gameDuration"] / 60
                            ch       = p.get("challenges", {})
                            total_cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
                            team     = [x for x in info["participants"] if x["teamId"] == p["teamId"]]
                            team_k   = sum(x["kills"] for x in team) or 1
                            kp       = (p["kills"] + p["assists"]) / team_k
                            patch = ".".join(info.get("gameVersion", "0.0").split(".")[:2])
                            insert_match(conn, {
                                "match_id": match_id, "account": account_label, "puuid": puuid,
                                "champion": p["championName"], "win": int(p["win"]),
                                "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
                                "cs": total_cs, "damage": p["totalDamageDealtToChampions"],
                                "vision": p["visionScore"], "kp": round(kp, 3),
                                "duration_min": round(dur, 1),
                                "timestamp": info["gameCreation"] // 1000,
                                "patch": patch, "queue": info.get("queueId", 420),
                                "double_kills": p.get("doubleKills", 0),
                                "triple_kills": p.get("tripleKills", 0),
                                "quadra_kills": p.get("quadraKills", 0),
                                "penta_kills":  p.get("pentaKills", 0),
                            })
                            conn.commit()
                    except Exception:
                        pass
                # Skip other errors silently
            finally:
                progress.advance(task)


# ---------------------------------------------------------------------------
# Stats display
# ---------------------------------------------------------------------------

def print_stats(account_label: str | None, conn: sqlite3.Connection):
    where = f"WHERE account='{account_label}'" if account_label else ""

    total = conn.execute(f"SELECT COUNT(*) FROM matches {where}").fetchone()[0]
    if total == 0:
        console.print("[yellow]No data in DB yet — run fetch first.[/yellow]")
        return

    wins = conn.execute(f"SELECT SUM(win) FROM matches {where}").fetchone()[0] or 0
    wr   = wins / total * 100

    first = conn.execute(f"SELECT MIN(timestamp) FROM matches {where}").fetchone()[0]
    last  = conn.execute(f"SELECT MAX(timestamp) FROM matches {where}").fetchone()[0]
    first_dt = datetime.fromtimestamp(first, tz=timezone.utc).strftime("%b %Y") if first else "?"
    last_dt  = datetime.fromtimestamp(last,  tz=timezone.utc).strftime("%b %Y") if last  else "?"

    label = account_label or "all accounts"
    wr_c  = "green" if wr >= 55 else ("red" if wr < 45 else "yellow")
    console.print(f"\n[bold]{label}[/bold]  {total} games  [{wr_c}]{wins}W/{total-wins}L  {wr:.1f}% WR[/{wr_c}]  [{first_dt} → {last_dt}]")

    # Multikill totals
    mk = conn.execute(f"""
        SELECT SUM(double_kills) as db, SUM(triple_kills) as tr,
               SUM(quadra_kills) as qd, SUM(penta_kills) as pt
        FROM matches {where}
    """).fetchone()
    mk_parts = []
    if mk["db"]: mk_parts.append(f"[cyan]{mk['db']}[/cyan] doubles")
    if mk["tr"]: mk_parts.append(f"[yellow]{mk['tr']}[/yellow] triples")
    if mk["qd"]: mk_parts.append(f"[magenta]{mk['qd']}[/magenta] quadras")
    if mk["pt"]: mk_parts.append(f"[bold red]{mk['pt']}[/bold red] pentas")
    if mk_parts:
        console.print("  Multikills: " + "  /  ".join(mk_parts))

    # Per-champion breakdown
    rows = conn.execute(f"""
        SELECT champion,
               COUNT(*) as g,
               SUM(win) as w,
               AVG(kills) as k,
               AVG(deaths) as d,
               AVG(assists) as a,
               AVG(cs) as cs,
               AVG(damage) as dmg,
               AVG(kp) as kp,
               SUM(double_kills) as db,
               SUM(triple_kills) as tr,
               SUM(quadra_kills) as qd,
               SUM(penta_kills) as pt
        FROM matches {where}
        GROUP BY champion
        HAVING g >= 3
        ORDER BY g DESC
    """).fetchall()

    t = Table(box=box.ROUNDED, show_header=True)
    t.add_column("Champion",  style="cyan", min_width=14)
    t.add_column("Games",     justify="right")
    t.add_column("WR%",       justify="right")
    t.add_column("KDA",       justify="right")
    t.add_column("Avg CS",    justify="right")
    t.add_column("Avg Dmg",   justify="right")
    t.add_column("Avg KP",    justify="right")
    t.add_column("Multikills", justify="right")

    for r in rows:
        g    = r["g"]
        wr_c2 = "green" if r["w"]/g >= 0.55 else ("red" if r["w"]/g < 0.45 else "yellow")
        d    = r["d"] or 1
        kda  = f"{(r['k'] + r['a']) / d:.2f}"
        mk_str = ""
        if r["pt"]: mk_str += f"[bold red]{r['pt']}P[/bold red] "
        if r["qd"]: mk_str += f"[magenta]{r['qd']}Q[/magenta] "
        if r["tr"]: mk_str += f"[yellow]{r['tr']}T[/yellow] "
        if r["db"]: mk_str += f"[cyan]{r['db']}D[/cyan]"
        t.add_row(
            r["champion"], str(g),
            f"[{wr_c2}]{r['w']/g*100:.0f}%[/{wr_c2}]",
            kda, str(int(r["cs"])), f"{int(r['dmg']):,}", f"{r['kp']*100:.0f}%",
            mk_str.strip() or "—",
        )

    console.print(t)

    # Patch trend (last 5 patches)
    patch_filter = "AND patch != '0.0'" if where else "WHERE patch != '0.0'"
    patches = conn.execute(f"""
        SELECT patch, COUNT(*) as g, SUM(win) as w
        FROM matches {where}
        {patch_filter}
        GROUP BY patch ORDER BY patch DESC LIMIT 5
    """).fetchall()

    if patches:
        pt = Table(title="Recent patches", box=box.SIMPLE)
        pt.add_column("Patch"); pt.add_column("Games", justify="right"); pt.add_column("WR", justify="right")
        for p in patches:
            g = p["g"]
            wr_p = p["w"]/g*100
            c = "green" if wr_p >= 55 else ("red" if wr_p < 45 else "yellow")
            pt.add_row(p["patch"], str(g), f"[{c}]{wr_p:.0f}%[/{c}]")
        console.print(pt)


# ---------------------------------------------------------------------------
# Backfill multikills for existing rows (double_kills=0 may mean unset)
# ---------------------------------------------------------------------------

def backfill_multikills(account_label: str | None, conn: sqlite3.Connection):
    where = f"AND account='{account_label}'" if account_label else ""
    # Only rows where all multikill cols are 0 AND kills > 0 — likely missing data
    rows = conn.execute(f"""
        SELECT match_id, puuid FROM matches
        WHERE double_kills=0 AND triple_kills=0 AND quadra_kills=0 AND penta_kills=0
        AND kills >= 2 {where}
    """).fetchall()

    if not rows:
        console.print("[green]Nothing to backfill.[/green]")
        return

    console.print(f"[dim]Backfilling multikills for {len(rows)} games...[/dim]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Backfilling", total=len(rows))
        for match_id, puuid in rows:
            try:
                match = get_match(match_id)
                p     = extract_participant(match, puuid)
                if p:
                    conn.execute("""
                        UPDATE matches SET
                            double_kills=?, triple_kills=?, quadra_kills=?, penta_kills=?
                        WHERE match_id=? AND puuid=?
                    """, (
                        p.get("doubleKills", 0), p.get("tripleKills", 0),
                        p.get("quadraKills", 0), p.get("pentaKills", 0),
                        match_id, puuid,
                    ))
                    conn.commit()
                time.sleep(0.05)
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower():
                    progress.print("[yellow]Rate limited — waiting 70s[/yellow]")
                    time.sleep(70)
            finally:
                progress.advance(task)

    console.print("[green]Backfill complete.[/green]")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_csv(conn: sqlite3.Connection):
    rows = conn.execute("SELECT * FROM matches ORDER BY timestamp").fetchall()
    with open("matches.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([d[0] for d in conn.execute("SELECT * FROM matches LIMIT 0").description])
        writer.writerows(rows)
    console.print(f"[green]Exported {len(rows)} rows to matches.csv[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Local match dataset manager")
    parser.add_argument("command", choices=["fetch", "stats", "export", "backfill"])
    parser.add_argument("--account", choices=list(ACCOUNTS.keys()), default=None)
    args = parser.parse_args()

    conn = get_db()

    if args.command == "fetch":
        accounts = [args.account] if args.account else list(ACCOUNTS.keys())
        for label in accounts:
            fetch_account(label, conn)
        console.print("\n[bold green]Done. Exporting matches.csv...[/bold green]")
        export_csv(conn)
        print_stats(args.account, conn)

    elif args.command == "stats":
        print_stats(args.account, conn)

    elif args.command == "export":
        export_csv(conn)

    elif args.command == "backfill":
        backfill_multikills(args.account, conn)
        export_csv(conn)

    conn.close()


if __name__ == "__main__":
    main()
