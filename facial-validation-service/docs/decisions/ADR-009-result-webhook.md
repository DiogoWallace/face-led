# ADR-009 — Webhook de resultado

- **Status:** ACCEPTED
- **Data:** 2026-10-01
- **Resolve:** "Webhook de resultado para os consumidores" (ADR-002, ADR-005,
  PENDING DECISION). Implementa um outbox transacional, que o ADR-005 deixou
  pendente, só para o webhook.

## Contexto

O cadastro e a validação são assíncronos (202), e até agora o consumidor tinha
de consultar o GET até o resultado sair. Avisar o consumidor obriga o serviço a
fazer requisições para fora, e isso traz quatro questões: para onde (SSRF), o
que mandar (dados pessoais), como provar a origem (assinatura) e o que fazer
quando o consumidor está fora do ar.

## Decisão

Decisões do usuário (2026-10-01):

1. **A URL é configurada por tenant, pela CLI, pelo operador:**
   `python -m app.cli set-webhook --slug X --url https://...`. Quem chama a
   API não escolhe para onde o serviço faz requisições, o que fecha o caminho
   de SSRF. Fora de `local`/`test`, a URL precisa ser `https`. URLs com
   credenciais ou com fragmento são recusadas.
2. **O corpo é o resultado igual ao GET** (as regras do ADR-007 valem: sem
   score, qualidade só em códigos), dentro de um envelope `{id, type,
   created_at, data}`. O GET e o webhook usam o mesmo serializador
   (`interfaces/http/serializers.py`), e um teste compara os dois byte a byte.
3. **Novas tentativas com backoff por cerca de 24 h:** 9 tentativas, com
   esperas de 1 min, 5 min, 30 min, 1 h, 2 h, 4 h, 8 h e 8 h (~23,8 h no total).
   Esgotadas as tentativas, a entrega fica `FAILED`, e o resultado continua
   disponível por GET.

Como o serviço implementa isso:

- **Outbox:** a linha de `webhook_deliveries` é gravada na mesma transação que
  grava o resultado. Ela guarda só o tipo do evento e o id do recurso, e
  nenhum dado pessoal fica parado ali. O corpo é montado na hora do envio,
  com o estado atual do recurso, igual ao GET.
- **Disparo:** depois do commit, o worker enfileira um atalho, o job
  `deliver_webhooks`. Uma varredura periódica (`sweep_webhooks`, a cada 30 s)
  entrega o que o atalho perdeu, por exemplo se o Redis caiu, e faz as novas
  tentativas.
- **Concorrência:** cada entrega é reservada com `SELECT ... FOR UPDATE SKIP
  LOCKED` por 2 minutos (`claim`). Duas varreduras nunca pegam a mesma linha.
  Se o worker morrer no meio do envio, a entrega volta a ficar disponível
  quando a reserva vence.
- **Semântica "pelo menos uma vez":** o `id` do evento (header
  `X-FVS-Event-Id`) é o mesmo em todas as tentativas, e o consumidor
  deduplica por ele. A ordem entre eventos não é garantida.
- **Assinatura:** `X-FVS-Signature: t=<unix>,v1=<hex(HMAC_SHA256(segredo,
  "{t}.{corpo}"))>`. O timestamp é renovado a cada tentativa, e o consumidor
  recusa assinaturas velhas (sugestão: 5 min).
- **Segredo:** `whsec_` seguido de 32 bytes aleatórios. É mostrado uma vez na
  CLI e guardado **cifrado** com AES-GCM, com o `tenant_id` como dado
  associado. Trocar a URL mantém o segredo; `--rotate-secret` gera outro. Uma
  constraint no banco exige URL e segredo juntos.
- **Envio:** sem seguir redirect, que poderia levar a um destino que o operador
  não configurou, e com timeout de `WEBHOOK_TIMEOUT_SECONDS` (padrão 10 s).
  Só respostas 2xx contam como entregues. Da resposta, o serviço guarda o
  status HTTP e mais nada; o log também não registra a URL.
- **Webhook desligado** (`disable-webhook`): as entregas pendentes são
  encerradas como `FAILED/WEBHOOK_DISABLED`.
- **Eventos:** `face_registration.completed` e `verification.completed`, para
  qualquer estado terminal (`APPROVED`, `REJECTED`, `ERROR`, `EXPIRED`).

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| `callback_url` em cada requisição | Qualquer chamador com API key escolheria o destino (SSRF); exigiria allowlist e bloqueio de IPs internos |
| URL por tenant + `callback_url` em allowlist | Mais peças sem um caso concreto que justifique |
| Só um aviso (id e status) no corpo | Obriga uma chamada extra por evento; o usuário preferiu o resultado completo |
| Poucas tentativas, ou uma só | Não cobre uma indisponibilidade de um dia do consumidor |
| Enfileirar o envio direto, sem outbox | Se o Redis falhar logo depois do commit, o aviso se perde sem rastro |

## Consequências

- **Exposição se a URL estiver errada:** o destino recebe o resultado e o
  `subject_id`, que pode ser dado pessoal. A URL é responsabilidade do
  operador e o corpo é assinado, mas a assinatura não protege a
  confidencialidade. Por isso `https` é obrigatório fora de local.
- **Dependência nova no runtime:** `httpx`.
- **Pendências:**
  - reenvio manual de uma entrega `FAILED`;
  - consulta das entregas pela API;
  - métricas de entrega (a porta de métricas ainda é no-op);
  - outbox também para o enfileiramento do processamento (ADR-005).
