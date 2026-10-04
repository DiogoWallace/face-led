# ADR-010 — Liveness ativo próprio

- **Status:** ACCEPTED (mecanismo). Os parâmetros estão **PENDING CALIBRATION**,
  e por isso o provider só roda em `APP_ENV=local|test`.
- **Data:** 2026-10-01
- **Resolve:** "Fornecedor SaaS (AWS/Azure) × ativo próprio" (plano §9,
  `docs/liveness-evaluation.md`, PENDING DECISION), pela opção **(b)**.

## Contexto

Reconhecimento 1:1 não responde se há uma pessoa real diante da câmera. A
avaliação (`docs/liveness-evaluation.md`) descartou o MiniFASNet como PAD único
e deixou duas opções: (a) PAD certificado de fornecedor (AWS/Azure), que leva
biometria a terceiro e exige SDK no front; (b) liveness ativo próprio, com
desafio sorteado pelo servidor.

## Decisão

Decisão do usuário: **liveness ativo próprio, sem nenhuma API externa** (nada
de AWS, Azure ou outro fornecedor), com desafios criados pelo próprio serviço,
atrás da porta `LivenessProvider` (`LIVENESS_PROVIDER=active`, nome
`active-challenge/v1`). **Objetivo atual: teste local.** Por isso os
parâmetros ficam PENDING CALIBRATION e o guard impede o uso fora de
`APP_ENV=local|test`; levar a produção exige uma nova decisão, depois de
medir APCER/BPCER.

### Fluxo

1. `POST /api/v1/liveness-sessions` `{subject_id, purpose}` → o servidor sorteia
   de 2 a 4 passos (`TURN_LEFT`, `TURN_RIGHT`, `MOVE_CLOSER`; sem repetir em
   seguida, `MOVE_CLOSER` no máximo uma vez, pelo menos um giro) e devolve a
   sessão com prazo (`LIVENESS_SESSION_TTL_SECONDS`, padrão 120 s).
2. O front mostra os passos, captura quadros **sem espelhamento** (~4–5 por
   segundo) e envia `liveness_session_id` + `frames` no mesmo multipart da
   selfie (cadastro, recadastro ou validação).
3. A API valida os quadros e, **na mesma transação** do cadastro/validação,
   marca a sessão como usada e grava os quadros no storage. A sessão é de uso
   único (também no banco: índice único em `liveness_challenge_id`) e
   amarrada a tenant + subject + finalidade (`REGISTRATION` serve para
   cadastro e recadastro; `VERIFICATION` para validação). Sessão de outro
   tenant → 404; usada, expirada, de outro subject ou finalidade → 409.
4. O worker remonta a evidência e o provider decide, na ordem do pipeline
   (gate → liveness → embedding).

### Regra (domínio, `domain/services/active_liveness.py`)

Por quadro, o YuNet dá os landmarks; o domínio mede o **giro** (deslocamento do
nariz em relação ao meio dos olhos, na escala da distância entre olhos) e a
**distância entre olhos**. `verify_challenge` exige:

- quadros suficientes e rosto rastreado em pelo menos 80% deles;
- um início frontal;
- cada passo cumprido **na ordem sorteada**;
- num giro, os olhos não podem "encolher" além de 65% da base (assinatura de
  foto plana inclinada);
- **continuidade**: sem salto de pose ou de escala entre quadros vizinhos, e
  no máximo um quadro sem rastreio seguido.

Depois, o adapter confere com SFace que **cada quadro-prova** (o início e um
por passo) é a **mesma pessoa da selfie**, acima de
`LIVENESS_SAME_PERSON_MIN_SIMILARITY`. Sem isso, alguém faria o desafio com o
próprio rosto e cadastraria a foto de outra pessoa. Esse limiar não tem
padrão: sem ele, o resultado é `INCONCLUSIVE` (→ `ERROR/LIVENESS_INCONCLUSIVE`).

Veredito: `LIVE`, `SPOOF` (→ `REJECTED/LIVENESS_FAILED`) ou `INCONCLUSIVE`
(→ `ERROR`). O porquê (`detail`, ex.: `STEP_2_TURN_LEFT_NOT_DONE`, `POSE_JUMP`,
`SAME_PERSON_MISMATCH`) vai para `liveness_sessions.detail` e para o evento
de auditoria, **nunca para a API** (ADR-007: o motivo detalhado ensinaria o
atacante).

### Guards e contrato

- Fora de `local|test`, `LIVENESS_PROVIDER=active` exige
  `LIVENESS_CALIBRATION_STATUS=CALIBRATED` e
  `LIVENESS_SAME_PERSON_MIN_SIMILARITY`; senão o serviço não sobe.
- Com `active` + `REQUIRED`, a API recusa na entrada (422
  `LIVENESS_EVIDENCE_REQUIRED`) envio sem sessão e quadros.
- Os campos novos são opcionais no contrato (mudança aditiva em `/api/v1`).
- Os quadros são biometria: seguem a retenção da captura
  (`CAPTURE_RETENTION`, ADR-004) e são apagados junto com ela.
- `GET /dev/liveness`: página de demonstração com **captura guiada** (webcam),
  montada só em `APP_ENV=local` e fora do OpenAPI. Cada quadro é medido no
  worker (`POST /dev/liveness/observe` → job `observe_liveness_frame`, mesma
  função `observe` da decisão, mais a caixa do rosto normalizada para desenhar
  a guia; a API não carrega modelo). A página mostra um oval para encaixar o
  rosto (cresce até o alvo no `MOVE_CLOSER`; setas nos giros), só começa a
  gravar com o rosto centralizado, de frente e a uma distância que permita
  aproximar (olhos/largura entre 0,09 e 0,20), só avança
  quando o passo é cumprido, para de gravar no último passo, pede para voltar ao centro e aborta, sem enviar,
  se o passo não vier a tempo. O resultado do job (com o quadro nos argumentos)
  fica no Redis só 10 s. Os limites vêm da mesma configuração do servidor.
  A primeira versão só contava tempo (2,5 s por passo): nas três tentativas
  reais o movimento chegou depois da janela e foi reprovado.

