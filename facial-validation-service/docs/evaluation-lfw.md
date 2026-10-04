# Avaliação no LFW — componentes do serviço

- **Data:** 2026-10-01 · commit base `6942bc8` · comando `make evaluate`
  (`python -m evaluation lfw`), relatório completo em `evaluation-results/` (fora do Git)
- **Componentes avaliados:** `opencv-yunet/2023mar`, `opencv-quality/v1`,
  `opencv-sface/2021dec`, `opencv-sface-cosine` — os **adapters do serviço**,
  chamados pelas portas, com o mesmo `BiometricSetup` do worker
- **Dataset:** LFW View 2 (10 folds, 6.000 pares, 7.701 imagens), `pairs.txt`
  SHA-256 `ea42330c…1592`
- **Máquina:** Intel Core i5-10400 (6 núcleos, 12 threads), WSL2 + Docker,
  OpenCV 4.14.0, numpy 2.5.3, Python 3.12

> O LFW são fotos de imprensa 250×250, centradas no rosto. Os números abaixo
> provam que o motor funciona **dentro do serviço**; **não** calibram
> produção. Threshold e FAR alvo continuam PENDING (plano §8, Fase 5).

## 1. Reconhecimento — reproduz o POC

Camada de reconhecimento: o maior rosto de cada imagem → embedding → cosseno.

| Métrica | Serviço (Fase 4) | POC (`poc/results/lfw_opencv.json`) |
|---|---|---|
| Falha de detecção | **0** de 7.701 | 0 |
| Score genuíno (média / p5 / p1) | 0,649 / 0,436 / 0,182 | 0,649 / 0,436 / — |
| Score impostor (média / p95 / p99 / máx) | 0,083 / 0,228 / 0,293 / 0,404 | 0,083 / 0,228 / — / — |
| **EER** | **1,47%** (thr 0,2778) | 1,47% (thr 0,2778) |
| FAR ≤ 1% | thr 0,2949 → FRR 1,67% | thr 0,2949 → FRR 1,67% |
| FAR ≤ 0,1% | thr 0,3443 → FRR 2,10% | thr 0,3443 → FRR 2,13% |
| Acurácia 10-fold | 98,83% ± 0,40 | 98,83% ± 0,41 |

Os números coincidem com o POC. A diferença em FAR ≤ 0,1% (2,10% contra 2,13%,
um par genuíno) vem da escolha do threshold: a suíte também considera os scores
genuínos como candidatos e pega o menor threshold que atende a FAR. Com isso,
fica verificado o caminho que os testes sintéticos não cobriam: detecção do
YuNet → `DetectedFace` → linha reconstruída para o `alignCrop` → template de
128 float32 → cosseno.

Com 3.000 impostores, a menor FAR mensurável é 0,033%. O ponto de 0,1% se
apoia em 3 pares e não tem precisão estatística.

## 2. Camada do serviço — o QualityGate sem calibração

Mesma detecção, decidida pelo `QualityGate` com os `QUALITY_*` em vigor (todos
vazios, PENDING CALIBRATION):

| Resultado | Imagens |
|---|---|
| ACCEPTED | 6.149 (79,8%) |
| MULTIPLE_FACES | 1.552 (20,2%) |

Rostos detectados por imagem: 1 rosto em 6.149, 2 em 1.206, 3 ou mais em 346.
Sem `QUALITY_MIN_FACE_PX`, todo rosto conta, inclusive os de fundo, e um em cada
cinco cadastros do LFW seria recusado. O POC já tinha apontado isso. O valor de
`QUALITY_MIN_FACE_PX` é calibração (Fase 5), feita com selfies, onde a
proporção de rostos ao fundo é outra.

## 3. Latência, vazão e memória

Passada principal (1 thread de inferência, sem fila): 7.701 imagens em 122,5 s.

| Etapa | média | p50 | p95 | p99 | máx (ms) |
|---|---|---|---|---|---|
| Detecção (YuNet) | 3,1 | 2,8 | 4,8 | 6,3 | 20,6 |
| Qualidade | 0,8 | 0,8 | 1,1 | 1,3 | 3,1 |
| Embedding (SFace) | 11,8 | 11,0 | 16,5 | 20,3 | 44,8 |

Vazão do pipeline no runtime do worker (detecção, qualidade e embedding; sem
fila e sem banco; 600 imagens):

| Threads de inferência | img/s |
|---|---|
| 1 | 59,7 |
| 2 | 64,4 |
| 4 | 83,7 |

Memória das instâncias dos modelos, medida em processo limpo: +54 MB com 1
thread, +101 MB com 2 e +194 MB com 4 (cerca de 50 MB por thread). O worker
ocioso com 1 thread usa ~145 MB.

Detecção em imagem grande (sintética, sem rosto, JPEG realista):

| Resolução | JPEG | Detecção |
|---|---|---|
| 640×480 | 0,04 MB | 14 ms |
| 1280×720 | 0,12 MB | 58 ms |
| 1920×1080 | 0,26 MB | 157 ms |
| 4000×3000 | 1,49 MB | 1.234 ms |

## 4. Conclusões para as próximas fases

1. **O motor está integrado corretamente.** Os resultados do POC se reproduzem
   pelas portas do serviço. A regressão `pytest -m evaluation` (fold 1)
   protege essa integração.
2. **Threads:** de 1 para 4 threads, a vazão sobe ~40% e a memória ~140 MB.
   Como o OpenCV já paraleliza cada chamada, o ganho é pequeno. Isso confirma
   o plano: escalar com réplicas do worker. `BIOMETRIC_INFERENCE_THREADS=1`
   continua sendo o padrão razoável.
3. **Imagem grande** (selfie de celular, 12 MP): só a detecção leva ~1,2 s,
   contra ~3 ms no LFW. Reduzir a imagem antes de detectar cortaria esse
   custo, mas o efeito na **acurácia** não pode ser medido no LFW (250×250).
   Fica **PENDING**: decidir com o dataset de selfies, junto da calibração.
4. **Pendente:** o `QualityGate` sem `QUALITY_MIN_FACE_PX` recusa 20% das
   imagens com rosto de fundo. É calibração, não bug.
5. **Divergência registrada:** o `docs/biometric-poc-results.md` cita um
   i5-7400 (4 núcleos), e esta avaliação rodou num i5-10400 (12 threads). As
   métricas de acurácia não dependem da máquina; as de tempo não são
   comparáveis entre os dois documentos.
