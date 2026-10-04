# Contrato da API — v1

Base: `/api/v1`. Autenticação: header `X-API-Key: fvs_...` (uma chave por tenant).
Esquema completo e exemplos: `/docs` (OpenAPI), fora de produção. O que a API
expõe e por quê: [ADR-007](decisions/ADR-007-api-exposure.md).

## Compatibilidade

Dentro de `/api/v1`, mudanças são **aditivas**: endpoints novos, campos novos
nas respostas e códigos novos de `reason` ou `quality.issues`. O consumidor deve
ignorar campos que não conhece e tratar códigos desconhecidos como genéricos.
Remover ou renomear campo, mudar tipo ou semântica exige `/api/v2`.

## Endpoints

| Método | Rota | Resposta |
|---|---|---|
| `POST` | `/subjects/{subject_id}/face` | 202 `{registration_id, subject_id, status}`, com multipart `image`. Primeiro cadastro; 409 se já houver referência ativa |
| `PUT` | `/subjects/{subject_id}/face` | 202, mesma resposta. **Recadastro**: substitui a referência ativa se o novo envio for aprovado ([ADR-008](decisions/ADR-008-reenrollment.md)). 404 para subject desconhecido; 409 `SUBJECT_NOT_ENROLLED` sem referência ativa |
| `GET` | `/face-registrations/{registration_id}` | 200 resultado do envio de cadastro |
| `GET` | `/subjects/{subject_id}/face` | 200 `{subject_id, enrolled, active_registration_id, latest_registration}` |
| `POST` | `/subjects/{subject_id}/verifications` | 202 resultado da validação (em andamento) — multipart `image` + `Idempotency-Key` |
| `GET` | `/verifications/{verification_id}` | 200 resultado da validação |
| `POST` | `/liveness-sessions` | 201 sessão de prova de vida: `{session_id, subject_id, purpose, challenge, instructions, expires_at, frames}` ([ADR-010](decisions/ADR-010-active-liveness.md)) |

`subject_id` é o identificador do sistema consumidor (`[A-Za-z0-9._:-]`, até
128 caracteres).

## Resultado (cadastro e validação)

```json
{
  "status": "REJECTED",
  "decision": "REJECTED",
  "reason": "LOW_QUALITY",
  "quality": { "passed": false, "issues": ["BLURRY"] }
}
```

- `status`: `CREATED`, `PROCESSING`, `APPROVED`, `REJECTED`, `ERROR`, `EXPIRED`.
- `decision`: `APPROVED`, `REJECTED` ou `ERROR` (inconclusivo; `EXPIRED`
  também vira `ERROR`). Nulo enquanto o processamento não termina.
