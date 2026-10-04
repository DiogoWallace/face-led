# Resultados do POC biométrico

- **Data:** 2026-09-30
- **Código:** [`poc/`](../poc/README.md). Dados brutos em `poc/results/*.json|csv`.
- **Máquina:** Intel Core i5-7400 (4 núcleos, 4 threads, sem GPU), 7,7 GB RAM
  (cerca de 2,3 GB livres durante os testes, com os outros projetos rodando), WSL2 e Docker.
- **Versões:** Python 3.12, OpenCV 4.14.0, dlib 20.0.1, torch 2.14.1+cpu, numpy 2.5.3.
  No serviço legado: Node 18 e face-api 1.7.15.
- **Modelos:** `results/assets.json` registra URL, licença e SHA-256 de cada arquivo.

> Os resultados valem para o LFW e para derivações sintéticas, rodados nesta
> máquina. **Não substituem** a calibração com capturas reais do público-alvo.

## 1. Verificação 1:1 no LFW (protocolo View 2, 6.000 pares)

| Métrica | OpenCV YuNet + SFace | dlib ResNet (HOG + 5 pts) | serviço legado (face-api.js)¹ |
|---|---|---|---|
| Métrica de score | cosseno (↑) | euclidiana (↓) | euclidiana (↓) |
| Falha de detecção (FTA) | **0** de 7.701 imagens | 35 imagens (45 pares descartados) | 84 de 600 pares (14%) |
| Pares avaliados | 3000 G / 3000 I | 2981 G / 2974 I | 258 G / 258 I |
| Score genuíno (média / p5) | 0,649 / 0,436 | 0,439 / 0,301 (p5 = melhor 5%) | 0,445 |
| Score impostor (média / p95) | 0,083 / 0,228 | 0,830 / 0,976 | 0,823 |
| **EER** | **1,47%** (thr 0,2778) | 1,81% (thr 0,6477) | — |
| FAR ≤ 1% | thr 0,2949 → FRR 1,67% | thr 0,6261 → FRR 2,25% | — |
| FAR ≤ 0,1% | thr 0,3443 → FRR 2,13% | thr 0,5439 → FRR 9,46% (FAR 0,067%) | — |
| Threshold de referência | 0,363 (exemplo OpenCV): FAR 0,067%, FRR 2,43% | 0,6: FAR 0,44%, FRR 3,19% | 0,6: FAR 0,39%, FRR 3,49% |
| Threshold de 0,5 (sistema legado) | — | FAR 0,034%, **FRR 21%** | FAR 0%, **FRR 24,4%** |
| Acurácia 10-fold | 98,83% ± 0,41 | 98,34% ± 0,48 | — |
| Tempo médio por imagem (lote LFW) | detecção 8,3 ms + embedding 39,5 ms | detecção 38,7 ms + embedding 138 ms | 289 ms (p95 310) |

¹ Pipeline do serviço legado (SSD MobileNet 0.5 + landmarks 68 + recognition),
rodado num container descartável da imagem existente, só no fold 1 (600 pares).

Com 3.000 pares impostores, a menor FAR mensurável é cerca de 3,3×10⁻⁴.
Pontos abaixo de 0,1% não têm significância estatística.

## 2. Cenários (item 7)

São 50 casos por cenário. A referência é uma foto LFW da pessoa. O threshold de
decisão é o EER do LFW de cada motor, usado **apenas como referência do POC**.
- **Estrito:** política do serviço, exatamente 1 rosto.
- **Maior rosto:** compara o maior rosto detectado, para separar o efeito do
  cenário dos rostos de fundo que existem no LFW.

Tempo, CPU e memória são médias por caso (`results/scenarios_*.csv` tem cada
caso com input, resultado, score, tempo, CPU e memória). CPU é o percentual de
1 núcleo; o OpenCV usa 4 threads.

### OpenCV YuNet + SFace (threshold 0,2778)

