"""
features.py — Turn Match v5 payloads into contextual-bandit samples.

One sample per participant:
  context — flags built from both teams' champion classes (Data Dragon)
  action  — the keystone rune the player took
  reward  — result, plus deaths and kill participation relative to the
            player's own teammates in the same game
"""

from dataclasses import dataclass

from riftlab.rl.static import Champion, StaticData

MIN_DURATION_S = 600  # skip remakes and early surrenders

CONTEXT_FLAGS = (
    "enemy_ap_heavy",     # 3+ enemies rated more magic than attack
    "enemy_ad_heavy",     # 3+ enemies rated more attack than magic
    "enemy_assassin",     # any enemy tagged Assassin
    "enemy_tanky",        # 2+ enemies whose primary class is Tank
    "ally_no_frontline",  # no teammate whose primary class is Tank or Fighter
)


@dataclass(frozen=True)
class Sample:
    match_id: str
    timestamp: int           # game creation, epoch seconds
    puuid: str
    champion: str            # Data Dragon ID
    context: frozenset[str]
    action: str              # keystone name
    reward: float
    win: bool


def team_context(allies: list[Champion], enemies: list[Champion]) -> frozenset[str]:
    """Context flags from the player's point of view. `allies` excludes the player."""
    flags = set()
    if sum(c.magic_leaning for c in enemies) >= 3:
        flags.add("enemy_ap_heavy")
    if sum(c.attack_leaning for c in enemies) >= 3:
        flags.add("enemy_ad_heavy")
    if any("Assassin" in c.tags for c in enemies):
        flags.add("enemy_assassin")
    if sum(c.primary == "Tank" for c in enemies) >= 2:
        flags.add("enemy_tanky")
    if not any(c.primary in ("Tank", "Fighter") for c in allies):
        flags.add("ally_no_frontline")
    return frozenset(flags)


def context_key(context: frozenset[str]) -> str:
    return ",".join(sorted(context)) or "none"


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_reward(player: dict, team: list[dict]) -> float:
    """
    Reward in [-1, 1]: +1 / -1 for the result, plus deaths and kill
    participation relative to the player's teammates in the same game (each
    term capped at ±0.25), divided by 1.5. `team` is all five participants
    of the player's team, including the player.
    """
    team_kills = sum(t["kills"] for t in team) or 1

    def kp(t: dict) -> float:
        return (t["kills"] + t["assists"]) / team_kills

    others = [t for t in team if t is not player] or [player]
    mean_deaths = sum(t["deaths"] for t in others) / len(others)
    mean_kp = sum(kp(t) for t in others) / len(others)

    reward = 1.0 if player["win"] else -1.0
    reward += _clip(0.1 * (mean_deaths - player["deaths"]), -0.25, 0.25)
    reward += _clip(0.5 * (kp(player) - mean_kp), -0.25, 0.25)
    return reward / 1.5


def keystone_id(participant: dict) -> int | None:
    styles = participant.get("perks", {}).get("styles", [])
    primary = next((s for s in styles if s.get("description") == "primaryStyle"),
                   styles[0] if styles else None)
    if not primary or not primary.get("selections"):
        return None
    return primary["selections"][0].get("perk")


def samples_from_match(match_id: str, match: dict, static: StaticData) -> list[Sample]:
    info = match["info"]
    if info.get("gameDuration", 0) < MIN_DURATION_S:
        return []

    parts = info["participants"]
    champs = [static.champions.get(p["championId"]) for p in parts]
    if any(c is None for c in champs):
        return []  # champion newer than the static data; skip the whole match

    samples = []
    for i, p in enumerate(parts):
        ks = keystone_id(p)
        if ks is None:
            continue
        team = [q for q in parts if q["teamId"] == p["teamId"]]
        allies = [champs[j] for j, q in enumerate(parts) if q["teamId"] == p["teamId"] and j != i]
        enemies = [champs[j] for j, q in enumerate(parts) if q["teamId"] != p["teamId"]]
        samples.append(Sample(
            match_id=match_id,
            timestamp=info["gameCreation"] // 1000,
            puuid=p["puuid"],
            champion=champs[i].name,
            context=team_context(allies, enemies),
            action=static.keystones.get(ks, f"Keystone {ks}"),
            reward=compute_reward(p, team),
            win=bool(p["win"]),
        ))
    return samples
