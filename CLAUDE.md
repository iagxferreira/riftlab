# RiftLab

Experimental data science / RL project on League of Legends data from my BR ranked games. Pulls data from the Riot API, builds a local ranked-match dataset, runs descriptive analysis, and learns a contextual bandit for keystone rune choice offline from logged games (`src/riftlab/rl/`). See README.md for what exists vs what's planned.

## Accounts

Configured in the local, gitignored `accounts.json` (template: `accounts.example.json`; falls back to `ACCOUNT_MAIN`, `REGION` and `REGION_ROUTING` in `.env`). There's one account, labelled `main`; the old second (`lab`) account is gone. Never commit real Riot IDs.

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
make last                                           # riftlab.analysis.last_match main
make dataset-fetch                                  # riftlab.ingest.dataset fetch
make rl-summary                                     # riftlab.rl.bandit summary
make rl-evaluate                                    # chronological replay evaluation
uv run python -m riftlab.rl.bandit recommend Syndra --ally "..." --enemy "..."
make test                                           # pytest
```

Dependencies are managed with uv (`pyproject.toml` + `uv.lock`): add them with `uv add <pkg>` (`uv add --dev` for tooling). There's no requirements.txt and no pip.

## Gotchas

- Local file locations (accounts.json, matches.csv, the match cache) come from `riftlab/paths.py`, anchored at the project root. Don't hardcode cwd-relative paths.
- The bandit has no weights file: it's rebuilt from `.match_cache.json` on every run. Every participant of every cached match is a sample, so all 10 players in each cached game are training data, not just the account owner.
- Bandit context uses Data Dragon (champion tags + attack/magic ratings) fetched live; tests use synthetic `StaticData` and need no network.
- The replay evaluation currently shows no detectable edge for the bandit's recommendations (see README). Don't describe it as improving outcomes.
- Riot-sourced data (`matches.csv`, `.match_cache.json`) is gitignored and was purged from history. Regenerate it locally and never commit it.
- Older local `matches.csv` files have a 16-column header while newer rows have 20 (multikill columns).
- The rule-based advisors and the Data Dragon downloader were removed because they depended on champion data that was never committed. They're in git history up to `ed9043d`.

## API notes

- Summoner v4 by-puuid no longer returns `id` — use `/lol/league/v4/entries/by-puuid/{puuid}` for ranked entries
- Dev key rate limit: 20 req/s short, 100 req/2min long — `time.sleep(0.05)` between match fetches is enough
- Match v5 is routed through `americas.api.riotgames.com` (not `br1`)