| Cenário | Input | Resultado (maior rosto) | Estrito: MULTIPLE_FACES | Score médio [mín–máx] | Tempo | CPU | RSS |
|---|---|---|---|---|---|---|---|
| Mesma pessoa | outra foto LFW | 50 MATCH | 12 | 0,683 | 23 ms | 330% | 166 MB |
| Pessoas diferentes | foto de outra pessoa | 50 NO_MATCH | 12 | 0,082 | 22 ms | 324% | 166 MB |
| Iluminação escura | gamma 2.2 (simulação) | 50 MATCH | 10 | 0,652 | 22 ms | 325% | 166 MB |
| Iluminação clara | gamma 0.45 (simulação) | 50 MATCH | 11 | 0,678 | 22 ms | 331% | 166 MB |
| Câmera diferente | resolução/JPEG q25/cor/ruído (simulação) | 50 MATCH | 11 | 0,611 | 22 ms | 327% | 166 MB |
| Imagem ruim | blur σ=4 | 43 MATCH, 7 NO_MATCH | 8 | 0,415 | 21 ms | 329% | 166 MB |
| Rosto pequeno | rosto ~20 px em 640×480 | 35 MATCH, 9 NO_MATCH, 6 NO_FACE | 5 | 0,406 | 28 ms | 333% | 166 MB |
| Oclusão (boca/nariz) | retângulo tipo máscara | 49 MATCH, 1 NO_MATCH | 12 | 0,593 | 22 ms | 326% | 166 MB |
| Oclusão (olhos) | retângulo tipo óculos escuros | 33 MATCH, 17 NO_MATCH | 12 | 0,325 | 21 ms | 335% | 166 MB |
| Múltiplos rostos | 2 pessoas lado a lado | estrito: 50 MULTIPLE_FACES | 50 | — | 25 ms | 328% | 166 MB |
| Sem rosto | ruído sintético | 50 NO_FACE | — | — | 4 ms | 269% | 166 MB |

### dlib ResNet (threshold 0,6477)

| Cenário | Resultado (maior rosto) | Estrito: MULTIPLE_FACES | Score médio | Tempo | CPU | RSS |
|---|---|---|---|---|---|---|
| Mesma pessoa | 48 MATCH, 1 NO_FACE, 1 referência sem rosto | 5 | 0,417 | 155 ms | 100% | 115 MB |
| Pessoas diferentes | 47 NO_MATCH, **2 MATCH (falso aceite)**, 1 referência sem rosto | 6 | 0,843 | 157 ms | 100% | 115 MB |
| Iluminação escura | 48 MATCH, 1 NO_FACE | 3 | 0,426 | 153 ms | 100% | 115 MB |
| Iluminação clara | 48 MATCH, 1 NO_FACE | 5 | 0,422 | 153 ms | 100% | 115 MB |
| Câmera diferente | 48 MATCH, 1 NO_FACE | 4 | 0,455 | 154 ms | 100% | 115 MB |
| Imagem ruim | 42 MATCH, 7 NO_FACE | 1 | 0,556 | 142 ms | 100% | 115 MB |
| Rosto pequeno | **50 NO_FACE** | 0 | — | 158 ms | 103% | 115 MB |
| Oclusão (boca/nariz) | 23 MATCH, 5 NO_MATCH, **21 NO_FACE** | 3 | 0,536 | 107 ms | 100% | 115 MB |
| Oclusão (olhos) | 5 NO_MATCH, **45 NO_FACE** | 1 | 0,691 | 47 ms | 99% | 115 MB |
| Múltiplos rostos | estrito: 48 MULTIPLE_FACES, 1 NO_MATCH | 48 | — | 185 ms | 102% | 115 MB |
| Sem rosto | 50 NO_FACE | — | — | 35 ms | 99% | 115 MB |

**Leitura:**
- O SFace separou mesma pessoa e pessoa diferente em 100% dos 50 casos. O dlib
  teve 2 falsos aceites em 49.
- Blur, rosto pequeno e oclusão dos olhos são os pontos fracos do SFace: o score
  cai e surgem NO_MATCH. Esses casos devem ser barrados pela etapa de
  **qualidade** antes da comparação.
- O YuNet detecta rostos pequenos de fundo. Na política estrita, isso gerou
  MULTIPLE_FACES em cerca de 20% das fotos LFW. Em selfie isso tende a ser raro,
  mas a regra precisa de um tamanho mínimo e relativo de rosto (pendente).
- O detector HOG do dlib falhou com rosto pequeno e com oclusão, o que gera
  mais recusas.

## 3. Liveness (item 8)

