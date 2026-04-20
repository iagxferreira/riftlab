"""
match_cache.py — Local cache for match data + exclusion flags.

Stores match JSON locally so repeated runs don't re-hit the API.
Excluded matches are skipped in recent form, playstyle, and last_match.

Usage:
  python match_cache.py exclude main              # exclude last game
  python match_cache.py exclude main --note "kat inting"
  python match_cache.py list                      # show excluded games
  python match_cache.py clear-excluded            # remove all exclusions
"""

import json
import sys
import argparse
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box

from lol_stats import get_account, get_match_ids, get_match, ACCOUNTS

load_dotenv()
console = Console()

CACHE_FILE = Path(".match_cache.json")


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _load() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {"matches": {}, "excluded": {}}


def _save(data: dict):
    CACHE_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Public API — used by other scripts
# ---------------------------------------------------------------------------

def get_match_cached(match_id: str) -> dict:
    """Return match data from cache, fetching from API if not present."""
    data = _load()
    if match_id not in data["matches"]:
        from lol_stats import get_match
        data["matches"][match_id] = get_match(match_id)
        _save(data)
        time.sleep(0.05)
    return data["matches"][match_id]


def is_excluded(match_id: str) -> bool:
    return match_id in _load()["excluded"]


def get_excluded() -> dict[str, str]:
    """Return {match_id: note} for all excluded matches."""
    return _load()["excluded"]


def mark_excluded(match_id: str, note: str = ""):
    data = _load()
    data["excluded"][match_id] = note
    _save(data)


def unmark_excluded(match_id: str):
    data = _load()
    data["excluded"].pop(match_id, None)
    _save(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_exclude(args):
    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)
    acct = get_account(game_name, tag)
    ids = get_match_ids(acct["puuid"], count=1)
    if not ids:
        console.print("[red]No recent matches found.[/red]")
        return
    match_id = ids[0]
    note = args.note or ""
    mark_excluded(match_id, note)
    console.print(f"[green]Marked {match_id} as excluded.[/green]" +
                  (f" Note: {note}" if note else ""))


def cmd_list(args):
    excluded = get_excluded()
    if not excluded:
        console.print("[dim]No excluded matches.[/dim]")
        return
    t = Table(title="Excluded Matches", box=box.SIMPLE)
    t.add_column("Match ID", style="cyan")
    t.add_column("Note")
    for mid, note in excluded.items():
        t.add_row(mid, note or "[dim]—[/dim]")
    console.print(t)


def cmd_clear(args):
    data = _load()
    n = len(data["excluded"])
    data["excluded"] = {}
    _save(data)
    console.print(f"[green]Cleared {n} exclusion(s).[/green]")


def main():
    parser = argparse.ArgumentParser(description="Match cache + exclusion manager")
    sub = parser.add_subparsers(dest="cmd")

    p_ex = sub.add_parser("exclude", help="Exclude last game for an account")
    p_ex.add_argument("account", choices=list(ACCOUNTS.keys()))
    p_ex.add_argument("--note", default="", help="Reason for exclusion")

    sub.add_parser("list", help="List excluded matches")
    sub.add_parser("clear-excluded", help="Remove all exclusions")

    args = parser.parse_args()
    if args.cmd == "exclude":      cmd_exclude(args)
    elif args.cmd == "list":       cmd_list(args)
    elif args.cmd == "clear-excluded": cmd_clear(args)
    else:                          parser.print_help()


if __name__ == "__main__":
    main()
