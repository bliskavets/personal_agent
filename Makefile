################################################################################
# Makefile — Jarvis Phase 1: ASR microservice
#
# Targets
# -------
#   make build       Build (or rebuild) all Docker images
#   make up          Start the full stack in detached mode
#   make down        Stop and remove containers (preserves volumes)
#   make destroy     Stop containers AND remove all named volumes
#   make restart     Down + up in one step
#   make test        Run the pytest suite inside the asr container
#   make test-local  Run tests directly on the host (needs venv active)
#   make logs        Follow logs for all services
#   make logs-asr    Follow logs for the asr service only
#   make logs-db     Follow logs for the postgres service only
#   make shell       Open a bash shell inside the running asr container
#   make psql        Open a psql shell inside the postgres container
#   make health      Query the /health endpoint
#   make lint        Run ruff linter over service source
#   make format      Auto-format with ruff
#   make clean       Remove __pycache__ trees and .pyc files
#   make help        Print this message
################################################################################

COMPOSE        := docker compose
ASR_SERVICE    := asr
POSTGRES_SERVICE := postgres
ASR_SRC        := services/asr

# Grab the host-side ASR port from .env (default 8000)
-include .env
ASR_PORT ?= 8000

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Docker Compose targets
# ---------------------------------------------------------------------------

.PHONY: build
build:           ## Build (or rebuild) all Docker images
	$(COMPOSE) build

.PHONY: up
up:              ## Start the full stack in detached mode
	$(COMPOSE) up -d

.PHONY: down
down:            ## Stop and remove containers (volumes are preserved)
	$(COMPOSE) down

.PHONY: destroy
destroy:         ## Stop containers AND remove all named volumes  ⚠ data loss
	$(COMPOSE) down -v

.PHONY: restart
restart: down up ## Restart all services

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

.PHONY: test
test:            ## Run pytest inside the running asr container
	$(COMPOSE) exec $(ASR_SERVICE) \
		python -m pytest services/asr/tests/ -v --tb=short 2>/dev/null || \
	$(COMPOSE) run --rm $(ASR_SERVICE) \
		python -m pytest tests/ -v --tb=short

.PHONY: test-local
test-local:      ## Run tests on the host (requires activated venv)
	cd $(ASR_SRC) && python -m pytest tests/ -v --tb=short

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

.PHONY: logs
logs:            ## Follow combined logs for all services
	$(COMPOSE) logs -f

.PHONY: logs-asr
logs-asr:        ## Follow logs for the asr service only
	$(COMPOSE) logs -f $(ASR_SERVICE)

.PHONY: logs-db
logs-db:         ## Follow logs for the postgres service only
	$(COMPOSE) logs -f $(POSTGRES_SERVICE)

# ---------------------------------------------------------------------------
# Shells / REPL
# ---------------------------------------------------------------------------

.PHONY: shell
shell:           ## Open a bash shell inside the running asr container
	$(COMPOSE) exec $(ASR_SERVICE) bash

.PHONY: psql
psql:            ## Open psql inside the postgres container
	$(COMPOSE) exec $(POSTGRES_SERVICE) \
		psql -U $${POSTGRES_USER:-asr} -d $${POSTGRES_DB:-asr}

# ---------------------------------------------------------------------------
# Health / smoke test
# ---------------------------------------------------------------------------

.PHONY: health
health:          ## Query GET /health on the running asr service
	curl -sf http://localhost:$(ASR_PORT)/health | python3 -m json.tool

# ---------------------------------------------------------------------------
# Lint & format  (requires ruff: pip install ruff)
# ---------------------------------------------------------------------------

.PHONY: lint
lint:            ## Run ruff linter over the asr service source
	ruff check $(ASR_SRC)

.PHONY: format
format:          ## Auto-format source with ruff
	ruff format $(ASR_SRC)

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

.PHONY: clean
clean:           ## Remove __pycache__ trees and .pyc files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:            ## Print available make targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
