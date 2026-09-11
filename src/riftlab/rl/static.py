"""
static.py — Public static game data from Data Dragon, fetched at runtime.

Champion classes (tags) and attack/magic ratings give a reproducible
team-composition representation, and keystone IDs map to names. Nothing is
written to disk.
"""

from dataclasses import dataclass
from functools import lru_cache

import requests

DDRAGON = "https://ddragon.leagueoflegends.com"


@dataclass(frozen=True)
class Champion:
    key: int                 # numeric ID used by Match v5 (championId)
    name: str                # Data Dragon ID, e.g. "MonkeyKing"
    display: str             # display name, e.g. "Wukong"
    tags: tuple[str, ...]    # Riot classes, primary first: Assassin, Fighter, Mage, Marksman, Support, Tank
    attack: int              # Data Dragon 0-10 ratings
    magic: int

    @property
    def primary(self) -> str:
        return self.tags[0] if self.tags else ""

    @property
    def magic_leaning(self) -> bool:
        return self.magic > self.attack

    @property
    def attack_leaning(self) -> bool:
        return self.attack > self.magic


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


@dataclass(frozen=True)
class StaticData:
    version: str
    champions: dict[int, Champion]
    keystones: dict[int, str]

    def champion_by_name(self, name: str) -> Champion | None:
        """Case/punctuation-insensitive lookup by ID or display name ("Kha'Zix", "khazix")."""
        target = _norm(name)
        return next((c for c in self.champions.values()
                     if target in (_norm(c.name), _norm(c.display))), None)


@lru_cache(maxsize=1)
def load_static(version: str | None = None) -> StaticData:
    version = version or requests.get(f"{DDRAGON}/api/versions.json", timeout=10).json()[0]
    base = f"{DDRAGON}/cdn/{version}/data/en_US"
    champs = requests.get(f"{base}/champion.json", timeout=10).json()["data"]
    runes = requests.get(f"{base}/runesReforged.json", timeout=10).json()

    champions = {
        int(c["key"]): Champion(int(c["key"]), c["id"], c["name"], tuple(c["tags"]),
                                c["info"]["attack"], c["info"]["magic"])
        for c in champs.values()
    }
    keystones = {r["id"]: r["name"] for tree in runes for r in tree["slots"][0]["runes"]}
    return StaticData(version, champions, keystones)
