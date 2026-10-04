# ADR-001 — Stack

- **Status:** ACCEPTED
- **Data:** 2026-09-30

## Contexto

Serviço centralizado de cadastro e validação facial 1:1, consumido por vários
sistemas da empresa. A stack foi definida no pedido inicial do projeto.

## Decisão

| Camada | Escolha | Observação |
|---|---|---|
| Linguagem | Python 3.12 (`python:3.12-slim-bookworm`) | Mesma série do WSL (3.12.3). É a versão com maior disponibilidade de wheels para bibliotecas de visão/ML candidatas a motor biométrico (ONNX Runtime, OpenCV etc.). Python 3.13 fica para quando o motor for escolhido. |
| HTTP | FastAPI + Pydantic 2 + Uvicorn | OpenAPI automático em `/docs` (desligado em produção). |
| Banco | PostgreSQL 17 + SQLAlchemy 2 (async, asyncpg) + Alembic | Mesma imagem `postgres:17-alpine` já presente no host. |
| Fila | Redis 7 + arq | Ver ADR-005. |
| Storage | S3 compatível (boto3); SeaweedFS em dev | Ver ADR-004. |
| Criptografia | `cryptography` (AES-256-GCM) | Template biométrico cifrado em repouso. |
| Testes | Pytest + pytest-asyncio + httpx | |
| Lint | Ruff | |

Dependências fixadas com hash em `requirements/*.lock` (gerados por `make lock`
a partir do `pyproject.toml`) e instaladas com `pip --require-hashes`.
Nenhuma dependência é instalada no host; tudo roda em container.

## Consequências

- Build reprodutível e verificável.
- Atualizar dependências exige `make lock` + rebuild.
