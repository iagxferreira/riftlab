# RiftLab

Experimental data science / RL playground built on League of Legends data from two BR accounts. Pulls data from the Riot API, builds a local match dataset, runs descriptive + rule-based analysis, and has a contextual-bandit prototype (`src/riftlab/rl/bandit.py`). See README.md for the full audit of what is implemented vs planned.

## Accounts

Configured in the local, gitignored `accounts.json` (template: `accounts.example.json`; falls back to `ACCOUNT_*` in `.env`). Labels: `main` (main account) and `lab` (experimentation account). Never commit real Riot IDs.

Focus champions: **Cassiopeia**, **Syndra**

## Setup

```bash
make setup   # uv sync + .env
# add RIOT_API_KEY to .env
```

Riot dev keys expire every 24h — regenerate at https://developer.riotgames.com if you get 401s.

## Running

Code lives in the `src/riftlab/` package (layout in README "Project layout"). Run modules with `uv run python -m riftlab.<module>`; the Makefile wraps the common ones:

```bash
make stats                                          # riftlab.analysis.stats
uv run python -m riftlab.analysis.playstyle main --games 40
make dataset-fetch                                  # riftlab.ingest.dataset fetch
make feedback-main                                  # riftlab.rl.bandit feedback main
```

Dependencies are managed with uv (`pyproject.toml` + `uv.lock`): add them with `uv add <pkg>`. There's no requirements.txt and no pip.

## Gotchas

- `data/` (comp.json, aliases.json, champions/*.json) is gitignored and not in the repo; `champion_loader.py` silently returns empty data without it.
- Local file locations (accounts.json, data/, matches.csv, caches, weights, ddragon/) come from `riftlab/paths.py`, anchored at the project root. Don't hardcode cwd-relative paths.
- `riftlab.rl.bandit.rank_actions()` is not called anywhere yet — don't describe the bandit as influencing recommendations.
- Riot-sourced data (`matches.csv`, `.match_cache.json`, `game_history.json`, `ddragon/`) is gitignored and was purged from history — regenerate locally, never commit it.
- Older local `matches.csv` files have a 16-column header while newer rows have 20 (multikill columns).

## API notes

- Summoner v4 by-puuid no longer returns `id` — use `/lol/league/v4/entries/by-puuid/{puuid}` for ranked entries
- Dev key rate limit: 20 req/s short, 100 req/2min long — `time.sleep(0.05)` between match fetches is enough
- Match v5 is routed through `americas.api.riotgames.com` (not `br1`)
