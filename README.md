# face-led — validação facial com prova de vida acendendo um LED no Arduino

Projeto de ponta a ponta que junta **visão computacional, backend e hardware**:
uma pessoa faz a validação facial com **prova de vida** no navegador, um
microserviço decide se é ela mesma e, quando aprova, um **Arduino Uno acende o
LED** da placa.

```text
 Navegador (webcam)                facial-validation-service                face-led
 ┌─────────────────┐  selfie +     ┌──────────────────────────┐  webhook     ┌──────────────┐  serial   ┌─────────┐
 │ captura guiada  │──quadros────▶ │ API ─▶ fila ─▶ worker    │──HMAC──────▶ │ ponte Python │─────────▶ │ Arduino │
 │ (oval + passos) │               │ YuNet · SFace · liveness │  (outbox)    │ (FastAPI)    │  USB      │ LED 3 s │
 └─────────────────┘               └──────────────────────────┘              └──────────────┘           └─────────┘
```

> **Protótipo.** Funciona e foi testado de ponta a ponta, mas **não está
> calibrado para produção**: o threshold de reconhecimento e os parâmetros da
> prova de vida são valores de partida, e as taxas de erro de ataque
> (APCER/BPCER) não foram medidas. O próprio serviço se recusa a subir assim
> fora de ambiente local ou de teste.

## O que tem aqui

| Pasta | O que é | Stack |
|---|---|---|
| [`facial-validation-service/`](facial-validation-service/) | Microserviço de cadastro facial e **validação 1:1** com prova de vida ativa | Python 3.12 · FastAPI · SQLAlchemy 2 async · PostgreSQL 17 · Redis + arq · S3 (SeaweedFS) · OpenCV (YuNet + SFace) · Docker |
| [`face-led/`](face-led/) | Firmware do Arduino e **ponte** que recebe o webhook do serviço e fala com a placa pela serial | C++ (Arduino, `arduino-cli`) · Python 3.12 · FastAPI · pyserial · uv |

## Destaques técnicos

**Microserviço**
- **Clean/Hexagonal:** domínio e casos de uso não importam OpenCV, banco nem
  framework; um teste de arquitetura falha se isso acontecer. Cada etapa
  biométrica (detector, qualidade, liveness, embedding, comparador) é uma porta
  com adapter próprio ([ADR-006](facial-validation-service/docs/decisions/ADR-006-biometric-components.md)).
- **Nunca uma aprovação silenciosa:** `REJECTED` (a pessoa não passou) é
  diferente de `ERROR` (o sistema não conseguiu decidir). Sem threshold
  configurado, a resposta é `ERROR`, nunca "aprovado".
- **Prova de vida ativa própria, sem API externa:** o servidor sorteia passos
  (virar à esquerda, à direita, aproximar); a regra confere ordem, continuidade
  do movimento e assinatura de foto plana, e confirma com SFace que quem fez o
  desafio é a mesma pessoa da selfie
  ([ADR-010](facial-validation-service/docs/decisions/ADR-010-active-liveness.md)).
- **Webhook confiável:** outbox transacional (o aviso nunca se perde se o Redis
  cair), assinatura HMAC-SHA256 com timestamp e cerca de 24 h de novas
  tentativas ([ADR-009](facial-validation-service/docs/decisions/ADR-009-result-webhook.md)).
- **Privacidade por padrão:** template biométrico cifrado com AES-256-GCM, o
  score nunca sai pela API, logs sem imagem nem dado pessoal, isolamento por
  tenant e retenção configurável das capturas.
- **Avaliação reproduzível no LFW:** EER de 1,47% no protocolo de 10 folds,
  com a mesma suíte medindo FAR/FRR, falha de captura, latência e vazão
  ([evaluation-lfw.md](facial-validation-service/docs/evaluation-lfw.md)).
- **Decisões documentadas:** 10 ADRs, do motor biométrico à prova de vida,
  com alternativas descartadas e pendências marcadas.

**Arduino e ponte**
- **Firmware não bloqueante** (sem `delay()`): um comando novo substitui o
  padrão do LED na hora, e o LED sempre termina apagado
  ([protocolo](face-led/docs/protocol.md)).
- **Ponte como consumidora real do webhook:** confere a assinatura, ignora
  eventos repetidos, responde 503 quando a placa está desconectada (o serviço
  tenta de novo) e sobrevive ao reset do Uno ao abrir a porta serial.
- **Testes sem hardware:** uma porta serial falsa imita o firmware
  (`READY`, `ACK`, `PONG`), cobrindo protocolo, assinatura e reconexão.

## Como funciona uma validação

