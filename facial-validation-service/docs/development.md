# Desenvolvimento local

## Pré-requisitos

- WSL2 com Docker Engine e Docker Compose v2 (validado com Docker 29.7.2 e
  Compose v5.4.0 no Ubuntu 24.04).
- `make` e `openssl` no WSL. Python **não** precisa estar instalado no host.

## Primeira execução

```bash
cd facial-validation-service
cp .env.example .env
make secrets            # copie os valores gerados para o .env
make up                 # build + sobe os 5 containers
make migrate            # aplica as migrations
make tenant NAME="Meu sistema" SLUG=meu-sistema   # imprime a API key uma única vez
```

API: <http://127.0.0.1:18100/docs> (acessível também pelo navegador do Windows).

## Comandos

| Ação | Comando |
|---|---|
| Subir | `make up` (`docker compose up -d --build`) |
| Parar | `make down` (mantém volumes) |
| Reiniciar | `make restart` |
| Status | `make ps` |
| Logs | `make logs` |
| Testes (tudo) | `make test` |
| Testes sem infra | `make test-unit` |
| Testes de integração | `make test-integration` |
| Migrations | `make migrate` |
| Shell no container | `make shell` |
| Lint | `make lint` |
| Regerar locks | `make lock` |
| Baixar modelos ONNX | `make models` |
| Testes com modelos reais | `make test-models` |
| Baixar o LFW | `make dataset-lfw` |
| Avaliação (LFW, 10 folds) | `make evaluate` (`ARGS="--folds 1"` para rodar rápido) |
| Regressão no LFW (fold 1) | `pytest -m evaluation` |

Os testes de integração usam recursos exclusivos: banco
`facial_validation_test`, Redis db 15 e bucket `facial-validation-test`.

## Modelos biométricos (OpenCV)

`make models` (ou `docker compose exec -T api python -m app.cli download-models`)
baixa o YuNet 2023mar (~230 KB, MIT) e o SFace 2021dec (~39 MB, Apache-2.0) do
`opencv_zoo` para `models/`. O manifesto com URL, tamanho e SHA-256 fica em
`app/infrastructure/biometric/opencv/models.py`; o download só é aceito se
conferir, e o worker confere de novo antes de carregar. `models/` está no
`.gitignore` e no `.dockerignore`: os modelos não entram no Git nem na imagem.

Para o worker usar o OpenCV em local, no `.env`:

```
BIOMETRIC_DETECTOR=opencv-yunet
BIOMETRIC_QUALITY_ASSESSOR=opencv
BIOMETRIC_EMBEDDER=opencv-sface
BIOMETRIC_COMPARATOR=opencv-sface
```

Para o pipeline chegar à decisão sem liveness (só local/test), acrescente
`LIVENESS_REQUIREMENT=DISABLED_FOR_EVALUATION`. Os limites `QUALITY_*` e
`FACE_MATCH_*` são PENDING CALIBRATION: em local, use valores de teste
explícitos e nunca os trate como valores de produção.

e `docker compose up -d --force-recreate api worker`. O log `worker_startup`
lista os modelos carregados. Sem os arquivos, o worker **não sobe**
(`ModelIntegrityError`); a API sobe normalmente, porque nunca carrega modelo.

Latência medida do detector (1 thread, Intel i5-10400, imagem de ruído, 2026-09-30;
medição atualizada com JPEG realista em `docs/evaluation-lfw.md`):
640×480 ≈ 35 ms, 1920×1080 ≈ 183 ms, 4000×3000 ≈ 1,3 s. Não há redução de
resolução antes da detecção; avaliar na Fase 4, com carga real.

## Webhook em local

