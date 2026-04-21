"""
champ_select.py — Champion pick advisor for your champion pool.

Compares your mains against each other and recommends who to play
based on ally + enemy comp.

Usage:
  python champ_select.py main          # live game
  python champ_select.py main --last   # last game (what should you have played?)
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

# ---------------------------------------------------------------------------
# Champion pool — add your mains here
# ---------------------------------------------------------------------------

POOL = {
    "Rakan": dict(
        role="Engage support",
        strengths=[
            "Team lacks engage or CC",
            "Fighting ADC in lane (Samira, Draven, Jinx, Tristana)",
            "Your team wants to start fights",
            "You need to create picks with R + ally follow-up",
        ],
        weaknesses=[
            "Enemy has Morgana (Black Shield counters W)",
            "Enemy has Nautilus or Leona (out-engage you at level 2)",
            "ADC is Vayne/Ezreal (peel-dependent, not fighting)",
            "Enemy has heavy poke that punishes your short range",
        ],
        # Conditions that FAVOR Rakan
        favor=lambda ally, enemy, ctx: sum([
            ctx.get("fighting_adc", False),
            not ctx.get("peel_adc", False),
            ally["primary_win_con"] in ("engage", "teamfight"),
            len(ally["frontline"]) <= 1,         # team needs you to be the engage
            enemy["primary_win_con"] not in ("poke",),
            "Morgana" not in [n for n, _ in enemy["resolved"]],
        ]),
        # Conditions that DISFAVOR Rakan
        against=lambda ally, enemy, ctx: sum([
            "Morgana"   in [n for n, _ in enemy["resolved"]],
            "Nautilus"  in [n for n, _ in enemy["resolved"]],
            "Leona"     in [n for n, _ in enemy["resolved"]],
            ctx.get("peel_adc", False) and not ctx.get("fighting_adc", False),
            enemy["primary_win_con"] == "poke",
        ]),
    ),

    "Milio": dict(
        role="Enchanter/peel support",
        strengths=[
            "ADC needs protection (Vayne, Ezreal, Aphelios, Jhin)",
            "Enemy has assassins that dive your carry",
            "Your team already has engage (Malphite, JarvanIV, etc.)",
            "Enemy has heavy CC — your R cleanses it for your whole team",
            "Morgana is on the enemy team (counters Rakan, not Milio)",
            "Team has AP carry that benefits from Staff of Flowing Water",
        ],
        weaknesses=[
            "Your team needs initiation and has no other engage",
            "Fighting ADC in lane (Milio doesn't enable their aggression as well)",
            "Enemy has no CC to cleanse — R loses value",
        ],
        favor=lambda ally, enemy, ctx: sum([
            ctx.get("peel_adc", False),
            ctx.get("has_assassin", False),
            ctx.get("ally_ap_carry", False),
            ctx.get("heavy_cc_enemy", False),         # R cleanse is impactful
            "Morgana" in [n for n, _ in enemy["resolved"]],
            ally["primary_win_con"] in ("scale", "poke"),
            len(ally["frontline"]) >= 2,              # team already has engage
        ]),
        against=lambda ally, enemy, ctx: sum([
            ctx.get("fighting_adc", False) and not ctx.get("peel_adc", False),
            len(ally["frontline"]) == 0 and ally["primary_win_con"] not in ("engage",),
            enemy["primary_win_con"] == "engage" and not ctx.get("heavy_cc_enemy", False),
        ]),
    ),
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_champion(champ: str, ally_comp: dict, enemy_comp: dict, ctx: dict) -> dict:
    entry   = POOL[champ]
    favor   = entry["favor"](ally_comp, enemy_comp, ctx)
    against = entry["against"](ally_comp, enemy_comp, ctx)
    score   = favor - against * 1.5   # disfavor weighted heavier
    return {"champ": champ, "score": score, "favor": favor, "against": against}


def pick_champion(ally_comp: dict, enemy_comp: dict, ctx: dict) -> tuple[str, list[dict]]:
    """Return (recommended_champ, all_scores_sorted)."""
    scores = [score_champion(c, ally_comp, enemy_comp, ctx) for c in POOL]
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[0]["champ"], scores


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_champion_pick(ally_comp: dict, enemy_comp: dict, ctx: dict):
    recommended, scores = pick_champion(ally_comp, enemy_comp, ctx)

    lines = []
    for s in scores:
        champ   = s["champ"]
        entry   = POOL[champ]
        marker  = "[bold green]► PLAY THIS[/bold green]" if champ == recommended else "[dim]  Alternative[/dim]"
        score_c = "green" if s["score"] > 0 else "red"
        lines.append(f"{marker}  [cyan]{champ}[/cyan] ({entry['role']})  score: [{score_c}]{s['score']:+.0f}[/{score_c}]")

    # Reasons for the top pick
    entry = POOL[recommended]
    reasons = []

    # Dynamic reasons based on context
    if recommended == "Rakan":
        if ctx.get("fighting_adc"):   reasons.append("Fighting ADC in lane — Rakan enables level 2 all-ins")
        if not ctx.get("peel_adc"):   reasons.append("ADC doesn't need peel — Rakan's aggression fits better")
        if ally_comp["primary_win_con"] in ("engage", "teamfight"):
            reasons.append(f"Your team win condition is {ally_comp['primary_win_con'].upper()} — Rakan is the engine")
        if len(ally_comp["frontline"]) <= 1:
            reasons.append("Team lacks frontline — Rakan's engage creates the setup your team needs")
        if "Morgana" not in [n for n, _ in enemy_comp["resolved"]]:
            reasons.append("No Morgana — your W lands freely")
    elif recommended == "Milio":
        if ctx.get("peel_adc"):       reasons.append("Peel ADC in lane — Milio's shields and heals protect them better")
        if ctx.get("has_assassin"):   reasons.append("Enemy assassin — Milio R cleanses the lockdown, Rakan W isn't fast enough")
        if ctx.get("heavy_cc_enemy"): reasons.append("Heavy enemy CC — your R cleanse can save the whole team from their engage")
        if ctx.get("ally_ap_carry"):  reasons.append("AP carry on your team — Staff of Flowing Water gives them AP and haste")
        if "Morgana" in [n for n, _ in enemy_comp["resolved"]]:
            reasons.append("Morgana on enemy — Black Shield counters Rakan W, play Milio instead")

    if not reasons:
        reasons.append(f"Better overall fit for this specific comp matchup (score: {scores[0]['score']:+.0f})")

    body = "\n".join(lines)
    body += "\n\n" + "\n".join(f"  [yellow]→[/yellow]  {r}" for r in reasons)

    # Counters/warnings for recommended pick
    warnings = [w for w in entry["weaknesses"]
                if any(kw in w.lower() for kw in
                       [n.lower() for n, _ in enemy_comp["resolved"]] +
                       (["morgana"] if "Morgana" in [n for n, _ in enemy_comp["resolved"]] else []))]
    if warnings:
        body += "\n\n  [red]Watch out:[/red] " + " / ".join(warnings[:2])

    console.print(Panel(body, title="[bold]Champion Pick[/bold]", border_style="magenta", padding=(1, 2)))


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, time
    from dotenv import load_dotenv
    from lol_stats import get_account, get_match_ids, get_match, extract_participant, ACCOUNTS
    from comp_check import analyze_comp, champ_name_from_id, _load_champ_id_map
    from build_advisor import fetch_live_data
    from lol_stats import BASE_SUMMONER, _get
    from runes import build_rune_context
    from match_cache import get_match_cached, is_excluded

    load_dotenv()

    parser = argparse.ArgumentParser(description="Champion pick advisor")
    parser.add_argument("account", choices=list(ACCOUNTS.keys()))
    parser.add_argument("--last", action="store_true", help="Use last game instead of live")
    args = parser.parse_args()

    account_str = ACCOUNTS[args.account]
    game_name, tag = account_str.rsplit("#", 1)
    _load_champ_id_map()

    if args.last:
        acct  = get_account(game_name, tag)
        puuid = acct["puuid"]
        ids   = get_match_ids(puuid, count=10)
        mid   = next((m for m in ids if not is_excluded(m)), None)
        if not mid:
            console.print("[red]No recent game found.[/red]")
            exit(1)
        match   = get_match_cached(mid)
        p       = extract_participant(match, puuid)
        my_team = p["teamId"]
        all_p   = match["info"]["participants"]
        my_champ    = p["championName"]
        ally_names  = [q["championName"] for q in all_p if q["teamId"] == my_team and q["championName"] != my_champ]
        enemy_names = [q["championName"] for q in all_p if q["teamId"] != my_team]
    else:
        my_champ, enemy_champs, _ = fetch_live_data(args.account)
        try:
            data = _get(f"{BASE_SUMMONER}/lol/spectator/v5/active-games/by-summoner/{get_account(game_name, tag)['puuid']}")
            my_team_id = next(p["teamId"] for p in data["participants"]
                              if champ_name_from_id(p["championId"]) == my_champ)
            ally_names  = [champ_name_from_id(p["championId"]) for p in data["participants"]
                           if p["teamId"] == my_team_id and champ_name_from_id(p["championId"]) != my_champ]
            enemy_names = [n for n, _ in enemy_champs]
        except Exception:
            ally_names, enemy_names = [], [n for n, _ in enemy_champs]

    ally_comp  = analyze_comp([my_champ] + ally_names)
    enemy_comp = analyze_comp(enemy_names)
    ctx        = build_rune_context(ally_comp, enemy_comp)

    console.print()
    print_champion_pick(ally_comp, enemy_comp, ctx)
