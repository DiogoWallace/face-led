# Facial Validation Service

## Objetivo

Microserviço centralizado, consumido por diferentes sistemas da empresa, para:

1. **Cadastro facial inicial** — detecção, qualidade, prova de vida e geração do
   template/embedding que servirá de referência.
2. **Validação facial posterior (1:1)** — prova de vida, embedding, comparação
   com a referência e decisão padronizada: `APPROVED`, `REJECTED` ou `ERROR`.

> **Estado atual:** protótipo funcional, **não calibrado para produção**. Há
> adapters OpenCV para detecção (YuNet), medição de qualidade, embedding e
> comparação (SFace), avaliados no LFW pelo próprio serviço (EER 1,47%,
> [evaluation-lfw.md](docs/evaluation-lfw.md)), e **liveness ativo próprio**
> com desafio sorteado e captura guiada
> ([ADR-010](docs/decisions/ADR-010-active-liveness.md)). Threshold e
> parâmetros de liveness estão PENDING CALIBRATION, e o serviço se recusa a
> subir com eles fora de `APP_ENV=local|test`. O padrão continua `none`: sem
> motor configurado, todo processamento termina em
> `ERROR / PROVIDER_NOT_CONFIGURED`, de propósito.

## Arquitetura

Clean Architecture + Hexagonal, DDD tático. O domínio não conhece FastAPI,
banco, fila, storage nem biblioteca biométrica; tudo entra por portas.
Detalhes em [docs/architecture.md](docs/architecture.md).

## Stack

Python 3.12 · FastAPI · Pydantic 2 · SQLAlchemy 2 (async) · Alembic · PostgreSQL 17 ·
Redis 7 · arq · S3 compatível (SeaweedFS em dev) · Pytest · httpx · Ruff · Docker Compose.

## Pré-requisitos

WSL2 com Docker Engine e Docker Compose v2, `make` e `openssl`. Python não é
necessário no host.

## Como iniciar

```bash
cp .env.example .env
make secrets          # gera valores aleatórios; copie para o .env
make up
make migrate
make tenant NAME="Meu sistema" SLUG=meu-sistema   # guarde a API key exibida
make models           # opcional: modelos ONNX do OpenCV (fora do Git, SHA-256 conferido)
```

## Como parar

```bash
make down             # para e remove containers; volumes são mantidos
```

## Como executar testes

```bash
make test             # unit + API + integração (PostgreSQL, Redis, S3 reais)
make test-unit        # sem dependências externas
make test-integration
make test-models      # adapter OpenCV com os modelos reais (exige make models)
make dataset-lfw      # LFW público (View 2) para datasets/lfw, SHA-256 conferido
make evaluate         # FAR/FRR/EER/10-fold, latência e vazão dos componentes configurados
```

`tests/models` usa só imagens sintéticas, sem pessoas, e fica em SKIP sem os
modelos. `make evaluate` (pacote `evaluation/`) mede os componentes configurados
no LFW e grava o relatório em `evaluation-results/` (fora do Git); `pytest -m
evaluation` é a regressão no fold 1. Os testes biométricos (`tests/biometric`)
ficam em SKIP até existir um dataset de selfies autorizado; nenhum resultado
biométrico é simulado.

## Como acessar a API

