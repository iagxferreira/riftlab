# lol-helper

Personal LoL stats tracker for two BR accounts. Pulls data from the Riot API and surfaces patterns that op.gg's cached pages can't show.

## Accounts

| Label | Riot ID | Notes |
|-------|---------|-------|
| lab | OtherName#TAG | experimentation account |
| main | GameName#TAG | main, Gold 4 |

Focus champions: **Cassiopeia**, **Syndra**

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add RIOT_API_KEY
```

Riot dev keys expire every 24h — regenerate at https://developer.riotgames.com if you get 401s.

## Running

```bash
# Rank overview + per-champion stats (both accounts)
python lol_stats.py

# Playstyle analysis + champion suggestions
python playstyle.py main
python playstyle.py lab
python playstyle.py main --games 40
```

## Files

- `lol_stats.py` — rank overview and champion stats tables
- `playstyle.py` — playstyle profiling, coaching insights, champion suggestions
- `requirements.txt` — pinned deps (requests, dotenv, pandas, rich)
- `.env` — secrets, never commit

## API notes

- Summoner v4 by-puuid no longer returns `id` — use `/lol/league/v4/entries/by-puuid/{puuid}` for ranked entries
- Dev key rate limit: 20 req/s short, 100 req/2min long — `time.sleep(0.05)` between match fetches is enough
- Match v5 is routed through `americas.api.riotgames.com` (not `br1`)
