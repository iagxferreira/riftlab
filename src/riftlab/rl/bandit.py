"""
bandit.py — Contextual bandit for keystone rune choice, learned offline from logged games.

Every participant of every cached ranked match is one logged decision
(see features.py): context = both teams' champion classes, action = the
keystone taken, reward = result + deaths/KP relative to teammates. The model
is rebuilt from the local match cache on every run, so the same cache always
gives the same results.

Estimates are kept per (champion, context, keystone), fall back to
(champion, keystone) when a context has fewer than MIN_CONTEXT_N games, and
are shrunk toward 0 for small samples. The logged actions were chosen by
players, not by this policy, so all estimates are observational.

Usage:
  python -m riftlab.rl.bandit fetch main --games 100   # pull ranked games into the cache
  python -m riftlab.rl.bandit summary                  # dataset / model size
  python -m riftlab.rl.bandit show Syndra              # keystone estimates for a champion
  python -m riftlab.rl.bandit recommend Syndra --ally "Jinx,Rakan,Sejuani,Garen" \\
      --enemy "Zed,Lux,Nautilus,Caitlyn,Darius"
  python -m riftlab.rl.bandit evaluate                 # chronological replay evaluation
"""

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby

from rich import box
from rich.table import Table

from riftlab.cache import all_cached, get_excluded, get_match_cached
from riftlab.paths import MATCHES_CSV
from riftlab.riot import ACCOUNTS, console, get_account, get_match_ids
from riftlab.rl.features import CONTEXT_FLAGS, Sample, context_key, samples_from_match, team_context
from riftlab.rl.static import Champion, StaticData, load_static

PRIOR_N = 2.0        # pseudo-games at reward 0 that every estimate starts with
MIN_CONTEXT_N = 10   # games a (champion, context) needs before it's used instead of the champion level
MIN_ARM_N = 5        # games a keystone needs before it can be the greedy recommendation
ANY = "*"            # context key for the champion-level fallback


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Arm:
    n: int = 0
    wins: int = 0
    total: float = 0.0

    def add(self, reward: float, win: bool):
        self.n += 1
        self.wins += int(win)
        self.total += reward

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0

    @property
    def shrunk(self) -> float:
        """Mean shrunk toward 0 by PRIOR_N pseudo-games, so one lucky game can't dominate."""
        return self.total / (self.n + PRIOR_N)


@dataclass(frozen=True)
class Estimate:
    action: str
    value: float   # shrunk mean, plus the UCB bonus when exploring
    mean: float
    n: int
    wins: int
    level: str     # context key the estimate came from, or ANY


class Bandit:
    def __init__(self):
        self.table: dict[tuple[str, str], dict[str, Arm]] = defaultdict(lambda: defaultdict(Arm))

    def update(self, s: Sample):
        for ctx in (context_key(s.context), ANY):
            self.table[(s.champion, ctx)][s.action].add(s.reward, s.win)

    def arms(self, champion: str, context: frozenset[str]) -> tuple[str, dict[str, Arm]]:
        """Arms for this context if it has enough games, else the champion-level arms."""
        key = context_key(context)
        arms = self.table.get((champion, key), {})
        if sum(a.n for a in arms.values()) >= MIN_CONTEXT_N:
            return key, arms
        return ANY, self.table.get((champion, ANY), {})

    def rank(self, champion: str, context: frozenset[str], explore: bool = False) -> list[Estimate]:
        level, arms = self.arms(champion, context)
        total = sum(a.n for a in arms.values())
        ranked = []
        for action, arm in arms.items():
            value = arm.shrunk
            if explore:  # UCB1 bonus: favors keystones that have been tried less
                value += math.sqrt(2 * math.log(max(total, 2)) / arm.n)
            ranked.append(Estimate(action, value, arm.mean, arm.n, arm.wins, level))
        return sorted(ranked, key=lambda e: e.value, reverse=True)

    def greedy(self, champion: str, context: frozenset[str]) -> str | None:
        """Best-estimated keystone among those with at least MIN_ARM_N games, else None."""
        return next((e.action for e in self.rank(champion, context) if e.n >= MIN_ARM_N), None)


def fit(samples: list[Sample]) -> Bandit:
    model = Bandit()
    for s in samples:
        model.update(s)
    return model


