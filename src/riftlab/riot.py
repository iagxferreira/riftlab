"""
riot.py — Shared Riot API client and account configuration.

Accounts are loaded from the local, gitignored accounts.json
(template: accounts.example.json), falling back to ACCOUNT_* in .env.
"""

import os
import json
import time

import requests
from dotenv import load_dotenv
from rich.console import Console

from riftlab.paths import ACCOUNTS_FILE

load_dotenv()
console = Console()

API_KEY = os.getenv("RIOT_API_KEY", "")

# ---------------------------------------------------------------------------
# Account loading — edit accounts.json to add/rename/change region
# ---------------------------------------------------------------------------

def _load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        with open(ACCOUNTS_FILE) as f:
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
