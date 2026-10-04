# Arquitetura

Resumo das decisões; os detalhes e alternativas estão em [`decisions/`](decisions/).

## Visão geral

```
                 ┌──────────────── facial-validation-network ────────────────┐
 Sistema         │                                                           │
 consumidor ──►  │  api (FastAPI) ──► PostgreSQL        worker (arq)          │
 X-API-Key       │     │   └────────► S3 (capturas)       │  ▲                │
                 │     └──── job ───► Redis ──────────────┘  │                │
                 │           detector · quality · embedder · comparator ·     │
                 │           liveness (portas independentes, ADR-006)         │
                 └───────────────────────────────────────────────────────────┘
```

## Camadas

| Pasta | Responsabilidade | Pode importar |
|---|---|---|
| `app/domain` | Entidades, estados, value objects, `FaceMatchPolicy`, `QualityGate`, `LivenessRequirement`, contratos de repositório, exceções | só stdlib |
| `app/application` | Casos de uso, `FaceAnalysisPipeline`, DTOs, portas (incl. `ports/biometric.py`) | domain |
| `app/infrastructure` | PostgreSQL, S3, Redis/arq, adapter biométrico, criptografia, observabilidade | application, domain, libs |
| `app/interfaces/http` | Rotas, schemas, dependências, erros | application, domain, container |
| `app/container.py` | Composition root | tudo |

## Componentes biométricos

Ver [ADR-006](decisions/ADR-006-biometric-components.md) e o
[plano por fases](biometric-architecture-plan.md).

```text
FaceDetector → QualityGate(rostos) → FaceQualityAssessor → QualityGate(critérios)
→ LivenessProvider (se REQUIRED) → FaceEmbedder → [validação] FaceComparator → FaceMatchPolicy
```

Adapters disponíveis (`BIOMETRIC_*`): `none` em todas as portas; `opencv-yunet`
(detector), `opencv` (medidor de qualidade: nitidez e brilho, sem modelo),
`opencv-sface` (embedder e comparator) e `active` (liveness ativo próprio,
[ADR-010](decisions/ADR-010-active-liveness.md)). Os adapters OpenCV rodam só
no worker, num executor com uma instância de cada modelo por thread
(`infrastructure/biometric/opencv/runtime.py`).

Os thresholds de qualidade e de match vêm de configuração. Sem valor, o
critério fica PENDING CALIBRATION. Fora de local/test, o serviço recusa subir
com política não calibrada, liveness desligado ou quality gate incompleto.

## Fluxos

**Cadastro** — `POST /api/v1/subjects/{subject_id}/face`
1. Autentica o tenant, valida a captura (tipo, assinatura, tamanho).
2. Cria o `Subject` se necessário; recusa (409) se já houver referência ativa.
3. Grava a captura no S3, cria `FaceRegistration` em `CREATED`, registra evento, enfileira.
4. Worker: detecção → qualidade → liveness → embedding → template cifrado → `APPROVED`.
   Falhas de negócio → `REJECTED`; falhas do sistema → `ERROR`.

**Validação** — `POST /api/v1/subjects/{subject_id}/verifications` (+ `Idempotency-Key`)
1. Autentica, valida captura, exige referência ativa (409 se não houver).
2. Reenvio com a mesma chave devolve a mesma validação (`Idempotent-Replayed: true`).
3. Worker: expiração → política ativa → pipeline → busca referência → compara
   → `FaceMatchPolicy.decide` → `APPROVED` / `REJECTED (FACE_MISMATCH)`.

**Recadastro** — `PUT /api/v1/subjects/{subject_id}/face` ([ADR-008](decisions/ADR-008-reenrollment.md))
1. Exige subject com referência ativa (404 / 409 `SUBJECT_NOT_ENROLLED`). Sem comparação com o rosto
   antigo: o consumidor decide se é legítimo.
2. Worker: mesmo pipeline do cadastro. Aprovado → na mesma transação, a referência atual é
   substituída (template apagado, `FACE_REGISTRATION_SUPERSEDED`) e a nova vira ativa. Recusado ou
   erro → a atual continua valendo.

