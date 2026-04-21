PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
GAMES  ?= 20

.PHONY: setup stats profile-main profile-lab last-main last-lab comp-last-main comp-last-lab comp-live-main comp-live-lab build-live-main build-live-lab pregame-main pregame-lab runes-main runes-lab exclude-main exclude-lab cache-list feedback-main feedback-lab rl-weights observe-main observe-lab pick-main pick-lab clean

setup: .venv .env

.venv:
	python -m venv .venv
	$(PIP) install --quiet --prefer-binary -r requirements.txt
	@echo "Done. Edit .env and add your RIOT_API_KEY."

.env:
	cp .env.example .env

stats: .venv
	$(PYTHON) lol_stats.py

profile-main: .venv
	$(PYTHON) playstyle.py main --games $(GAMES)

profile-lab: .venv
	$(PYTHON) playstyle.py lab --games $(GAMES)

last-main: .venv
	$(PYTHON) last_match.py main

last-lab: .venv
	$(PYTHON) last_match.py lab

comp-last-main: .venv
	$(PYTHON) comp_check.py --from-last main

comp-last-lab: .venv
	$(PYTHON) comp_check.py --from-last lab

comp-live-main: .venv
	$(PYTHON) comp_check.py --live main

comp-live-lab: .venv
	$(PYTHON) comp_check.py --live lab

build-live-main: .venv
	$(PYTHON) build_advisor.py --live main

build-live-lab: .venv
	$(PYTHON) build_advisor.py --live lab

pregame-main: .venv
	$(PYTHON) pregame.py main

pregame-lab: .venv
	$(PYTHON) pregame.py lab

runes-main: .venv
	$(PYTHON) runes.py main

runes-lab: .venv
	$(PYTHON) runes.py lab

exclude-main: .venv
	$(PYTHON) match_cache.py exclude main --note "$(NOTE)"

exclude-lab: .venv
	$(PYTHON) match_cache.py exclude lab --note "$(NOTE)"

cache-list: .venv
	$(PYTHON) match_cache.py list

feedback-main: .venv
	$(PYTHON) rl_advisor.py feedback main

feedback-lab: .venv
	$(PYTHON) rl_advisor.py feedback lab

rl-weights: .venv
	$(PYTHON) rl_advisor.py weights

observe-main: .venv
	$(PYTHON) observer.py main

observe-lab: .venv
	$(PYTHON) observer.py lab

pick-main: .venv
	$(PYTHON) champ_select.py main

pick-lab: .venv
	$(PYTHON) champ_select.py lab

clean:
	rm -rf .venv __pycache__ *.pyc
