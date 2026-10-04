# face-led

Acende o LED integrado do Arduino Uno quando o
[facial-validation-service](../facial-validation-service) aprova uma
validação facial. Teste local. Stack e motivos: [ADR-001](docs/decisions/ADR-001-stack.md);
protocolo serial: [docs/protocol.md](docs/protocol.md).

```text
worker (Docker) --webhook HMAC--> ponte (WSL) --serial /dev/ttyUSB0--> Uno: LED 3 s
```

| LED | Significado |
|---|---|
| aceso 3 s | validação **aprovada** |
| 3 piscadas rápidas | reprovada |
| 1 piscada longa | erro (inclui expirada) |

## Estrutura

```text
firmware/face_led/   sketch (face_led.ino) + sketch.yaml (core fixo)
bridge/              ponte Python: CLI face-led (send, serve, ports) + testes
docs/                protocolo e decisões
```

## Preparação (uma vez)

**Windows (PowerShell como administrador):**

```powershell
winget install --exact dorssel.usbipd-win
usbipd list                      # anote o BUSID do "USB-SERIAL CH340"
usbipd bind --busid <BUSID>
```

**WSL:**

```bash
sudo usermod -aG dialout $USER   # depois feche e reabra o terminal do WSL
```

`arduino-cli` (core `arduino:avr@1.8.8`) e `uv` ficam em `~/.local/bin`.

## Uso

**1. Anexar a placa ao WSL** (PowerShell comum; `--auto-attach` reanexa se ela reiniciar):

```powershell
usbipd attach --wsl --busid <BUSID> --auto-attach
```

No WSL, `ls /dev/ttyUSB*` deve mostrar `/dev/ttyUSB0`.

**2. Gravar o firmware:**

```bash
cd face-led/firmware/face_led
arduino-cli compile --profile uno .
arduino-cli upload --profile uno -p /dev/ttyUSB0 .
```

**3. Testar a placa pela ponte:**

```bash
cd face-led/bridge
uv run face-led send PING
uv run face-led send APPROVED
```

**4. Ligar o webhook do serviço à ponte** (na pasta `facial-validation-service/`; o
segredo aparece uma vez — guarde em `bridge/.env`):

```bash
docker compose exec -T api python -m app.cli set-webhook --slug teste-liveness \
  --url http://host.docker.internal:18200/webhook
```

**5. Subir a ponte e validar:**

```bash
cd face-led/bridge && cp -n .env.example .env   # preencha FACE_LED_WEBHOOK_SECRET
uv run face-led serve
```

Faça uma **validação** em `http://127.0.0.1:18100/dev/liveness`: aprovada, o LED acende.

## Desenvolvimento

```bash
cd face-led/bridge
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `Permission denied: /dev/ttyUSB0` | usuário fora do grupo `dialout` (reabra o terminal depois do `usermod`) |
| `/dev/ttyUSB0` não existe | placa não anexada (`usbipd attach`) ou anexada ao Windows |
| IDE do Windows não vê a COM4 | placa anexada ao WSL: `usbipd detach --busid <BUSID>` |
| ponte responde 401 no log do serviço | `FACE_LED_WEBHOOK_SECRET` diferente do segredo do `set-webhook` |
| ponte responde 503 | placa desconectada ou firmware não gravado |
