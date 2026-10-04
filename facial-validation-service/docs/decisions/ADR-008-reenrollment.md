# ADR-008 — Recadastro (substituição da referência biométrica)

- **Status:** ACCEPTED
- **Data:** 2026-10-01
- **Resolve:** "Recadastro/substituição da referência biométrica (hoje retorna
  409)" (ADR-002, PENDING DECISION)

## Contexto

Cada subject tem no máximo uma referência ativa (cadastro `APPROVED`). Até
aqui, um novo cadastro de quem já tinha referência era recusado com 409, e não
havia como trocá-la. A troca é necessária quando o modelo biométrico muda
(templates de outro modelo resultam em `ERROR/MODEL_MISMATCH`), quando a
aparência mudou ou quando o primeiro cadastro foi feito com a pessoa errada.

Ao mesmo tempo, trocar o rosto de alguém é a operação mais sensível do serviço:
quem consegue fazer isso assume a identidade do subject.

## Decisão

Decisões tomadas pelo usuário em 2026-10-01:

1. **Endpoint explícito:** `PUT /api/v1/subjects/{subject_id}/face`. O `POST`
   continua respondendo 409 para quem já tem referência, e a mensagem indica o
   `PUT`. Assim ninguém troca a referência sem querer, e a substituição fica
   explícita na auditoria.
2. **O serviço confia no sistema consumidor:** o novo rosto **não** é
   comparado com a referência atual. Quem decide se o recadastro é legítimo é
   o consumidor (por exemplo, depois de atendimento ou de validação de
   documento). Essa escolha mantém o recadastro possível justamente nos casos
   em que ele é necessário: troca de modelo, cadastro errado, mudança de
   aparência.
3. **O template substituído é apagado** (minimização, LGPD). O registro antigo
   continua `APPROVED`, como histórico, com `superseded_at` e
   `superseded_by_id` preenchidos e sem biometria.

Regras que valem independentemente dessas escolhas:

- O `PUT` exige um subject conhecido (senão 404) com referência ativa (senão
  409 `SUBJECT_NOT_ENROLLED`). O primeiro cadastro é sempre pelo `POST`.
- O novo envio passa pelo mesmo pipeline do cadastro: qualidade, liveness e
  embedding.
- **A referência atual só sai quando a nova é aprovada.** Se o novo envio for
  `REJECTED` ou `ERROR`, nada muda.
- A troca acontece numa única transação do worker. Primeiro a referência atual
  é aposentada (template apagado, evento `FACE_REGISTRATION_SUPERSEDED`);
  depois a nova é aprovada.
- Com recadastros concorrentes, vale o último aprovado. Com dois cadastros
  iniciais (`POST`) concorrentes, o segundo termina em `ERROR`, como antes.
- Uma validação processada depois da troca compara contra a referência nova.

## Garantias no banco (migration `0003`)

- O índice único de referência ativa passa a valer só para
  `status = 'APPROVED' AND superseded_at IS NULL`.
- `ck_face_registrations_approved_has_template`: um `APPROVED` ativo exige
  template; um `APPROVED` substituído exige template **nulo**. Apagar a
  biometria não depende só do código.
- `ck_face_registrations_superseded_consistent`: `superseded_at` e
  `superseded_by_id` andam juntos, e só existem em `APPROVED`.
- O `downgrade` só funciona enquanto nenhum recadastro tiver sido concluído:
  referências substituídas, sem template, quebrariam a regra antiga.

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| `POST` com `replace=true` | A intenção fica num parâmetro fácil de esquecer ou de mandar por engano |
| `POST` sempre substitui | Uma chamada acidental, ou um retry mal feito, troca o rosto do subject |
| Exigir match com a referência antiga | Impede o recadastro exatamente quando ele é necessário (`MODEL_MISMATCH`, cadastro errado) e depende de uma política calibrada que ainda não existe |
| Configurável por tenant | Dois caminhos para manter e testar, sem um caso concreto que justifique |
| Manter o template antigo | Guardaria biometria sem uso, enquanto o prazo de retenção ainda é PENDING LEGAL |

## Consequências

- **Risco aceito:** um consumidor comprometido, ou com um fluxo de recadastro
  fraco, consegue trocar o rosto de um subject. A mitigação está no consumidor
  e na auditoria: todos os eventos registram qual referência substituiu qual e
  quando.
- **Recadastro errado não se desfaz:** o template antigo foi apagado, então a
  correção é um novo recadastro.
- A resposta do cadastro ganhou `replaces_registration_id`, `superseded_at` e
  `superseded_by_id` (mudança aditiva, ADR-007).