def replay(samples: list[Sample]) -> dict:
    """
    Chronological replay: before each match, ask the model (trained only on
    earlier matches) for its greedy keystone for every participant, then learn
    from the match. Samples must be sorted by time.
    """
    model = Bandit()
    agree, disagree, uncovered = [], [], 0
    for _, group in groupby(samples, key=lambda s: s.match_id):
        group = list(group)
        for s in group:
            rec = model.greedy(s.champion, s.context)
            if rec is None:
                uncovered += 1
            elif rec == s.action:
                agree.append(s)
            else:
                disagree.append(s)
        for s in group:
            model.update(s)
    return {"n": len(samples), "uncovered": uncovered, "agree": agree, "disagree": disagree}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def my_puuids() -> set[str]:
    """PUUIDs of the configured accounts (from matches.csv if present, else the API)."""
    if MATCHES_CSV.exists():
        with open(MATCHES_CSV, newline="") as f:
            ids = {r["puuid"] for r in csv.DictReader(f) if r.get("puuid")}
        if ids:
            return ids
    return {get_account(*rid.rsplit("#", 1))["puuid"] for rid in ACCOUNTS.values() if rid}


def load_samples(static: StaticData, scope: str = "all") -> list[Sample]:
    excluded = get_excluded()
    samples = []
    for mid, match in all_cached().items():
        if mid not in excluded:
            samples.extend(samples_from_match(mid, match, static))
    if scope == "mine":
        mine = my_puuids()
        samples = [s for s in samples if s.puuid in mine]
    return sorted(samples, key=lambda s: (s.timestamp, s.match_id))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _champion(static: StaticData, name: str) -> Champion:
    c = static.champion_by_name(name)
    if c is None:
        raise SystemExit(f"Unknown champion: {name!r}")
    return c


def _names(raw: str | None) -> list[str]:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def _mean_var(xs: list[float]) -> tuple[float, float]:
    """Mean and variance of the mean (0 variance for n < 2)."""
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0
    return m, var / len(xs)


def _estimates_table(title: str, estimates: list[Estimate], value_label: str) -> Table:
    t = Table(title=title, box=box.SIMPLE)
    t.add_column("Keystone", style="cyan")
    t.add_column(value_label, justify="right")
    t.add_column("Mean reward", justify="right")
    t.add_column("Games", justify="right")
    t.add_column("WR", justify="right")
    for e in estimates:
        low = " [dim](low data)[/dim]" if e.n < MIN_ARM_N else ""
        t.add_row(e.action, f"{e.value:+.3f}", f"{e.mean:+.3f}", f"{e.n}{low}", f"{e.wins / e.n * 100:.0f}%")
    return t


def cmd_fetch(args):
    game_name, tag = ACCOUNTS[args.account].rsplit("#", 1)
    puuid = get_account(game_name, tag)["puuid"]
    cached = set(all_cached())
    new = [m for m in get_match_ids(puuid, count=args.games) if m not in cached]
    console.print(f"[dim]{len(new)} of the last {args.games} ranked games are not cached yet.[/dim]")
    for i, mid in enumerate(new, 1):
        get_match_cached(mid)
        console.print(f"[dim]  {i}/{len(new)} {mid}[/dim]")
    console.print(f"[green]Cache now holds {len(cached) + len(new)} matches.[/green]")


def cmd_summary(args):
    static = load_static()
    samples = load_samples(static, args.scope)
    model = fit(samples)
    champ_arms = [a for (_, ctx), arms in model.table.items() if ctx == ANY for a in arms.values()]
    t = Table(title=f"Bandit dataset ({args.scope} players)", box=box.SIMPLE, show_header=False)
    t.add_column(style="bold"); t.add_column(justify="right")
    t.add_row("Samples (player-games)", str(len(samples)))
    t.add_row("Matches", str(len({s.match_id for s in samples})))
    t.add_row("Players", str(len({s.puuid for s in samples})))
    t.add_row("Champions", str(len({s.champion for s in samples})))
    t.add_row("Contexts seen", f"{len({context_key(s.context) for s in samples})} of {2 ** len(CONTEXT_FLAGS)}")
    t.add_row("(champion, keystone) arms", str(len(champ_arms)))
    t.add_row(f"  with ≥ {MIN_ARM_N} games", str(sum(a.n >= MIN_ARM_N for a in champ_arms)))
    t.add_row("Data Dragon version", static.version)
    console.print(t)


