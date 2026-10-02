.PHONY: build up down logs backend-lint backend-typecheck backend-test backend-check frontend-install frontend-check test-e2e security-check check migrate owner demo smoke-worker backup restore \
	backend-lint-local backend-typecheck-local backend-test-local backend-check-local frontend-check-local test-e2e-local security-check-local backend-shell

COMPOSE := docker compose --project-directory . --file infra/docker-compose.yml
BACKEND_DIR := backend
FRONTEND_DIR := frontend
PNPM ?= pnpm

# Quality gates run inside containers: this machine has no Python 3.12, Node or pnpm. The `*-local`
# targets hold the same command chains for running natively (CI with setup-python/setup-node).
BACKEND_RUN := $(COMPOSE) run --rm --build backend-tests
FRONTEND_RUN := $(COMPOSE) run --rm --build frontend-tests

# Consumed by backend-tests `user:` so bind-mounted writes are owned by the invoking user.
export DOCKER_UID := $(shell id -u)
export DOCKER_GID := $(shell id -g)

BACKEND_LINT_CMD := ruff check . && ruff format --check .
BACKEND_TYPECHECK_CMD := mypy src
BACKEND_TEST_CMD := pytest
BACKEND_DJANGO_CMD := python src/manage.py makemigrations --check --dry-run && python src/manage.py check
BACKEND_CHECK_CMD := $(BACKEND_LINT_CMD) && $(BACKEND_TYPECHECK_CMD) && $(BACKEND_TEST_CMD) && $(BACKEND_DJANGO_CMD)
FRONTEND_CHECK_CMD := pnpm lint && pnpm typecheck && pnpm test && pnpm build
E2E_CMD := pytest -m e2e --no-cov

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --detach --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs --follow frontend backend postgres redis minio

backend-lint:
	$(BACKEND_RUN) sh -c '$(BACKEND_LINT_CMD)'

backend-typecheck:
	$(BACKEND_RUN) sh -c '$(BACKEND_TYPECHECK_CMD)'

backend-test:
	$(BACKEND_RUN) sh -c '$(BACKEND_TEST_CMD)'

backend-check:
	$(BACKEND_RUN) sh -c '$(BACKEND_CHECK_CMD)'

backend-shell:
	$(BACKEND_RUN) sh

frontend-install:
	cd $(FRONTEND_DIR) && $(PNPM) install --frozen-lockfile

frontend-check:
	$(FRONTEND_RUN) sh -c '$(FRONTEND_CHECK_CMD)'

test-e2e:
	$(BACKEND_RUN) sh -c '$(E2E_CMD)'

security-check:
	$(BACKEND_RUN) sh -c 'pip-audit'
	$(FRONTEND_RUN) sh -c 'pnpm audit --prod'

check: backend-check frontend-check

# Native equivalents for CI, where the toolchain is installed on the runner instead.
backend-lint-local:
	cd $(BACKEND_DIR) && $(BACKEND_LINT_CMD)

backend-typecheck-local:
	cd $(BACKEND_DIR) && $(BACKEND_TYPECHECK_CMD)

backend-test-local:
	cd $(BACKEND_DIR) && $(BACKEND_TEST_CMD)

backend-check-local:
	cd $(BACKEND_DIR) && $(BACKEND_CHECK_CMD)

frontend-check-local:
	cd $(FRONTEND_DIR) && $(FRONTEND_CHECK_CMD)

test-e2e-local:
	cd $(BACKEND_DIR) && $(E2E_CMD)

security-check-local:
	cd $(BACKEND_DIR) && pip-audit
	cd $(FRONTEND_DIR) && $(PNPM) audit --prod

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
