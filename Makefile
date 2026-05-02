.PHONY: install lint format typecheck test up down migrate revision seed seed-admin seed-templates openapi build-node-agent

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

seed: seed-admin seed-templates

seed-admin:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_admin

seed-templates:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_templates

openapi:
	mkdir -p docs
	SECRET_KEY=dummy-for-export uv run --package gamehost-api python -m gamehost_api.scripts.export_openapi > docs/openapi.json

build-node-agent:
	docker build -f apps/node_agent/Dockerfile -t gamehost-node:dev .
