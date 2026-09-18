.PHONY: setup install env-check bootstrap cron-install cron-status cron-remove test run-once clean

PYTHON := python3
PROJECT_DIR := $(shell pwd)

# Full from-zero setup: deps, .env check, Notion KB bootstrap, cron install.
# The session cookie (session/cookie.txt, session/action_request.txt) is
# never automated — see README.md "Session cookie" for the manual step.
setup: install env-check bootstrap cron-install
	@echo ""
	@echo "Setup complete. One manual step remains: seed session/cookie.txt"
	@echo "and session/action_request.txt — see README.md 'Session cookie'."

install:
	$(PYTHON) -m pip install -r requirements.txt
	mkdir -p session

env-check:
	@test -f .env || { \
		echo "Missing .env — copy .env.example to .env and fill in real credentials first:"; \
		echo "  cp .env.example .env && chmod 600 .env"; \
		exit 1; \
	}

bootstrap:
	$(PYTHON) scripts/bootstrap_kb.py

cron-install:
	@( crontab -l 2>/dev/null | grep -v 'scripts/ticker.sh' ; echo "* * * * * $(PROJECT_DIR)/scripts/ticker.sh" ) | crontab -
	@echo "Installed per-minute ticker cron job (only invokes a real cycle once session/next_wake.txt is due)."

cron-status:
	@crontab -l 2>/dev/null | grep ticker.sh || echo "No ticker cron job installed. Run 'make cron-install'."

cron-remove:
	@crontab -l 2>/dev/null | grep -v 'scripts/ticker.sh' | crontab -
	@echo "Removed the ticker cron job."

test:
	pytest -v

run-once:
	scripts/run_hourly_cycle.sh

clean:
	rm -rf __pycache__ scripts/__pycache__ tests/__pycache__ har/__pycache__ .pytest_cache