Em `APP_ENV=local|test`, a URL do webhook pode ser `http`, por exemplo um
receptor no próprio compose: `http://api:9000/hook`, com um servidor simples
rodando no container `api`. O worker entrega pelo atalho na hora e pela
varredura a cada 30 s. O log `webhook_delivery` mostra status, tentativa e
código, sem URL nem corpo. Para conferir a assinatura, use o trecho de código
de [api.md](api.md#webhook-de-resultado).

## Liveness ativo em local

Com os modelos baixados e, no `.env`, os componentes OpenCV, `LIVENESS_PROVIDER=active` e
`LIVENESS_SAME_PERSON_MIN_SIMILARITY` (valor só de teste; PENDING CALIBRATION), reinicie
`api` e `worker` e abra `http://127.0.0.1:18100/dev/liveness` no navegador do Windows. A página
pede a sessão, mede cada quadro no worker (giro e escala aparecem ao vivo), só avança quando o
passo é cumprido e então envia selfie + quadros e acompanha o resultado. Ela só é montada em
`APP_ENV=local`.

Os testes não dependem do `.env`: `tests/conftest.py` remove `BIOMETRIC_*`, `LIVENESS_*`,
`FACE_MATCH_*`, `QUALITY_*` e `CAPTURE_RETENTION` (exceto nas marcas `models` e `evaluation`).

Ataque simulado ("slides" de fotos da vítima) no LFW, resultado no ADR-010:

```bash
docker compose exec -T api python -m evaluation.liveness_attacks
```

## Avaliação biométrica (LFW)

`make dataset-lfw` baixa o LFW (~180 MB, mesma origem e SHA-256 do POC) para
`datasets/lfw` (fora do Git e da imagem). `make evaluate` roda o protocolo View 2
(10 folds, 6.000 pares) com os componentes **configurados** no `.env`, pelas
portas do serviço, e grava em `evaluation-results/lfw-<UTC>/`:

- `report.md`: reconhecimento (FTA, scores, EER, FAR 1% e 0,1%, 10-fold),
  serviço (o que o `QualityGate` configurado rejeitaria), latência por etapa,
  vazão por nº de threads e detecção em imagens grandes;
- `report.json`: os mesmos dados completos, com versões, CPU e commit;
- `scores.csv`: score de cada par.

Opções: `--folds 1,2`, `--throughput 1,2,4`, `--throughput-sample 600`,
`--dir`, `--out`. A suíte padrão (`make test`) não roda a avaliação; o teste de
regressão (`pytest -m evaluation`, fold 1, ~25 s) só roda quando pedido.

O LFW são fotos de imprensa 250×250: mede se o motor funciona dentro do
serviço, **não** serve para calibrar produção.

## Portas e justificativa

| Serviço | Host | Container |
|---|---|---|
| API | 127.0.0.1:18100 | 8000 |
| PostgreSQL | 127.0.0.1:18110 | 5432 |
| Redis | 127.0.0.1:18120 | 6379 |
| S3 (SeaweedFS) | 127.0.0.1:18130 | 8333 |

A auditoria de 2026-09-30 encontrou as portas dos outros projetos concentradas
em 1026, 1431–1434, 3306–3310, 4502, 5040, 5173–5184, 5432, 6379–6382, 7070,
8025–8028, 8080–8121, 14333–14334, 27173 e 33060 (WSL, Docker e Windows).
A faixa **18100–18199** estava totalmente livre no WSL (`ss -tulpn`), no
Windows (`netstat`), nos bindings de todos os containers (inclusive parados) e
fora das faixas reservadas pelo Windows (`netsh ... excludedportrange`).
Um bloco próprio com dezenas separadas por serviço evita colisão quando
projetos parados voltarem a subir e deixa espaço para novos serviços (ex.: 18140).

Todas as portas são configuráveis no `.env` (`FVS_*_PORT`) e publicadas apenas
em `127.0.0.1`.

## Isolamento em relação aos outros projetos

- Projeto compose `facial-validation`, rede `facial-validation-network`,
  volumes `facial-validation-*-data`, containers `facial-validation-*`.
- Nenhuma rede externa; nenhum container ou volume de outro projeto é usado.
- Imagens base reaproveitadas apenas como imagem (`postgres:17-alpine`,
  `redis:7-alpine`), nunca containers.