- `reason`: o motivo de `REJECTED` ou `ERROR` (tabela em
  [architecture.md](architecture.md#códigos-de-motivo)).
- `quality`: nulo quando a qualidade não foi avaliada (em andamento, ou erro
  antes do gate). `issues` vazio significa que a captura passou.
- Validação: inclui também `policy_version` e `expires_at`.
- Cadastro: inclui também `replaces_registration_id` (preenchido num
  recadastro), `superseded_at` e `superseded_by_id` (preenchidos quando a
  referência foi substituída; o registro continua `APPROVED`, como histórico,
  sem biometria).

### Recadastro

A referência atual **só sai se o novo envio for aprovado**. Se ele for
recusado (`REJECTED`) ou terminar em erro (`ERROR`), a atual continua valendo.
Para conferir, consulte `GET /subjects/{id}/face`: o `active_registration_id`
muda quando a troca acontece. O serviço não compara o novo rosto com o antigo,
então quem decide se o recadastro é legítimo é o sistema consumidor.

### Códigos de `quality.issues`

| Código | O que o usuário deve fazer |
|---|---|
| `NO_FACE` | Enquadrar o rosto |
| `MULTIPLE_FACES` | Ficar sozinho na foto |
| `FACE_TOO_SMALL`, `FACE_TOO_SMALL_RELATIVE` | Aproximar o rosto da câmera |
| `BLURRY` | Segurar firme e focar |
| `TOO_DARK` / `TOO_BRIGHT` | Melhorar a iluminação / evitar luz direta |
| `SCORE_BELOW_MINIMUM:<medidor>` | Medidor adicional (reservado; nenhum ativo) |

## Prova de vida (liveness ativo)

[ADR-010](decisions/ADR-010-active-liveness.md). Só tem efeito com
`LIVENESS_PROVIDER=active`.

1. `POST /api/v1/liveness-sessions` com `{"subject_id": "...", "purpose":
   "REGISTRATION" | "VERIFICATION"}` (`REGISTRATION` vale para cadastro e
   recadastro). A resposta traz os passos sorteados (`challenge`:
   `TURN_LEFT`, `TURN_RIGHT`, `MOVE_CLOSER`; esquerda **da pessoa**), textos
   sugeridos (`instructions`), `expires_at` e os limites de `frames`
   (`min`, `max`, `max_bytes_each`, `content_types`).
2. Comece com o rosto de frente, execute os passos na ordem, voltando ao
   centro entre eles, e capture quadros JPEG/PNG **sem espelhar**, em ordem,
   ~4–5 por segundo.
3. Envie no mesmo multipart do cadastro, recadastro ou validação: `image`
   (selfie), `liveness_session_id` e `frames` (repetido, um arquivo por quadro).

A sessão vale **uma vez**, até `expires_at`, para o mesmo `subject_id` e a
mesma finalidade. Liveness reprovado → `REJECTED/LIVENESS_FAILED`; sem decisão
possível → `ERROR/LIVENESS_INCONCLUSIVE`. O motivo detalhado não sai pela API.
Referência de front em `APP_ENV=local`: `http://127.0.0.1:18100/dev/liveness`.

## O que nunca sai pela API

Score de similaridade, threshold, medidas de qualidade (nitidez, brilho,
tamanho do rosto), template ou embedding, modelo biométrico, resultado bruto de
liveness e a captura. Dados de outro tenant respondem 404.

## Webhook de resultado

Opcional, por tenant, configurado pelo operador
([ADR-009](decisions/ADR-009-result-webhook.md)):

```bash
python -m app.cli set-webhook --slug meu-sistema --url https://meu-sistema.example/webhooks/fvs
# imprime webhook_secret=whsec_... UMA vez; guarde-o
python -m app.cli set-webhook --slug meu-sistema --url ... --rotate-secret   # novo segredo
python -m app.cli disable-webhook --slug meu-sistema
```

Quando um cadastro ou uma validação termina (`APPROVED`, `REJECTED`, `ERROR`
ou `EXPIRED`), o serviço faz um `POST` na URL:

```json
{
  "id": "6f1c…",                          // id do evento: o mesmo em todas as tentativas
  "type": "verification.completed",        // ou face_registration.completed
  "created_at": "2026-10-01T12:00:00+00:00",
  "data": { "...": "o mesmo JSON do GET do recurso" }
}
```

Cabeçalhos: `X-FVS-Event-Id`, `X-FVS-Event-Type` e
`X-FVS-Signature: t=<unix>,v1=<hex>`.

**Como o consumidor valida o webhook.** Use o corpo cru, antes de qualquer
parse:

```python
import hashlib, hmac, time

def valido(corpo: bytes, assinatura: str, segredo: str, tolerancia=300) -> bool:
    partes = dict(p.split("=", 1) for p in assinatura.split(","))
    esperado = hmac.new(segredo.encode(), f"{partes['t']}.".encode() + corpo,
                        hashlib.sha256).hexdigest()
    recente = abs(time.time() - int(partes["t"])) <= tolerancia
    return recente and hmac.compare_digest(esperado, partes["v1"])
```

- **Resposta:** responda `2xx` rápido (o tempo limite é 10 s) e processe
  depois. Qualquer outra resposta, timeout ou redirect conta como falha.
- **Entrega "pelo menos uma vez":** deduplique pelo `id`. A ordem entre
  eventos não é garantida.
- **Novas tentativas:** 9 no total, com esperas de 1 min, 5 min, 30 min, 1 h,
  2 h, 4 h, 8 h e 8 h (~24 h). Depois disso a entrega é abandonada, e o GET
  continua sendo a fonte da verdade.
- **Corpo:** é o estado do recurso **no momento do envio**. Um cadastro que
  foi substituído depois aparece com `superseded_at`.

## Erros

`{"error": {"code": "...", "message": "..."}}`. Os principais:

- `401 UNAUTHENTICATED`;
- `404 SUBJECT_NOT_FOUND`, `FACE_REGISTRATION_NOT_FOUND`, `VERIFICATION_NOT_FOUND`,
  `LIVENESS_SESSION_NOT_FOUND`;
- `409 SUBJECT_ALREADY_ENROLLED`, `SUBJECT_NOT_ENROLLED`, `IDEMPOTENCY_CONFLICT`,
  `LIVENESS_SESSION_INVALID` (usada, expirada, de outro subject ou finalidade);
- `422 INVALID_CAPTURE`, `VALIDATION_ERROR`, `LIVENESS_EVIDENCE_REQUIRED`
  (liveness ativo exigido e envio sem sessão e quadros);
- `503 DEPENDENCY_UNAVAILABLE`.

As mensagens de validação não ecoam a entrada.