- OpenAPI/Swagger: <http://127.0.0.1:18100/docs>
- Autenticação: header `X-API-Key: fvs_...` (por tenant)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/v1/subjects/{subject_id}/face` | Cadastro facial (multipart `image`). 202 |
| `POST` | `/api/v1/subjects/{subject_id}/verifications` | Nova validação (multipart `image` + header `Idempotency-Key`). 202 |
| `GET` | `/api/v1/verifications/{verification_id}` | Resultado da validação |
| `GET` | `/api/v1/face-registrations/{registration_id}` | Resultado do envio de cadastro |
| `GET` | `/api/v1/subjects/{subject_id}/face` | Subject tem referência ativa? + último envio |
| `PUT` | `/api/v1/subjects/{subject_id}/face` | Recadastro: substitui a referência se o novo envio for aprovado. 202 |

**Webhook (opcional):** `make tenant` e depois
`python -m app.cli set-webhook --slug meu-sistema --url https://...`. O serviço avisa
cada resultado com o mesmo JSON do GET, assinado com HMAC-SHA256. Detalhes e
exemplo de validação em [docs/api.md](docs/api.md#webhook-de-resultado).

Respostas trazem `decision`, `reason` e `quality` (códigos como `BLURRY` e
`TOO_DARK`); score e medidas nunca saem. Contrato v1 e regras de compatibilidade:
[docs/api.md](docs/api.md).
| `GET` | `/health` | Liveness do processo |
| `GET` | `/ready` | Readiness (PostgreSQL, Redis, S3) |

```bash
curl -H "X-API-Key: $API_KEY" -F "image=@selfie.jpg;type=image/jpeg" \
  http://127.0.0.1:18100/api/v1/subjects/cliente-123/face
```

## Portas utilizadas

| Serviço | Host (127.0.0.1) | Container |
|---|---|---|
| API | 18100 | 8000 |
| PostgreSQL | 18110 | 5432 |
| Redis | 18120 | 6379 |
| S3 (SeaweedFS) | 18130 | 8333 |

Escolhidas após inventário do WSL; justificativa em [docs/development.md](docs/development.md).

## Containers

| Container | Imagem | Função |
|---|---|---|
| `facial-validation-api` | `facial-validation/api:local` | API HTTP |
| `facial-validation-worker` | `facial-validation/api:local` | Worker arq |
| `facial-validation-postgres` | `postgres:17-alpine` | Banco |
| `facial-validation-redis` | `redis:7-alpine` | Fila |
| `facial-validation-s3` | `chrislusf/seaweedfs:4.48` | Object storage S3 local |

Rede própria `facial-validation-network`; volumes `facial-validation-{postgres,redis,s3}-data`.

## Banco

PostgreSQL, banco `facial_validation` (e `facial_validation_test` para testes).
Tabelas: `tenants`, `subjects`, `face_registrations`, `liveness_sessions`,
`verifications`, `verification_events`. Migrations em `migrations/` (`make migrate`).

## Redis

Fila do arq (db 0) com senha obrigatória. Testes usam o db 15.

## Storage

S3 compatível, bucket privado `facial-validation-captures`. Em dev, SeaweedFS
(a imagem do MinIO não estava disponível — [ADR-004](docs/decisions/ADR-004-storage.md)).
O template biométrico fica cifrado (AES-256-GCM) no PostgreSQL, não no storage.

## Variáveis de ambiente

Todas documentadas em [`.env.example`](.env.example). Principais:

| Variável | Uso |
|---|---|
| `APP_ENV`, `APP_NAME`, `LOG_LEVEL` | Aplicação |
| `DATABASE_URL`, `POSTGRES_*` | Banco (no compose a URL é montada automaticamente) |
| `REDIS_URL`, `REDIS_PASSWORD` | Fila |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Storage |
| `BIOMETRIC_DETECTOR`, `BIOMETRIC_QUALITY_ASSESSOR`, `BIOMETRIC_EMBEDDER`, `BIOMETRIC_COMPARATOR`, `LIVENESS_PROVIDER`, `LIVENESS_REQUIREMENT` | Componentes biométricos (ADR-006) |
| `BIOMETRIC_MODELS_DIR`, `BIOMETRIC_INFERENCE_THREADS`, `BIOMETRIC_OPENCV_THREADS`, `BIOMETRIC_DETECTOR_SCORE_THRESHOLD` | Runtime OpenCV (só o worker carrega modelos) |
| `BIOMETRIC_MODEL`, `BIOMETRIC_MODEL_VERSION`, `FACE_MATCH_*` | FaceMatchPolicy (PENDING CALIBRATION) |
| `QUALITY_*` | QualityGate (PENDING CALIBRATION) |
| `LIVENESS_CALIBRATION_STATUS`, `LIVENESS_SAME_PERSON_MIN_SIMILARITY`, `LIVENESS_*` (passos, prazo, quadros, limites) | Liveness ativo (ADR-010, PENDING CALIBRATION) |
| `FACE_MATCH_POLICY_VERSION`, `FACE_MATCH_MIN_SIMILARITY` | Política de match |
| `CAPTURE_RETENTION` | `KEEP` (padrão original) ou `DELETE_AFTER_PROCESSING` — PENDING LEGAL/BUSINESS DECISION |
| `TEMPLATE_ENCRYPTION_KEY` | Chave AES-256 do template (e do segredo do webhook) |
| `WEBHOOK_TIMEOUT_SECONDS` | Tempo máximo de cada tentativa de webhook (padrão 10) |
| `FVS_*_PORT` | Portas no host |

O `.env` nunca é versionado.

## Estrutura do projeto

```
app/
  domain/          entities, value_objects, services (FaceMatchPolicy), repositories, exceptions
  application/     use_cases, dto, ports (FaceDetector, FaceEmbedder..., CaptureStorage, TaskQueue)
  infrastructure/  database, storage, queue, biometric (none, opencv), security, observability
  interfaces/http/ routes, schemas, dependencies, errors
  container.py     composition root
  main.py          app FastAPI    worker.py  worker arq    cli.py  comandos admin
migrations/        Alembic
tests/             unit, api, integration, models, evaluation, biometric
evaluation/        suíte de avaliação (LFW): python -m evaluation — fora da imagem de runtime
models/            modelos ONNX baixados por make models (fora do Git e da imagem)
docs/              architecture.md, development.md, decisions/
docker/            init do PostgreSQL e entrypoint do S3 local
requirements/      locks com hash
```

## Decisões arquiteturais

| ADR | Tema | Status |
|---|---|---|
| [001](docs/decisions/ADR-001-stack.md) | Stack | ACCEPTED |
| [002](docs/decisions/ADR-002-architecture.md) | Arquitetura e segurança base | ACCEPTED + pendências |
| [003](docs/decisions/ADR-003-biometric-provider.md) | Motor biométrico | PROPOSED (OpenCV SFace para calibração); liveness e threshold pendentes |
| [004](docs/decisions/ADR-004-storage.md) | Storage | ACCEPTED (interface) + pendências |
| [005](docs/decisions/ADR-005-async-processing.md) | Processamento assíncrono | ACCEPTED (MVP) |
| [006](docs/decisions/ADR-006-biometric-components.md) | Componentes biométricos independentes | ACCEPTED |
| [007](docs/decisions/ADR-007-api-exposure.md) | O que a API expõe (sem score; qualidade só em códigos; consulta do cadastro) | ACCEPTED |
| [008](docs/decisions/ADR-008-reenrollment.md) | Recadastro (PUT; confia no consumidor; template antigo apagado) | ACCEPTED |
| [009](docs/decisions/ADR-009-result-webhook.md) | Webhook de resultado (URL por tenant via CLI; corpo = GET; HMAC; outbox; ~24 h de tentativas) | ACCEPTED |
| [010](docs/decisions/ADR-010-active-liveness.md) | Liveness ativo próprio (desafio sorteado + quadros + mesma pessoa da selfie) | ACCEPTED; parâmetros PENDING CALIBRATION |

Plano de evolução: [docs/biometric-architecture-plan.md](docs/biometric-architecture-plan.md).
Avaliação do motor no serviço: [LFW](docs/evaluation-lfw.md).
Avaliação do motor biométrico (POC): [engine](docs/biometric-engine-evaluation.md),
[liveness](docs/liveness-evaluation.md), [resultados do POC](docs/biometric-poc-results.md)
e o POC isolado em [`poc/`](poc/README.md).
