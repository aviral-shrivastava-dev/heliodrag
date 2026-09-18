# Every target is a thin wrapper over uv. CI runs the same targets, so if it
# passes here it passes there.

UV ?= uv
DBT_DIR := transform
# dbt >=1.9 takes --project-dir on the subcommand, not globally.
DBT := $(UV) run dbt
DBT_FLAGS := --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR)

.DEFAULT_GOAL := help
.PHONY: help setup lint format typecheck test test-cov run build docs clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install everything, install the git hooks
	$(UV) sync --all-groups
	$(UV) run pre-commit install
	@test -f .env || (cp .env.example .env && echo "created .env -- fill it in")

lint: ## ruff check + format check + mypy
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

format: ## Apply ruff fixes and formatting
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## mypy only
	$(UV) run mypy

test: ## Run the test suite (never touches the network)
	$(UV) run pytest

test-cov: ## Run tests with a coverage report for science/
	$(UV) run pytest --cov --cov-report=term-missing

run: ## Launch the Dagster UI at http://localhost:3000
	$(UV) run dagster dev -m starlink_drag.definitions

build: ## Sync bronze views, then run dbt build (models + tests)
	$(UV) run starlink-drag warehouse sync
	$(DBT) build $(DBT_FLAGS)

docs: ## Build dbt docs into transform/target/
	$(DBT) deps $(DBT_FLAGS)
	$(DBT) docs generate $(DBT_FLAGS)
	$(UV) run starlink-drag data-dictionary
	@echo "open $(DBT_DIR)/target/index.html"

clean: ## Remove build, cache and dbt artefacts (never touches data/)
	rm -rf .mypy_cache .pytest_cache .ruff_cache .coverage htmlcov
	rm -rf $(DBT_DIR)/target $(DBT_DIR)/dbt_packages $(DBT_DIR)/logs
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
