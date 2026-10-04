# ADR-005 — Processamento assíncrono

- **Status:** ACCEPTED para o MVP
- **Data:** 2026-09-30

## Contexto

Detecção, liveness e embedding podem levar segundos e depender de CPU/GPU ou
de um fornecedor externo. A API não deve segurar a conexão do consumidor.

```
API -> PostgreSQL (CREATED) -> Redis (job) -> Worker -> BiometricProvider -> PostgreSQL -> consulta (GET)
```

## Alternativas avaliadas

| Opção | Prós | Contras |
|---|---|---|
| Celery | Maduro, muitos recursos | Pesado para 2 tarefas; modelo síncrono; mais configuração |
| RQ | Simples | Síncrono; o código da aplicação é async |
| Dramatiq / Taskiq | Bons recursos | Mais peças do que o MVP precisa |
| Fila em PostgreSQL (`SKIP LOCKED`) | Sem Redis | Implementação própria; Redis já faz parte da stack |
| **arq** | Asyncio nativo, pequeno, usa o Redis já previsto, deduplicação por `_job_id`, retries e timeout | Projeto em modo de manutenção |

## Decisão

**arq** atrás da porta `TaskQueue`. Um worker (`arq app.worker.WorkerSettings`)
executa `process_face_registration` e `process_verification`. Os casos de uso
são idempotentes: só processam itens em `CREATED`, então reentregas não
duplicam trabalho. Escalar = mais réplicas do worker.

O risco do arq estar em manutenção é aceito porque a fila está isolada pela
porta: trocar por outro executor não afeta domínio nem casos de uso.

## PENDING DECISION

- ~~Webhook de resultado para os consumidores~~: resolvido no ADR-009, que usa
  outbox transacional e varredura por cron do arq.
- Outbox transacional para o **processamento** (hoje o enqueue ocorre após o
  commit; se o Redis falhar nesse instante o item fica em `CREATED` e precisa de
  reprocessamento). O padrão do ADR-009 (outbox + varredura) serve de modelo.
- Reprocessamento/varredura de itens presos em `CREATED`/`PROCESSING`.
- Filas separadas por prioridade/tenant, se necessário.
