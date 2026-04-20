"""
rl_advisor.py — Contextual bandit feedback loop for pregame recommendations.

After each game, logs what was recommended vs the outcome and updates weights.
Recommendations in pregame improve over time as you play more games.

Usage:
  python rl_advisor.py feedback main          # log last game outcome
  python rl_advisor.py feedback main --match BR1_123456
  python rl_advisor.py weights                # show learned weights
  python rl_advisor.py reset                  # wipe learned weights
"""

import json
import argparse
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from lol_stats import get_account, get_match_ids, extract_participant, ACCOUNTS
from match_cache import get_match_cached, is_excluded
from comp_check import analyze_comp

load_dotenv()
console = Console()

WEIGHTS_FILE = Path(".rl_weights.json")
LEARNING_RATE = 0.2   # how fast to update weights (0=never update, 1=replace)
MIN_GAMES = 3         # minimum observations before weights influence recommendations


# ---------------------------------------------------------------------------
# State bucketing — discrete context keys from comp analysis
# ---------------------------------------------------------------------------

def build_state(ally_comp: dict, enemy_comp: dict) -> frozenset[str]:
    """Reduce comp analysis to a small set of context flags."""
    state = set()

    enemy_ap = enemy_comp["magic_dmg"]
    enemy_ad = enemy_comp["phys_dmg"]
    enemy_cc = enemy_comp["hard_cc"] + enemy_comp["soft_cc"]

    if enemy_ap >= 3:                         state.add("ap_heavy")
    if enemy_ad >= 3:                         state.add("ad_heavy")
    if enemy_ap >= 2 and enemy_ad >= 2:       state.add("mixed_dmg")
    if enemy_cc >= 4:                         state.add("cc_heavy")
    if len(enemy_comp["late_scalers"]) >= 2:  state.add("late_game")
    if len(enemy_comp["assassins"]) >= 1:     state.add("has_assassin")
    if len(ally_comp["frontline"]) == 0:      state.add("no_frontline")

    ally_names = {n for n, _ in ally_comp["resolved"]}
    fighting_adcs = {"Samira", "Draven", "Jinx", "Tristana", "Kaisa"}
    peel_adcs     = {"Vayne", "Ezreal", "Aphelios", "Caitlyn", "Jhin"}
    if ally_names & fighting_adcs:            state.add("fighting_adc")
    if ally_names & peel_adcs:                state.add("peel_adc")

    return frozenset(state)


def state_key(state: frozenset[str]) -> str:
    return ",".join(sorted(state)) or "default"


# ---------------------------------------------------------------------------
# Reward function
# ---------------------------------------------------------------------------

def compute_reward(participant: dict, avg_deaths: float, avg_kp: float) -> float:
    """
    Reward in [-1.0, 1.0].
    Win contributes most. Deaths below baseline = bonus. KP above baseline = bonus.
    """
    reward = 1.0 if participant["win"] else -0.5

    deaths = participant.get("deaths", 6)
    deaths_delta = avg_deaths - deaths          # positive = fewer deaths than usual
    reward += min(0.3, deaths_delta * 0.05)     # capped at +0.3

    kp = participant.get("challenges", {}).get("killParticipation", avg_kp)
    kp_delta = kp - avg_kp                      # positive = more KP than usual
    reward += min(0.2, kp_delta * 0.4)          # capped at +0.2

    return max(-1.0, min(1.0, reward))


# ---------------------------------------------------------------------------
# Weight store
# ---------------------------------------------------------------------------

