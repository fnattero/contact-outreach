.PHONY: build up down logs lint typecheck test test-e2e check migrate owner demo smoke-worker

COMPOSE := docker compose

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --detach --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --follow web worker beat

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy src

test:
	pytest

test-e2e:
	pytest -m e2e --no-cov

check: lint typecheck test
	python src/manage.py makemigrations --check --dry-run
	python src/manage.py check

migrate:
	$(COMPOSE) exec web python src/manage.py migrate_safe

owner:
	$(COMPOSE) exec web python src/manage.py bootstrap_owner

demo:
	$(COMPOSE) exec -e ALLOW_DEMO_DATA=true web python src/manage.py load_demo_data

smoke-worker:
	$(COMPOSE) exec web python src/manage.py check_worker
