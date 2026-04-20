PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
GAMES  ?= 20

.PHONY: setup stats profile-main profile-lab last-main last-lab comp-last-main comp-last-lab comp-live-main comp-live-lab build-live-main build-live-lab clean

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

clean:
	rm -rf .venv __pycache__ *.pyc
