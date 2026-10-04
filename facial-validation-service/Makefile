COMPOSE ?= docker compose
RUN_API = $(COMPOSE) exec api

.PHONY: help up down restart ps logs build migrate test test-unit test-integration test-models shell lint secrets tenant lock models dataset-lfw evaluate

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

up: ## Sobe todos os containers do projeto
	$(COMPOSE) up -d --build

down: ## Para e remove os containers do projeto (mantém volumes)
	$(COMPOSE) down

restart: ## Reinicia api e worker
	$(COMPOSE) restart api worker

ps: ## Status dos containers do projeto
	$(COMPOSE) ps

logs: ## Logs em tempo real (api e worker)
	$(COMPOSE) logs -f api worker

build: ## Reconstrói a imagem da aplicação
	$(COMPOSE) build api

migrate: ## Aplica migrations no banco principal
	$(RUN_API) alembic upgrade head

test: ## Executa toda a suíte (unit + api + integration)
	$(RUN_API) pytest

test-unit: ## Executa apenas testes sem dependências externas
	$(RUN_API) pytest -m "not integration and not biometric and not models and not evaluation"

test-integration: ## Executa testes contra PostgreSQL, Redis e S3 do compose
	$(RUN_API) pytest -m integration

test-models: ## Testes com os modelos ONNX reais (exige make models)
	$(RUN_API) pytest -m models

models: ## Baixa os modelos ONNX (YuNet, SFace) para models/ e confere o SHA-256
	$(RUN_API) python -m app.cli download-models

dataset-lfw: ## Baixa o LFW (View 2) para datasets/lfw e confere o SHA-256
	$(RUN_API) python -m evaluation download-lfw

evaluate: ## Avaliação dos componentes configurados no LFW: make evaluate ARGS="--folds 1"
	$(RUN_API) python -m evaluation lfw $(ARGS)

shell: ## Abre um shell no container da API
	$(COMPOSE) exec api bash

lint: ## Ruff
	$(RUN_API) ruff check app tests migrations evaluation

tenant: ## Cria tenant: make tenant NAME="Projeto X" SLUG=projeto-x
	$(RUN_API) python -m app.cli create-tenant --name "$(NAME)" --slug "$(SLUG)"

secrets: ## Imprime segredos aleatórios para preencher o .env local
	@echo "POSTGRES_PASSWORD=$$(openssl rand -hex 24)"
	@echo "REDIS_PASSWORD=$$(openssl rand -hex 24)"
	@echo "S3_ACCESS_KEY=$$(openssl rand -hex 12)"
	@echo "S3_SECRET_KEY=$$(openssl rand -hex 24)"
	@echo "TEMPLATE_ENCRYPTION_KEY=$$(openssl rand -base64 32 | tr '+/' '-_')"

lock: ## Regera os arquivos de lock com hashes (requirements/*.lock)
	docker run --rm -v "$$PWD":/src -w /src python:3.12-slim-bookworm sh -c '\
	  pip install -q uv && \
	  uv pip compile pyproject.toml --generate-hashes --python-version 3.12 -o requirements/runtime.lock && \
	  uv pip compile pyproject.toml --extra dev --generate-hashes --python-version 3.12 -o requirements/dev.lock'
