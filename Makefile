PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
GAMES  ?= 20

.PHONY: setup stats profile-main profile-lab clean

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

clean:
	rm -rf .venv __pycache__ *.pyc