def cmd_show(args):
    static = load_static()
    champ = _champion(static, args.champion)
    model = fit(load_samples(static, args.scope))
    ranked = model.rank(champ.name, frozenset({"__no_such_context__"}))  # forces champion level
    if not ranked:
        console.print(f"[yellow]No cached games for {champ.display}.[/yellow]")
        return
    console.print(_estimates_table(f"{champ.display} — all contexts", ranked, "Shrunk mean"))
    for (name, ctx), arms in sorted(model.table.items()):
        if name == champ.name and ctx != ANY and sum(a.n for a in arms.values()) >= MIN_CONTEXT_N:
            est = model.rank(champ.name, frozenset(ctx.split(",")) if ctx != "none" else frozenset())
            console.print(_estimates_table(f"context: {ctx}", est, "Shrunk mean"))


def cmd_recommend(args):
    static = load_static()
    champ = _champion(static, args.champion)
    allies = [_champion(static, n) for n in _names(args.ally)]
    allies = [c for c in allies if c.key != champ.key]
    enemies = [_champion(static, n) for n in _names(args.enemy)]
    context = team_context(allies, enemies)

    model = fit(load_samples(static, args.scope))
    ranked = model.rank(champ.name, context, explore=args.explore)
    console.print(f"[bold]{champ.display}[/bold]  context: [cyan]{context_key(context)}[/cyan]")
    if not ranked:
        console.print(f"[yellow]No cached games for {champ.display} — nothing to recommend yet.[/yellow]")
        return
    level = ranked[0].level
    if level == ANY:
        console.print(f"[dim]Fewer than {MIN_CONTEXT_N} games in this context — using all of {champ.display}'s games.[/dim]")
    label = "UCB score" if args.explore else "Shrunk mean"
    console.print(_estimates_table("Keystone ranking", ranked, label))
    console.print("[dim]Observational estimates from other players' logged choices, not a causal effect.[/dim]")


def cmd_evaluate(args):
    static = load_static()
    samples = load_samples(static, args.scope)
    res = replay(samples)
    agree, disagree = res["agree"], res["disagree"]
    covered = len(agree) + len(disagree)

    t = Table(title="Chronological replay", box=box.SIMPLE, show_header=False)
    t.add_column(style="bold"); t.add_column(justify="right")
    t.add_row("Samples", str(res["n"]))
    t.add_row("Covered (a recommendation existed)", f"{covered} ({covered / max(res['n'], 1) * 100:.0f}%)")
    if agree and disagree:
        (ma, va), (md, vd) = _mean_var([s.reward for s in agree]), _mean_var([s.reward for s in disagree])
        diff, se = ma - md, math.sqrt(va + vd)
        wr = lambda xs: sum(s.win for s in xs) / len(xs) * 100
        t.add_row("Player took the recommended keystone", f"{len(agree)} ({len(agree) / covered * 100:.0f}%)")
        t.add_row("Mean reward — took it", f"{ma:+.3f}  (WR {wr(agree):.0f}%)")
        t.add_row("Mean reward — didn't", f"{md:+.3f}  (WR {wr(disagree):.0f}%)")
        t.add_row("Difference (95% CI)", f"{diff:+.3f}  [{diff - 1.96 * se:+.3f}, {diff + 1.96 * se:+.3f}]")
    console.print(t)
    console.print(
        "[dim]Observational check, not a causal estimate: players who pick the recommended keystone may differ in "
        "other ways, and the 10 samples from one match are correlated, so the interval is optimistic.[/dim]"
    )


def main():
    parser = argparse.ArgumentParser(description="Contextual bandit for keystone choice")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="Pull an account's recent ranked games into the match cache")
    p.add_argument("account", choices=list(ACCOUNTS.keys()))
    p.add_argument("--games", type=int, default=20)

    for name, help_text in (("summary", "Dataset and model size"),
                            ("evaluate", "Chronological replay evaluation")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--scope", choices=["all", "mine"], default="all")

    p = sub.add_parser("show", help="Keystone estimates for a champion")
    p.add_argument("champion")
    p.add_argument("--scope", choices=["all", "mine"], default="all")

    p = sub.add_parser("recommend", help="Rank keystones for a champion in a given matchup")
    p.add_argument("champion")
    p.add_argument("--ally", help='Your 4 teammates, comma-separated')
    p.add_argument("--enemy", help='The 5 enemies, comma-separated')
    p.add_argument("--explore", action="store_true", help="Rank by UCB instead of the shrunk mean")
    p.add_argument("--scope", choices=["all", "mine"], default="all")

    args = parser.parse_args()
    {"fetch": cmd_fetch, "summary": cmd_summary, "show": cmd_show,
     "recommend": cmd_recommend, "evaluate": cmd_evaluate}[args.cmd](args)


if __name__ == "__main__":
    main()
