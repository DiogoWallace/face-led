# Protocolo serial — `face-led/1`

115200 baud, 8N1, texto ASCII, **um comando por linha** (`\n`; `\r` é ignorado).
Linhas com mais de 31 caracteres são descartadas com `ERR TOO_LONG`.

| Comando (ponte → placa) | LED | Resposta |
|---|---|---|
| (placa liga ou reinicia) | apagado | `READY face-led/1` |
| `PING` | — | `PONG face-led/1` |
| `APPROVED` | aceso por 3 s | `ACK APPROVED` |
| `REJECTED` | 3 piscadas rápidas (150 ms) | `ACK REJECTED` |
| `ERROR` | 1 piscada longa (1 s) | `ACK ERROR` |
| `OFF` | apaga | `ACK OFF` |
| outro | — | `ERR UNKNOWN` |

- Um comando novo **substitui** o padrão em andamento; o LED sempre termina apagado.
- Abrir a porta reinicia o Uno (DTR): espere o `READY` (~2 s) ou confirme com `PING`.
- Mudou o protocolo de forma incompatível? Suba a versão (`face-led/2`) no firmware
  e em `bridge/src/face_led_bridge/protocol.py`.

## Mapa resultado → comando (ponte)

| Webhook do serviço | `data.decision` | Comando |
|---|---|---|
| `verification.completed` | `APPROVED` | `APPROVED` |
| `verification.completed` | `REJECTED` | `REJECTED` |
| `verification.completed` | `ERROR` (inclui `EXPIRED`) | `ERROR` |
| outros tipos (ex.: `face_registration.completed`) | — | ignorado (configurável) |