Resultados detalhados em [liveness-evaluation.md](liveness-evaluation.md#3-resultados-do-poc-minifasnetv2--v1se).

| Teste pedido | Resultado |
|---|---|
| Pessoa real | **NOT TESTED**: sem capturas autorizadas |
| Foto impressa | **NOT TESTED** |
| Foto em tela | **NOT TESTED** |
| Reprodução de vídeo | **NOT TESTED** fisicamente. Proxy digital: vídeo de foto estática aceito como REAL em 9 de 9 vídeos |

O liveness ativo não foi implementado, conforme pedido. A seção 1 da
avaliação de liveness descreve como funcionaria (desafio com nonce, vídeo e
análise temporal).

## 4. Benchmark (item 9)

Entrada: foto LFW 250×250 (e ampliada para 640×640). Média e p95 em ms.
CPU é % de 1 núcleo.

| Etapa | 1 execução | 10 execuções | 100 execuções | CPU (100) |
|---|---|---|---|---|
| OpenCV detecção 250 px | 5,3 | 5,7 (p95 8,2) | 3,4 (p95 8,9) | 339% |
| OpenCV detecção 640 px | 27,9 | 16,0 (p95 35,7) | 22,6 (p95 48,4) | 329% |
| OpenCV embedding | 36,1 | 22,7 (p95 46,1) | 17,6 (p95 27,7) | 308% |
| OpenCV comparação | 0,081 | 0,003 | 0,002 | — |
| OpenCV pipeline (det + emb) | 30,2 | 22,0 (p95 40,3) | 27,0 (p95 56,4) | 284% |
| dlib detecção 250 px | 37,9 | 40,0 (p95 51,6) | 35,1 (p95 40,8) | 99% |
| dlib detecção 640 px | 217,9 | 215,9 (p95 240,8) | 219,5 (p95 290,6) | 102% |
| dlib embedding | 116,2 | 118,0 (p95 135,9) | 117,9 (p95 128,8) | 100% |
| dlib comparação | 0,073 | 0,005 | 0,003 | — |
| dlib pipeline | 166,7 | 150,9 (p95 163,5) | 156,7 (p95 181,1) | 100% |
| MiniFASNet liveness | 33,7 | 30,6 (p95 95,2) | 24,5 (p95 32,1) | 362% |

| Carga e memória | OpenCV | dlib | MiniFASNet |
|---|---|---|---|
| Tempo de carga dos modelos | 190 ms | 693 ms | 2.232 ms |
| RSS após carga | 150 MB (+97 MB) | 206 MB | +173 MB (torch). Processo com os 3 motores: 545 MB |

**Concorrência** (pipeline, 40 tarefas, uma instância por thread):

| Threads | OpenCV (req/s) | dlib (req/s) |
|---|---|---|
| 1 | 41,0 | 6,2 |
| 2 | 49,1 | 7,6 |
| 4 | 46,4 | 6,6 |

O OpenCV já usa os 4 núcleos numa única chamada; mais threads quase não aumentam
a vazão nesta CPU. Para escalar, a opção é **mais réplicas do worker**, cada uma
com `cv2.setNumThreads` ajustado, e não mais threads por processo.

**Thread-safety:** uma instância de `FaceDetectorYN` compartilhada por 4
threads encerrou o processo com **segfault** (código 139,
`results/thread_safety.txt`). O adapter precisa de uma instância por
thread ou processo.

## 5. Encaixe no `BiometricProvider` (item 10)

O protótipo rodou dentro do `analyze_capture` do core, sem modificá-lo
(`results/provider_fit.json`). Nos casos com exatamente 1 rosto, o protótipo:
- produziu embeddings de 512 bytes;
- mediu similaridade de 0,58 a 0,72 para a mesma pessoa e de −0,02 a 0,23 para
  pessoas diferentes;
- devolveu `LOW_QUALITY` para blur e rosto pequeno, `MULTIPLE_FACES` para
  colagens e `NO_FACE` para ruído.

Cada captura levou de 75 a 370 ms, com 4 detecções por captura por causa do
contrato atual (ver [biometric-engine-evaluation.md](biometric-engine-evaluation.md#4-encaixe-no-contrato-biometricprovider)).

## 6. Threshold (item 11)

`FaceMatchPolicy` **não foi alterada**.

| Campo | Valor |
|---|---|
| Modelo | `opencv-sface` / `2021dec` (detector YuNet 2023mar, alinhamento `alignCrop` 5 pts), similaridade cosseno |
| Threshold candidato (LFW) | 0,2778 (EER); 0,2949 (FAR 1%); **0,3443 (FAR 0,1%)** |
| FAR / FRR no candidato 0,3443 | 0,10% / 2,13% |
| Dataset | LFW View 2, 6.000 pares, fotos de imprensa, sem selfies nem documentos |
| Condições | CPU i5-7400, OpenCV 4.14.0, imagens JPEG 250×250, maior rosto |
| **Status** | **PENDING CALIBRATION** |

O status continua pendente porque o LFW não representa selfies de celular, fotos
de documento, a demografia do público nem o nível de FAR que o negócio aceita.
A menor FAR mensurável com 3.000 impostores é de cerca de 0,03%. Calibrar exige:
- um dataset autorizado do público-alvo, com milhares de pares;
- a FAR alvo definida pelo negócio;
- a análise por subgrupo (viés).

## 7. Limitações

- O LFW não tem licença explícita e é usado aqui só para avaliação interna.
  Os próprios mantenedores alertam que o benchmark não comprova adequação a uso comercial.
- Iluminação, câmera, oclusão e múltiplos rostos são simulações.
- Não houve teste de documento × selfie, que é o uso atual do serviço legado.
- A máquina estava compartilhada com os outros projetos rodando, o que gera
  variação nos tempos (por exemplo, o p95 do OpenCV).
- A qualidade usa heurísticas (nitidez, tamanho, brilho). Nenhum modelo FIQA foi avaliado.
