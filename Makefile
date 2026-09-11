RUN   := uv run python -m riftlab
GAMES ?= 20

.PHONY: setup stats profile-main profile-lab last-main last-lab dataset-fetch dataset-stats exclude-main exclude-lab cache-list rl-fetch-main rl-fetch-lab rl-summary rl-evaluate test clean

setup: .env
	uv sync
	@echo "Done. Edit .env and add your RIOT_API_KEY."

.env:
	cp .env.example .env

# --- analysis ---------------------------------------------------------------

stats:
	$(RUN).analysis.stats

profile-main:
	$(RUN).analysis.playstyle main --games $(GAMES)

profile-lab:
	$(RUN).analysis.playstyle lab --games $(GAMES)

last-main:
	$(RUN).analysis.last_match main

last-lab:
	$(RUN).analysis.last_match lab

# --- data -------------------------------------------------------------------

dataset-fetch:
	$(RUN).ingest.dataset fetch

dataset-stats:
	$(RUN).ingest.dataset stats

exclude-main:
	$(RUN).cache exclude main --note "$(NOTE)"

exclude-lab:
	$(RUN).cache exclude lab --note "$(NOTE)"

cache-list:
	$(RUN).cache list

# --- bandit -----------------------------------------------------------------

rl-fetch-main:
	$(RUN).rl.bandit fetch main --games $(GAMES)

rl-fetch-lab:
	$(RUN).rl.bandit fetch lab --games $(GAMES)

rl-summary:
	$(RUN).rl.bandit summary

rl-evaluate:
	$(RUN).rl.bandit evaluate

test:
	uv run pytest

clean:
	rm -rf .venv
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
