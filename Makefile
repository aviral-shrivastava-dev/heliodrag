# Task aliases. Reviewers on macOS/Linux and CI use these; on Windows run the
# underlying commands directly (each recipe is a single readable line).

export PYTHONPATH := src
DBT := cd dbt && DBT_PROFILES_DIR=.

.PHONY: help install fixtures test lint dbt-build dbt-test dbt-docs \
        smoke backfill dagster docker-up docker-down clean ci

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Install Python dependencies and dbt packages
	pip install -r requirements.txt
	$(DBT) && dbt deps

fixtures:  ## Generate synthetic bronze for offline development
	python -m starlink_drag.cli.fixtures

test:  ## Run unit tests
	pytest tests/ -q

lint:  ## Format and lint
	ruff format src tests
	ruff check src tests

dbt-build:  ## Build and test all models against real data
	$(DBT) && dbt build

dbt-test:  ## Run data tests only
	$(DBT) && dbt test

dbt-docs:  ## Generate and serve the lineage graph
	$(DBT) && dbt docs generate && dbt docs serve

smoke:  ## Validate Space-Track credentials and query (1 request)
	python -m starlink_drag.cli.backfill --smoke

backfill:  ## Load the V2 analysis window (resumable)
	python -m starlink_drag.cli.backfill --window v2-overlap --verbose

dagster:  ## Launch the Dagster UI
	DAGSTER_HOME=$(PWD)/.dagster dagster dev -m starlink_drag.orchestration.definitions

docker-up:  ## Start Dagster + Redpanda locally
	docker compose up -d

docker-down:  ## Stop the local stack
	docker compose down

ci:  ## Everything CI runs: tests plus a full build against fixtures
	pytest tests/ -q
	python -m starlink_drag.cli.fixtures
	$(DBT) && dbt build --target ci --vars '{gp_history_path: ../data/fixtures/bronze/gp_history, omni_path: ../data/fixtures/bronze/omni, interim_path: ../data/fixtures/interim}'

clean:  ## Remove build artifacts (leaves ingested data alone)
	rm -rf dbt/target dbt/dbt_packages .pytest_cache data/ci.duckdb
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
