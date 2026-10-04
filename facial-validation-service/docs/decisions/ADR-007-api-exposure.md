# ADR-007 — O que a API expõe do resultado biométrico

- **Status:** ACCEPTED
- **Data:** 2026-10-01
- **Resolve:** a decisão sobre expor score e threshold (plano §1, "PENDING BUSINESS
  DECISION") e a consulta do cadastro (ADR-002, PENDING DECISION)

## Contexto

O consumidor precisa saber o resultado do cadastro e, quando a captura é
recusada, o que corrigir ("foto escura, tente de novo"). Por outro lado, cada
número devolvido é feedback para quem tenta fraudar: com o score de
similaridade, um atacante ajusta a imagem e acompanha o número subir
(*hill-climbing*); com as medidas de qualidade, ajusta a foto até ficar logo
acima do limite.

## Decisão

1. **Score e threshold não são expostos.** A validação devolve só `decision`,
   `reason` e `policy_version`. O `similarity` continua gravado em
   `verifications`, para auditoria e calibração, e nunca sai pela API.
2. **Qualidade sai só como códigos.** O campo `quality` traz
   `{passed, issues}`, com os códigos do `QualityGate` (`BLURRY`, `TOO_DARK`,
   `MULTIPLE_FACES`...). Nitidez, brilho e tamanho do rosto não são expostos.
   `quality` é nulo quando a qualidade não chegou a ser avaliada.
3. **Consulta do cadastro por dois caminhos:**
   - `GET /api/v1/face-registrations/{registration_id}`: o resultado daquele
     envio;
   - `GET /api/v1/subjects/{subject_id}/face`: `enrolled` (há referência
     ativa?), `active_registration_id` e o envio mais recente.

   Os dois seguem o escopo por tenant: recurso de outro tenant responde 404.

Os códigos são gravados em `face_registrations.quality_issues` e
`verifications.quality_issues` (JSONB, migration `0002`). Registros anteriores
ficam com NULL; para eles, os códigos só existem nos eventos de auditoria.

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| Expor o score por tenant (flag) | Nenhum consumidor precisa dele hoje. Pode ser revisto quando aparecer um caso concreto (ex.: auditoria interna), com ADR próprio |
| Expor o score sempre | Feedback numérico direto para ataque |
| Códigos + medidas de qualidade | As medidas facilitam o ajuste fino contra o gate; os códigos bastam para a UX |
| Só o `reason` geral | Com `LOW_QUALITY`, o consumidor não sabe o que corrigir |

## Consequências

- Um teste (`test_openapi_never_documents_score_or_measurements`) falha se
  algum schema da API documentar `similarity`, `score`, `threshold`,
  `sharpness`, `brightness` ou `template`.
- Mudanças dentro de `/api/v1` são só aditivas. Ver [docs/api.md](../api.md).
- Continuam pendentes: webhook de resultado e recadastro, que hoje responde
  409 (ADR-002).