1. O front pede uma **sessão de prova de vida**; o servidor sorteia os passos.
2. A página guia a pessoa com um oval na tela; cada quadro é medido e o passo
   só avança quando é cumprido.
3. Selfie e quadros vão para o serviço, que responde `202` e processa no worker:
   detecção → qualidade → prova de vida → embedding → comparação.
4. Com o resultado gravado, o **webhook** sai assinado para a ponte.
5. A ponte confere a assinatura e manda `APPROVED` pela serial: **LED aceso por 3 s**.
   Reprovado pisca 3 vezes; erro, uma piscada longa.

## Rodando localmente

Pré-requisitos: Docker (com Compose), um Arduino Uno (ou clone) e, para o
`face-led`, `arduino-cli` e `uv`. Em WSL2, a placa chega ao Linux pelo
`usbipd-win`.

```bash
# 1. Microserviço (tudo em containers)
cd facial-validation-service
cp .env.example .env            # ajuste segredos e componentes (ver docs/development.md)
docker compose up -d --build
docker compose exec -T api alembic upgrade head
docker compose exec -T api python -m app.cli download-models   # YuNet + SFace, conferidos por SHA-256
docker compose exec -T api python -m app.cli create-tenant --name "Demo" --slug demo
```

```bash
# 2. Arduino: gravar o firmware
cd face-led/firmware/face_led
arduino-cli compile --profile uno .
arduino-cli upload --profile uno -p /dev/ttyUSB0 .
```

```bash
# 3. Ligar o webhook do serviço à ponte e subir a ponte
docker compose -f facial-validation-service/compose.yaml exec -T api \
  python -m app.cli set-webhook --slug demo --url http://host.docker.internal:18200/webhook
cd face-led/bridge && cp .env.example .env    # cole o whsec_... em FACE_LED_WEBHOOK_SECRET
uv run face-led serve
```

Para a demonstração, o `.env` do serviço precisa ligar os motores (o padrão
é `none`, de propósito). Os limiares abaixo são **valores de teste**, o
candidato do LFW, e não de produção:

```ini
APP_ENV=local
BIOMETRIC_DETECTOR=opencv-yunet
BIOMETRIC_QUALITY_ASSESSOR=opencv
BIOMETRIC_EMBEDDER=opencv-sface
BIOMETRIC_COMPARATOR=opencv-sface
LIVENESS_PROVIDER=active
LIVENESS_SAME_PERSON_MIN_SIMILARITY=0.3443
BIOMETRIC_MODEL=opencv-sface
BIOMETRIC_MODEL_VERSION=2021dec
FACE_MATCH_POLICY_VERSION=local-test-1
FACE_MATCH_MIN_SIMILARITY=0.3443
```

Depois abra `http://127.0.0.1:18100/dev/liveness` (página de demonstração,
disponível só em `APP_ENV=local`), faça o cadastro e uma validação.

Passo a passo completo: [serviço](facial-validation-service/docs/development.md) ·
[face-led](face-led/README.md).

## Testes

```bash
docker compose -f facial-validation-service/compose.yaml exec -T api pytest -q   # 351 testes
cd face-led/bridge && uv run pytest -q                                            # 26 testes
```

## O que foi verificado e o que não foi

| Verificado | Não verificado |
|---|---|
| Suítes de testes dos dois projetos e lint limpo | Taxas de erro de ataque (APCER/BPCER) com dataset autorizado |
| EER de 1,47% no LFW (fotos de imprensa, não selfies) | Calibração do threshold com selfies reais |
| Prova de vida com webcam real (1 pessoa) e ataque simulado com fotos do LFW, documentado no ADR-010 | Outras pessoas, dispositivos e iluminações |
| Fluxo de ponta a ponta: validação aprovada → webhook → ponte → LED aceso | Proteção contra injeção digital (câmera virtual, quadros montados) |

## Documentação

- Serviço: [arquitetura](facial-validation-service/docs/architecture.md) ·
  [contrato da API](facial-validation-service/docs/api.md) ·
  [decisões (ADRs)](facial-validation-service/docs/decisions/) ·
  [avaliação do motor](facial-validation-service/docs/biometric-engine-evaluation.md) ·
  [liveness](facial-validation-service/docs/liveness-evaluation.md)
- face-led: [README](face-led/README.md) · [protocolo serial](face-led/docs/protocol.md) ·
  [stack (ADR-001)](face-led/docs/decisions/ADR-001-stack.md)

Modelos (YuNet, SFace) e o dataset LFW **não** são versionados: são baixados
pelos comandos do projeto, com SHA-256 conferido.

## Licença

[MIT](LICENSE) © 2026 Diogo Wallace