def _load_weights() -> dict:
    if WEIGHTS_FILE.exists():
        try:
            return json.loads(WEIGHTS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_weights(w: dict):
    WEIGHTS_FILE.write_text(json.dumps(w, indent=2))


def update_weight(champion: str, ctx_key: str, action: str, reward: float):
    """Exponential moving average update."""
    w = _load_weights()
    champ_w = w.setdefault(champion, {})
    ctx_w   = champ_w.setdefault(ctx_key, {})

    entry = ctx_w.get(action, {"score": 0.0, "n": 0})
    old_score = entry["score"]
    n         = entry["n"]

    new_score = (1 - LEARNING_RATE) * old_score + LEARNING_RATE * reward
    ctx_w[action] = {"score": round(new_score, 4), "n": n + 1}

    _save_weights(w)


def get_weight(champion: str, ctx_key: str, action: str) -> tuple[float, int]:
    """Return (score, n_observations) for a (champion, context, action) triple."""
    w = _load_weights()
    entry = w.get(champion, {}).get(ctx_key, {}).get(action, None)
    if entry is None:
        return 0.0, 0
    return entry["score"], entry["n"]


# ---------------------------------------------------------------------------
# Public API — used by pregame/runes for weighted recommendations
# ---------------------------------------------------------------------------

def rank_actions(champion: str, ctx_key: str, actions: list[str]) -> list[tuple[str, float, int]]:
    """
    Return actions sorted by learned score descending.
    Actions with < MIN_GAMES observations keep their original order (rule-based wins).
    Returns list of (action, score, n).
    """
    scored = []
    for action in actions:
        score, n = get_weight(champion, ctx_key, action)
        scored.append((action, score, n))

    has_data = any(n >= MIN_GAMES for _, _, n in scored)
    if not has_data:
        return scored  # keep original rule-based order

    return sorted(scored, key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Feedback command — run after each game
# ---------------------------------------------------------------------------

def cmd_feedback(args):
    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)
    acct  = get_account(game_name, tag)
    puuid = acct["puuid"]

    # Find the match to log
    if args.match:
        mid = args.match
    else:
        ids = get_match_ids(puuid, count=5)
        mid = next((m for m in ids if not is_excluded(m)), None)
        if not mid:
            console.print("[red]No recent non-excluded game found.[/red]")
            return

    match = get_match_cached(mid)
    p     = extract_participant(match, puuid)
    if not p:
        console.print("[red]Could not find your participant data.[/red]")
        return

    my_champ = p["championName"]
    duration = match["info"]["gameDuration"] / 60
    if duration < 10:
        console.print("[yellow]Game too short to log.[/yellow]")
        return

    # Build comp state from the match
    all_parts = match["info"]["participants"]
    my_team   = p["teamId"]
    ally_names  = [q["championName"] for q in all_parts if q["teamId"] == my_team   and q["championName"] != my_champ]
    enemy_names = [q["championName"] for q in all_parts if q["teamId"] != my_team]

    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp(enemy_names)
    state      = build_state(ally_comp, enemy_comp)
    ctx_key    = state_key(state)

    # Simple baselines from recent history (last 10 non-excluded games)
    baseline_ids = [m for m in get_match_ids(puuid, count=15) if not is_excluded(m) and m != mid][:10]
    baseline_deaths, baseline_kp = [], []
    for bid in baseline_ids:
        try:
            bm = get_match_cached(bid)
            bp = extract_participant(bm, puuid)
            if bp:
                baseline_deaths.append(bp.get("deaths", 6))
                ch = bp.get("challenges", {})
                baseline_kp.append(ch.get("killParticipation", 0.5))
        except Exception:
            pass
        time.sleep(0.02)

    avg_deaths = sum(baseline_deaths) / len(baseline_deaths) if baseline_deaths else 6.0
    avg_kp     = sum(baseline_kp) / len(baseline_kp) if baseline_kp else 0.5

    reward = compute_reward(p, avg_deaths, avg_kp)

    # Infer what rune page was likely used (from keystone perk ID in the match)
    perk_style = p.get("perks", {}).get("styles", [{}])[0]
    keystone_id = next(
        (sel["perk"] for sel in perk_style.get("selections", []) if sel.get("perk", 0) in {
            8439, 8465, 8214, 8437, 8112, 8128, 9923, 8005, 8008, 8021, 8010,
            8351, 8360, 8369, 8229, 8230
        }),
        0
    )
    KEYSTONE_NAMES = {
        8439: "Aftershock", 8465: "Guardian", 8214: "Summon Aery",
        8437: "Grasp of the Undying", 8112: "Electrocute", 8128: "Dark Harvest",
        9923: "Hail of Blades", 8005: "Press the Attack", 8008: "Lethal Tempo",
        8021: "Fleet Footwork", 8010: "Conqueror", 8351: "Glacial Augment",
        8360: "Unsealed Spellbook", 8369: "First Strike", 8229: "Arcane Comet",
        8230: "Phase Rush",
    }
    keystone_name = KEYSTONE_NAMES.get(keystone_id, "Unknown")

    # Infer mythic from items (first legendary support item found)
    MYTHICS = {"Locket of the Iron Solari", "Shurelya's Battlesong", "Turbo Chemtank",
               "Imperial Mandate", "Moonstone Renewer", "Echoes of Helia"}
    items = [p.get(f"item{i}") for i in range(7)]
    # We don't have item ID→name here without Data Dragon, so log keystone as primary action
    action = f"keystone:{keystone_name}"

    update_weight(my_champ, ctx_key, action, reward)

    # Display
    outcome_str = "[green]WIN[/green]" if p["win"] else "[red]LOSS[/red]"
    reward_str  = f"[green]+{reward:.2f}[/green]" if reward > 0 else f"[red]{reward:.2f}[/red]"
    ch = p.get("challenges", {})

    console.print()
    console.print(Panel(
        f"  Match  : {mid}\n"
        f"  Champ  : {my_champ}\n"
        f"  Result : {outcome_str}\n"
        f"  Kills  : {p['kills']}/{p['deaths']}/{p['assists']}\n"
        f"  KP     : {ch.get('killParticipation', 0)*100:.0f}%   Vision: {p['visionScore']}\n\n"
        f"  Context: {ctx_key}\n"
        f"  Action : {action}\n"
        f"  Reward : {reward_str}   (baseline deaths: {avg_deaths:.1f}, kp: {avg_kp*100:.0f}%)",
        title="[bold]RL Feedback Logged[/bold]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


# ---------------------------------------------------------------------------
# Show weights
# ---------------------------------------------------------------------------

def cmd_weights(args):
    w = _load_weights()
    if not w:
        console.print("[dim]No weights learned yet. Run feedback after some games.[/dim]")
        return

    for champ, ctx_map in w.items():
        console.print(f"\n[bold cyan]{champ}[/bold cyan]")
        for ctx_key, actions in ctx_map.items():
            t = Table(title=f"Context: {ctx_key}", box=box.SIMPLE, show_header=True)
            t.add_column("Action", style="cyan")
            t.add_column("Score",  justify="right")
            t.add_column("Games",  justify="right")

            rows = sorted(actions.items(), key=lambda x: x[1]["score"], reverse=True)
            for action, data in rows:
                score = data["score"]
                col   = "green" if score > 0.1 else ("red" if score < -0.1 else "yellow")
                t.add_row(action, f"[{col}]{score:+.3f}[/{col}]", str(data["n"]))

            console.print(t)


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def cmd_reset(args):
    if WEIGHTS_FILE.exists():
        WEIGHTS_FILE.unlink()
    console.print("[green]Weights reset.[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RL feedback loop for recommendations")
    sub = parser.add_subparsers(dest="cmd")

    p_fb = sub.add_parser("feedback", help="Log last game outcome and update weights")
    p_fb.add_argument("account", choices=list(ACCOUNTS.keys()))
    p_fb.add_argument("--match", default="", help="Specific match ID to log")

    sub.add_parser("weights", help="Show learned weights")
    sub.add_parser("reset",   help="Wipe all learned weights")

    args = parser.parse_args()
    if args.cmd == "feedback": cmd_feedback(args)
    elif args.cmd == "weights": cmd_weights(args)
    elif args.cmd == "reset":   cmd_reset(args)
    else:                       parser.print_help()


if __name__ == "__main__":
    main()
