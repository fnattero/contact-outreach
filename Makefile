.PHONY: build up down logs backend-lint backend-typecheck backend-test backend-check frontend-install frontend-check test-e2e security-check check migrate owner demo smoke-worker backup restore

COMPOSE := docker compose --project-directory . --file infra/docker-compose.yml
BACKEND_DIR := backend
FRONTEND_DIR := frontend
PNPM ?= pnpm

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --detach --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --follow frontend backend postgres redis minio

backend-lint:
	cd $(BACKEND_DIR) && ruff check .
	cd $(BACKEND_DIR) && ruff format --check .

backend-typecheck:
	cd $(BACKEND_DIR) && mypy src

backend-test:
	cd $(BACKEND_DIR) && pytest

backend-check: backend-lint backend-typecheck backend-test
	cd $(BACKEND_DIR) && python src/manage.py makemigrations --check --dry-run
	cd $(BACKEND_DIR) && python src/manage.py check

frontend-install:
	cd $(FRONTEND_DIR) && $(PNPM) install --frozen-lockfile

frontend-check:
	cd $(FRONTEND_DIR) && $(PNPM) lint
	cd $(FRONTEND_DIR) && $(PNPM) typecheck
	cd $(FRONTEND_DIR) && $(PNPM) test
	cd $(FRONTEND_DIR) && $(PNPM) build

test-e2e:
	cd $(BACKEND_DIR) && pytest -m e2e --no-cov

security-check:
	cd $(BACKEND_DIR) && pip-audit
	cd $(FRONTEND_DIR) && $(PNPM) audit --prod

check: backend-check frontend-check

migrate:
	$(COMPOSE) exec backend python src/manage.py migrate_safe

owner:
	$(COMPOSE) exec backend python src/manage.py bootstrap_owner

demo:
	$(COMPOSE) exec -e ALLOW_DEMO_DATA=true backend python src/manage.py load_demo_data

smoke-worker:
	$(COMPOSE) exec backend python src/manage.py check_worker

backup:
	./backend/scripts/backup.sh $(BACKUP_ROOT)

restore:
	./backend/scripts/restore.sh --confirm $(BACKUP)
