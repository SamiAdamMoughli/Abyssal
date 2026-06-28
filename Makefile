# =============================================================================
# VesselX — Makefile
# All commands operate from the repo root.
# =============================================================================

COMPOSE        := docker compose
COMPOSE_PROD   := $(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml
ENV_FILE       ?= .env

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
.PHONY: help
help:
	@echo ""
	@echo "  VesselX IaC — available targets"
	@echo ""
	@echo "  Dev"
	@echo "    make up            Start all core services (dev mode, hot reload)"
	@echo "    make up-live       Start with live AIS gateway (adds --profile live)"
	@echo "    make up-client     Start with Vite frontend (adds --profile client)"
	@echo "    make up-ops        Start with Flower + brain-api ops panel"
	@echo "    make down          Stop and remove containers (keep volumes)"
	@echo "    make reset         Stop, remove containers AND volumes (full wipe)"
	@echo ""
	@echo "  Production"
	@echo "    make up-prod       Start all services with production overrides"
	@echo "    make down-prod     Stop production stack"
	@echo ""
	@echo "  Database"
	@echo "    make migrate       Run alembic upgrade head inside spatial-engine"
	@echo "    make migrate-sql   Print the pending SQL without applying it"
	@echo "    make psql          Open psql shell on vesselx-core-db"
	@echo ""
	@echo "  Build"
	@echo "    make build         Build all images (no cache)"
	@echo "    make build-cache   Build all images (with cache)"
	@echo ""
	@echo "  Utilities"
	@echo "    make logs          Tail logs for all running services"
	@echo "    make logs-api      Tail only the spatial-engine API logs"
	@echo "    make shell         Open a bash shell in vesselx-spatial-engine"
	@echo "    make validate-env  Check that all required env vars are set"
	@echo ""

# ---------------------------------------------------------------------------
# Dev stack
# ---------------------------------------------------------------------------
.PHONY: up
up: validate-env
	$(COMPOSE) --env-file $(ENV_FILE) up -d

.PHONY: up-live
up-live: validate-env
	$(COMPOSE) --env-file $(ENV_FILE) --profile live up -d

.PHONY: up-client
up-client: validate-env
	$(COMPOSE) --env-file $(ENV_FILE) --profile client up -d

.PHONY: up-ops
up-ops: validate-env
	$(COMPOSE) --env-file $(ENV_FILE) --profile ops up -d

.PHONY: down
down:
	$(COMPOSE) down

.PHONY: reset
reset:
	@echo "WARNING: This will delete all volumes (database data, Redis data)."
	@read -p "Type YES to confirm: " confirm && [ "$$confirm" = "YES" ]
	$(COMPOSE) down -v

# ---------------------------------------------------------------------------
# Production stack
# ---------------------------------------------------------------------------
.PHONY: up-prod
up-prod: validate-env
	$(COMPOSE_PROD) --env-file $(ENV_FILE) up -d

.PHONY: down-prod
down-prod:
	$(COMPOSE_PROD) down

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: migrate
migrate:
	$(COMPOSE) exec vesselx-spatial-engine alembic upgrade head

.PHONY: migrate-sql
migrate-sql:
	$(COMPOSE) exec vesselx-spatial-engine alembic upgrade head --sql

.PHONY: psql
psql:
	$(COMPOSE) exec vesselx-core-db psql -U vesselx -d vesselx

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
.PHONY: build
build:
	$(COMPOSE) build --no-cache

.PHONY: build-cache
build-cache:
	$(COMPOSE) build

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
.PHONY: logs
logs:
	$(COMPOSE) logs -f --tail=100

.PHONY: logs-api
logs-api:
	$(COMPOSE) logs -f --tail=100 vesselx-spatial-engine

.PHONY: shell
shell:
	$(COMPOSE) exec vesselx-spatial-engine bash

.PHONY: validate-env
validate-env:
	@bash scripts/validate-env.sh $(ENV_FILE)
