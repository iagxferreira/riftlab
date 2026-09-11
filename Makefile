RUN   := uv run python -m riftlab
GAMES ?= 20

.PHONY: setup stats profile-main profile-lab last-main last-lab dataset-fetch dataset-stats ddragon-fetch comp-last-main comp-last-lab comp-live-main comp-live-lab build-live-main build-live-lab pregame-main pregame-lab runes-main runes-lab exclude-main exclude-lab cache-list rl-fetch-main rl-fetch-lab rl-summary rl-evaluate test observe-main observe-lab pick-main pick-lab clean

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

ddragon-fetch:
	$(RUN).ingest.ddragon fetch

exclude-main:
	$(RUN).cache exclude main --note "$(NOTE)"

exclude-lab:
	$(RUN).cache exclude lab --note "$(NOTE)"

cache-list:
	$(RUN).cache list

# --- advisors ---------------------------------------------------------------

comp-last-main:
	$(RUN).advisors.comp_check --from-last main

comp-last-lab:
	$(RUN).advisors.comp_check --from-last lab

comp-live-main:
	$(RUN).advisors.comp_check --live main

comp-live-lab:
	$(RUN).advisors.comp_check --live lab

build-live-main:
	$(RUN).advisors.build_advisor --live main

build-live-lab:
	$(RUN).advisors.build_advisor --live lab

pregame-main:
	$(RUN).advisors.pregame main

pregame-lab:
	$(RUN).advisors.pregame lab

runes-main:
	$(RUN).advisors.runes main

runes-lab:
	$(RUN).advisors.runes lab

observe-main:
	$(RUN).advisors.observer main

observe-lab:
	$(RUN).advisors.observer lab

pick-main:
	$(RUN).advisors.champ_select main

pick-lab:
	$(RUN).advisors.champ_select lab

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
