RUN   := uv run python -m riftlab
GAMES ?= 20

.PHONY: setup stats profile last dataset-fetch dataset-stats exclude cache-list rl-fetch rl-summary rl-evaluate test clean

setup: .env
	uv sync
	@echo "Done. Edit .env and add your RIOT_API_KEY."

.env:
	cp .env.example .env

# --- analysis ---------------------------------------------------------------

stats:
	$(RUN).analysis.stats

profile:
	$(RUN).analysis.playstyle main --games $(GAMES)

last:
	$(RUN).analysis.last_match main

# --- data -------------------------------------------------------------------

dataset-fetch:
	$(RUN).ingest.dataset fetch

dataset-stats:
	$(RUN).ingest.dataset stats

exclude:
	$(RUN).cache exclude main --note "$(NOTE)"

cache-list:
	$(RUN).cache list

# --- bandit -----------------------------------------------------------------

rl-fetch:
	$(RUN).rl.bandit fetch main --games $(GAMES)

rl-summary:
	$(RUN).rl.bandit summary

rl-evaluate:
	$(RUN).rl.bandit evaluate

test:
	uv run pytest

clean:
	rm -rf .venv
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
