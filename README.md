# lol-helper

Personal League of Legends stats tracker for BR accounts. Pulls live data from the Riot API to track rank, champion performance, and playstyle patterns — the stuff op.gg's cached pages don't show.

## Requirements

- Python 3.11+
- A Riot API key from [developer.riotgames.com](https://developer.riotgames.com) (dev keys expire every 24h)

## Setup

```bash
make setup
```

Then edit `.env` and add your `RIOT_API_KEY`.

## Usage

```bash
# Rank overview + champion stats for both accounts
make stats

# Playstyle analysis + coaching insights + champion suggestions
make profile-main
make profile-lab

# Analyze more games (default is 20)
make profile-main GAMES=40
```

## Manual commands

```bash
source .venv/bin/activate

python lol_stats.py
python playstyle.py main --games 30
python playstyle.py lab --games 30
```

## Accounts

| Label | Riot ID |
|-------|---------|
| lab | OtherName#TAG |
| main | GameName#TAG (Gold 4) |
