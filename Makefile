# Common tasks. Run `make` to list them.
.DEFAULT_GOAL := help
AOI ?= sequatchie_tn

help:  ## List available commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

setup:  ## Install Python and frontend dependencies
	uv sync
	cd frontend && npm install

up:  ## Start the database, apply migrations, start the tile server
	docker compose up -d --wait db
	uv run hhsf db upgrade
	docker compose up -d tiles

ingest:  ## Download and load boundary, streams, elevations, parcels and flood zones for an AOI (default: sequatchie_tn)
	uv run hhsf ingest all --aoi $(AOI)
	@# The tile server measures each table's extent when it starts; the map opens on the counties' extent.
	docker compose restart tiles

analyze:  ## Find each stream's steepest stretch; compute head, firm flow and firm power (needs make ingest)
	uv run hhsf analyze hydro --aoi $(AOI)

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

down:  ## Stop services (data is kept)
	docker compose down

.PHONY: help setup up ingest analyze score export web test lint down
