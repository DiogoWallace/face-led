# ADR-002 — Arquitetura

- **Status:** ACCEPTED (itens de segurança marcados como PENDING DECISION)
- **Data:** 2026-09-30

## Decisão

Clean Architecture + Hexagonal (ports & adapters), com DDD tático onde agrega valor:

```
interfaces/http  ->  application (use cases, DTOs, ports)  ->  domain
infrastructure   --implementa-->  ports / repositories
container.py     =  composition root (único lugar que conhece implementações)
```

- **domain**: entidades com ciclo de vida explícito (`ProcessStatus`), value objects
  biométricos opacos, `FaceMatchPolicy` versionada, contratos de repositório.
  Não importa FastAPI, SQLAlchemy, boto3, Redis ou qualquer lib biométrica.
- **application**: casos de uso (`RegisterFace`, `ProcessFaceRegistration`,
  `CreateVerification`, `ProcessVerification`, `GetVerification`,
  `AuthenticateTenant`) e portas (`BiometricProvider`, `CaptureStorage`,
  `TaskQueue`, `TemplateCipher`, `Clock`).
- **infrastructure**: PostgreSQL, S3, arq/Redis, adapter biométrico, AES-GCM,
  logging/tracing/métricas.
- **interfaces/http**: rotas, schemas, dependências e mapeamento de erros.

### Adaptações em relação à estrutura sugerida

| Sugerido | Adotado | Motivo |
|---|---|---|
| `interfaces/http/controllers/` | não criado | Em FastAPI a função da rota já é o controller; uma camada extra só repassaria chamadas. A conversão de exceções fica em `interfaces/http/errors.py`. |
| — | `infrastructure/security/` | Criptografia do template não se encaixa em database/storage. |
| — | `app/container.py` | Composition root explícito, facilita testes com dublês. |
| — | `requirements/` | Locks com hash (ADR-001). |

### Modelo

`Tenant` 1—N `Subject` 1—N `FaceRegistration` (no máximo uma `APPROVED`,
garantido por índice único parcial) e 1—N `Verification`. `LivenessSession`
registra cada resultado de prova de vida; `VerificationEvent` é a trilha de
auditoria (sem dados biométricos).

`Subject.external_id` é o id do sistema consumidor e pode ser dado pessoal:
não é usado em chaves do storage nem em logs.

### Estados

`CREATED -> PROCESSING -> APPROVED | REJECTED | ERROR`, e `EXPIRED` para
validações não processadas dentro do TTL. **REJECTED** = processo concluído e a
pessoa não passou; **ERROR** = o sistema não conseguiu decidir. A API expõe
`decision` = `APPROVED | REJECTED | ERROR` (EXPIRED → ERROR).

### Segurança adotada nesta fundação

- Autenticação sistema-a-sistema provisória por API key por tenant
  (`X-API-Key`, só o SHA-256 é persistido; CLI `create-tenant`).
- Isolamento por tenant em todas as consultas; recurso de outro tenant → 404.
- `Idempotency-Key` obrigatória na criação de validações (única por tenant).
- Template cifrado com AES-256-GCM, AAD = tenant/subject/registration.
- Logs JSON com redação de campos sensíveis; nunca corpo de requisição.
- Erros de validação não ecoam a entrada; score de similaridade não é exposto.
- Bucket privado; URLs temporárias via `CaptureStorage.temporary_url`.
- Portas publicadas só em `127.0.0.1`; containers com `no-new-privileges`;
  processo da aplicação sem root.

### PENDING DECISION

- Mecanismo definitivo de autenticação entre sistemas (mTLS, OAuth2 client
  credentials, API key com rotação/escopos).
- Rate limiting (por tenant, provavelmente em Redis ou no gateway).
- TLS: terminação no gateway/ingress ou no serviço.
- ~~Webhook do resultado~~: resolvido no ADR-009. A consulta do cadastro foi
  resolvida no ADR-007 (`GET /face-registrations/{id}` e `GET /subjects/{id}/face`).
- ~~Recadastro/substituição da referência biométrica~~ — resolvido no ADR-008
  (`PUT /subjects/{id}/face`).
- Outbox para garantir o enfileiramento após o commit.
