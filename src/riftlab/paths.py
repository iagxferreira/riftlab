"""
paths.py — Project-root-relative locations for local config, data and caches.

Everything here is local-only and gitignored, so the tools read and write the
same files regardless of the working directory.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACCOUNTS_FILE = ROOT / "accounts.json"
MATCHES_CSV   = ROOT / "matches.csv"
MATCH_CACHE   = ROOT / ".match_cache.json"