### Parâmetros iniciais (PENDING CALIBRATION)

| Variável | Valor | Papel |
|---|---|---|
| `LIVENESS_CHALLENGE_STEPS` | 3 | passos por sessão |
| `LIVENESS_MIN_FRAMES` / `MAX_FRAMES` | 8 / 120 | quadros aceitos (~5 por segundo na captura guiada) |
| `LIVENESS_FRAME_MAX_BYTES` | 300 000 | por quadro |
| `LIVENESS_MIN_TRACKED_RATIO` | 0,8 | fração com 1 rosto rastreado |
| `LIVENESS_FRONTAL_MAX_YAW` | 0,15 | início frontal |
| `LIVENESS_TURN_MIN_YAW` | 0,35 | giro mínimo |
| `LIVENESS_TURN_MIN_EYE_RATIO` | 0,65 | olhos no giro / base |
| `LIVENESS_CLOSER_MIN_SCALE` | 1,25 | aproximar |
| `LIVENESS_MAX_YAW_JUMP` / `MAX_SCALE_JUMP` | 0,25 / 0,25 | continuidade |

São valores de partida escolhidos na implementação. **Nenhum foi calibrado com
capturas reais de webcam**. Nas primeiras capturas reais (3 tentativas, 1
pessoa, 2026-10-01): giro real chegou a −0,37 a −0,58 com olhos em 0,72–0,77
da base; um salto de 0,24 entre quadros vizinhos (limite 0,25) a ~4 quadros/s —
motivo para capturar a ~5/s. Na primeira aprovação com a captura guiada, a
distância natural foi olhos/largura ≈ 0,165 (rosto ≈ 39% da largura), o
aproximar chegou a 1,28, os giros a −0,38 e +0,49 com olhos em 0,82–0,85 da
base, e cada instrução levou ~2,5–3,5 s de reação (90 quadros usados de 90 —
por isso o limite passou a 120); os testes unitários usam sequências sintéticas de
medidas e provam só que a regra faz o que promete.

## Medição: ataque simulado no LFW

`python -m evaluation.liveness_attacks` (dentro do container `api`, precisa de
`datasets/lfw` e `models/`). O ataque é uma "apresentação de slides": fotos
reais da vítima em várias poses, ordenadas por giro, com zoom para
`MOVE_CLOSER`, entregues como quadros. A selfie é outra foto frontal da vítima.
O limiar de mesma pessoa usado ali é o EER do LFW (0,2778), só para o
experimento.

Resultado em 2026-10-01, 34 pessoas do LFW com ≥ 30 fotos:

| Atacante | Montável | Passou |
|---|---|---|
| Ingênuo (fotos como estão) | 3 | 0 (3 `SCALE_JUMP`) |
| Alinhado (olhos na mesma escala/posição) | 3 | 0 (2 `SCALE_JUMP`, 1 passo não cumprido) |
| **Forte** (alinha, remede e encadeia dentro dos limites) | 3 | **1** |

Controle negativo: no caso que passou, a mesma sequência com a selfie de outra
pessoa deu `SPOOF/SAME_PERSON_MISMATCH`.

Leitura: com fotos públicas comuns o ataque raramente é montável (31 de 34 não
têm poses para um giro contínuo), mas **quando é montável, um atacante que
conhece os limites passa**. Os quadros chegam como arquivos: quem controla o
cliente injeta o que quiser. Os números descrevem esse ataque no LFW; não são
APCER.

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| PAD certificado de fornecedor (AWS/Azure) | Decisão do usuário: nenhuma API externa por enquanto (teste local; evita transferir biometria a terceiro e o SDK do fornecedor). Continua sendo a alternativa se a calibração não atingir o necessário |
| MiniFASNet como PAD único | Alto BPCER no POC (`docs/liveness-evaluation.md`) |
| Vídeo único em vez de quadros | Quadros JPEG reaproveitam a validação de captura existente e não trazem codec para o worker |
| Expor o `detail` na API | Ensina o atacante qual limite ajustar (ADR-007) |

## Consequências

- **Não protege contra injeção digital** (câmera virtual, deepfake em tempo
  real, quadros montados): exige atestação do dispositivo/app. Sem
  certificação ISO/IEC 30107-3; APCER e BPCER **não medidos**.
- **Front obrigatório:** o consumidor precisa de captura guiada (a página
  `/dev/liveness` é a referência). A medição por quadro existe só em local;
  um front de produção precisaria medir no dispositivo ou de um endpoint de
  progresso no contrato — PENDING DECISION.
- **Custo no worker:** detecção em todos os quadros + embedding nos
  quadros-prova, por envio.
- **Pendências:**
  - dataset de PAD autorizado (`real`, `print`, `screen`, `video_replay`) com
    capturas de webcam/celular e medição de APCER/BPCER —
    PENDING LEGAL/BUSINESS DECISION;
  - calibração dos parâmetros e de `LIVENESS_SAME_PERSON_MIN_SIMILARITY` —
    PENDING CALIBRATION;
  - mesmo nível de liveness no cadastro e na validação? — PENDING BUSINESS
    DECISION (hoje `LIVENESS_REQUIREMENT` vale para os dois);
  - expurgo de sessões de desafio nunca usadas (hoje ficam na tabela, sem
    quadros) — PENDING DECISION;
  - atestação de dispositivo contra injeção — PENDING DECISION.
