.PHONY: install lint format typecheck test up down migrate revision seed

install:
	uv sync --all-packages
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy apps packages

test:
	uv run pytest

up:
	docker compose -f deploy/docker-compose.yml up -d

down:
	docker compose -f deploy/docker-compose.yml down

migrate:
	set -a && . ./.env && set +a && cd apps/api && uv run alembic upgrade head

revision:
	set -a && . ./.env && set +a && cd apps/api && uv run alembic revision --autogenerate -m "$(m)"

seed:
	@echo "available from Stage 2 (seed templates)"
