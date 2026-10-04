# ADR-001 — Stack do face-led

- **Status:** ACCEPTED
- **Data:** 2026-10-01

## Contexto

Teste local: acender o LED integrado de um Arduino Uno (clone com CH340,
COM4 no Windows) ligado por USB quando o facial-validation-service
(Docker Desktop, WSL2) aprovar uma validação facial. Tudo local.

## Decisão

```text
worker (Docker) --webhook HMAC--> ponte (WSL, 127.0.0.1:18200) --serial--> Uno (LED_BUILTIN)
```

| Parte | Escolha |
|---|---|
| Firmware | C++ Arduino; core `arduino:avr` 1.8.8 fixo em `sketch.yaml`; FQBN `arduino:avr:uno` |
| Build/upload | `arduino-cli` 1.5 no WSL |
| USB no WSL | `usbipd-win` (`bind` uma vez como administrador; `attach --wsl --auto-attach`); driver `ch341` do kernel do WSL → `/dev/ttyUSB0` |
| Ponte | Python 3.12 + `uv`; `pyserial`; FastAPI + uvicorn; `pydantic-settings` |
| Qualidade | pytest (porta serial falsa que imita o firmware), Ruff |

- A ponte é um **consumidor do webhook** (ADR-009 do serviço): confere
  `X-FVS-Signature` (tolerância de 5 min), deduplica por `X-FVS-Event-Id` e só
  então aciona a placa. Placa fora → 503, e o serviço tenta de novo.
- Escuta só em `127.0.0.1`. Verificado em 2026-10-01: o worker alcança um
  serviço do WSL ligado em `127.0.0.1` por `http://host.docker.internal:<porta>`.
- O log da ponte tem tipo de evento e comando; nunca `subject_id` (pode ser CPF).

## Verificação (2026-10-01)

- Placa: Uno clone, CH340 (`1a86:7523`), BUSID `1-6`, `/dev/ttyUSB0` no WSL.
- Protocolo na placa real: `READY`, `PONG`, `ACK APPROVED/REJECTED`,
  `ERR UNKNOWN`, `ERR TOO_LONG`.
- Ponta a ponta: validação aprovada na `/dev/liveness` → entrega
  `verification.completed` `DELIVERED` na 1ª tentativa (HTTP 200) → ponte
  `APPROVED` → LED aceso. Assinatura errada → 401; evento repetido → ignorado.

## Alternativas descartadas

| Opção | Motivo |
|---|---|
| Web Serial na página `/dev/liveness` | Só funciona com a página aberta; não exercita o webhook |
| Ponte no Windows (Python + COM4) | Python no Windows; fora do WSL, onde ficam os projetos |
| Ponte em container | Docker Desktop não repassa porta serial para containers com facilidade |
| PlatformIO | Mais pesado do que um LED precisa |

## Consequências

- A placa fica com **um lado por vez**: anexada ao WSL, a IDE do Windows não
  vê a COM4 (`usbipd detach` devolve).
- Seu usuário precisa estar no grupo `dialout` para abrir `/dev/ttyUSB0`.
- **Pendências:** serviço da ponte sempre ativo (systemd do WSL) — PENDING DECISION.
