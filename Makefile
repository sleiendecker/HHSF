# Common tasks. Run `make` to list them.
.DEFAULT_GOAL := help
AOI ?= sequatchie_tn
BACKUP_DIR := data/backups
STAMP := $(shell date +%Y%m%d-%H%M%S)

help:  ## List available commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup:  ## Install Python and frontend dependencies
	uv sync
	cd frontend && npm install

up:  ## Start the database, apply migrations, start the tile server
	docker compose up -d --wait db
	uv run hhsf db upgrade
	docker compose up -d tiles

ingest:  ## Download and load boundary, streams, elevations, parcels, flood zones, roads and soils for an AOI (default: sequatchie_tn)
	uv run hhsf ingest all --aoi $(AOI)
	@# The tile server measures each table's extent when it starts; the map opens on the counties' extent.
	docker compose restart tiles

analyze:  ## Find each stream's steepest stretch; compute head, firm flow and firm power, and the turbine parcels' buildable ground and farmland (needs make ingest)
	uv run hhsf analyze hydro --aoi $(AOI)
	uv run hhsf analyze buildable --aoi $(AOI)
	uv run hhsf analyze farmland --aoi $(AOI)

routing:  ## Start the Valhalla drive-time service (the first start downloads OpenStreetMap and builds for 10-30 min)
	docker compose up -d --wait routing

drive-area:  ## Build the 1-hour drive area from downtown Chattanooga and list the counties it touches (needs make routing)
	uv run hhsf analyze drive-area

drive-time:  ## Drive time from downtown Chattanooga to every corridor of the latest hydro run (needs make routing, make analyze)
	uv run hhsf analyze drive-time --aoi $(AOI)

score:  ## Score and rank the latest hydro run against the 1 kW target (needs make analyze)
	uv run hhsf score hydro --aoi $(AOI)

export:  ## Write the latest scores to data/outputs/ as CSV, GeoJSON and a report (needs make score)
	uv run hhsf export hydro --aoi $(AOI)

web:  ## Run the map at http://localhost:5173
	cd frontend && npm run dev

test:  ## Run tests (integration tests need Docker running)
	uv run pytest

lint:  ## Check code style
	uv run ruff check .
	uv run ruff format --check .

backup:  ## Save the whole database to data/backups/ (includes owner names: keep it private)
	@mkdir -p $(BACKUP_DIR)
	@docker compose exec -T db pg_dump -U hhsf -d hhsf --format=custom > $(BACKUP_DIR)/hhsf-$(STAMP).dump.part
	@# Rename only when complete, so a failed dump is never mistaken for a good one.
	@mv $(BACKUP_DIR)/hhsf-$(STAMP).dump.part $(BACKUP_DIR)/hhsf-$(STAMP).dump
	@echo "Saved $(BACKUP_DIR)/hhsf-$(STAMP).dump ($$(du -h $(BACKUP_DIR)/hhsf-$(STAMP).dump | cut -f1 | tr -d ' '))"

restore:  ## Replace the database with a backup: make restore FILE=data/backups/<name>.dump
	@test -n "$(FILE)" || { echo "Name the backup: make restore FILE=<path>. Backups:"; ls -1t $(BACKUP_DIR)/*.dump 2>/dev/null; exit 1; }
	@test -f "$(FILE)" || { echo "No file $(FILE)"; exit 1; }
	docker compose exec -T db pg_restore -U hhsf -d hhsf --clean --if-exists --no-owner --exit-on-error < $(FILE)
	@# The tile server measures each table when it starts.
	docker compose restart tiles
	@echo "Restored $(FILE). Run make up to apply any migrations newer than the backup."

down:  ## Stop services (data is kept)
	docker compose down

.PHONY: help setup up ingest analyze routing drive-area drive-time score export web test lint backup restore down
