"""
paths.py — Project-root-relative locations for local config, data and caches.

Everything here is local-only and gitignored (except .rl_weights.json), so the
tools read and write the same files regardless of the working directory.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACCOUNTS_FILE = ROOT / "accounts.json"
DATA_DIR      = ROOT / "data"              # hand-authored champion knowledge
MATCHES_CSV   = ROOT / "matches.csv"
MATCH_CACHE   = ROOT / ".match_cache.json"
RL_WEIGHTS    = ROOT / ".rl_weights.json"
DDRAGON_DIR   = ROOT / "ddragon"