**Consulta** — `GET /api/v1/verifications/{verification_id}`,
`GET /api/v1/face-registrations/{registration_id}` e
`GET /api/v1/subjects/{subject_id}/face` devolvem `status`, `decision`
(`APPROVED`/`REJECTED`/`ERROR`), `reason` e `quality` (`{passed, issues}`, só
códigos). O score e as medidas de qualidade não são expostos
([ADR-007](decisions/ADR-007-api-exposure.md)). Contrato completo em
[api.md](api.md).

**Prova de vida** — `POST /api/v1/liveness-sessions` ([ADR-010](decisions/ADR-010-active-liveness.md))
1. O servidor sorteia os passos e grava a sessão (uso único, com prazo, amarrada a tenant + subject
   + finalidade).
2. O cadastro/validação traz `liveness_session_id` + `frames`; na mesma transação a sessão é
   consumida e os quadros vão para o storage.
3. Worker: YuNet em cada quadro → regra de domínio `verify_challenge` (passos na ordem, início
   frontal, continuidade, sem assinatura de foto plana) → SFace confere que os quadros-prova são a
   mesma pessoa da selfie.

## Webhook de resultado

[ADR-009](decisions/ADR-009-result-webhook.md). Ao gravar um resultado terminal,
o worker grava também, **na mesma transação**, uma linha em `webhook_deliveries`
(outbox), se o tenant tiver webhook. Depois do commit, enfileira o atalho
`deliver_webhooks`. Uma varredura a cada 30 s (`sweep_webhooks`, cron do arq)
cobre falhas da fila e as novas tentativas. A entrega é reservada com
`SKIP LOCKED`; o corpo é montado na hora, pelo mesmo serializador do GET, e
assinado com HMAC-SHA256.

```text
resultado + outbox (1 transação) → atalho na fila ┐
                                  varredura 30 s ─┴→ claim (SKIP LOCKED) → POST assinado
                                                     2xx → DELIVERED · falha → backoff · 9ª → FAILED
```

## Retenção da captura

`CAPTURE_RETENTION` (PENDING LEGAL/BUSINESS DECISION): `KEEP` mantém a selfie
no storage, como sempre foi; `DELETE_AFTER_PROCESSING` apaga a captura assim que
o resultado é gravado (qualquer estado terminal), remove a chave do registro e
grava o evento `CAPTURE_DELETED`. Se o storage falhar, a chave é mantida e o
resultado não muda. O template cifrado do cadastro não é afetado. Os quadros
do liveness ativo seguem a mesma regra e são apagados junto com a captura.

## Códigos de motivo

| Estado | `reason` |
|---|---|
| REJECTED | `NO_FACE`, `MULTIPLE_FACES`, `LOW_QUALITY`, `LIVENESS_FAILED`, `FACE_MISMATCH` |
| ERROR | `PROVIDER_NOT_CONFIGURED`, `PROVIDER_FAILURE`, `LIVENESS_INCONCLUSIVE`, `POLICY_NOT_CONFIGURED`, `MODEL_MISMATCH`, `REFERENCE_NOT_FOUND`, `CAPTURE_UNAVAILABLE`, `INTERNAL_ERROR` |
| EXPIRED | `EXPIRED` |

## Observabilidade

- **Logs estruturados**: JSON em stdout, `request_id` (aceita `X-Request-ID`),
  redação de chaves sensíveis (`app/infrastructure/observability/logging.py`).
  O log HTTP registra apenas método, rota, status e duração.
- **Health checks**: `/health` (processo) e `/ready` (PostgreSQL, Redis, S3).
- **OpenTelemetry**: `configure_tracing` desligado por padrão; ativar com o
  extra `otel` e `OTEL_ENABLED=true`.
- **Métricas**: porta `Metrics` com implementação no-op; exportador é pendente.

## Segurança

Ver ADR-002 para o que já está implementado e o que está pendente. Regra fixa:
nunca registrar selfie, vídeo, documento, embedding, tokens, API keys ou segredos.
