# Avaliação do motor biométrico

- **Data:** 2026-09-30
- **Escopo:** escolher o primeiro `BiometricProvider` real para verificação 1:1.
  Liveness está em [liveness-evaluation.md](liveness-evaluation.md). Os números
  estão em [biometric-poc-results.md](biometric-poc-results.md).
- **Ambiente de medição:** Intel Core i5-7400 (4 núcleos, sem GPU), 7,7 GB RAM, WSL2.

## 1. Motor existente: serviço legado com face-api.js

Já existia, em outro sistema, um serviço interno de comparação facial
(documento × selfie). Ele foi analisado só para decidir se valia reaproveitá-lo;
nada nele foi alterado, e as medições usaram um container descartável.

| Item | Encontrado |
|---|---|
| Runtime | Node.js 18 com Express |
| Biblioteca facial | `@vladmandic/face-api` 1.7.15 (MIT), fork do face-api.js, sobre `@tensorflow/tfjs-node` |
| Situação da biblioteca | repositório **arquivado (somente leitura) desde 05/02/2025**; o autor indica a "Human" como sucessora (github.com/vladmandic/face-api) |
| Detector | SSD MobileNet v1 (WIDER FACE), confiança mínima 0,5 |
| Landmarks | face_landmark_68 do face-api.js (licença dos dados de treino não informada) |
| Embedding | ResNet-34 com **pesos do dlib**, descritor de 128 dimensões |
| Similaridade | distância euclidiana |
| Threshold | **dois valores divergentes** entre o serviço (0,60) e o sistema que o chamava (0,5) |
| Liveness | **não existe** |
| CPU/GPU e memória | só CPU; **RSS de 525 MB** medido no POC |

**Medição do motor legado** (fold 1 do LFW, 600 pares, mesma configuração do serviço):
- Em 84 dos 600 pares (14%), pelo menos uma das imagens não teve rosto detectado.
- Com `0.5`: FAR 0% e **FRR 24,4%**.
- Com `0.6`: FAR 0,39% e FRR 3,5%.
- Tempo de 289 ms por imagem (p95 de 310 ms).

**Limitações conhecidas:**
1. A biblioteca está arquivada.
2. O Node 18 está fora de suporte desde abril de 2025.
3. Não há liveness.
4. Os thresholds são inconsistentes e não foram calibrados.
5. A taxa de falha de detecção é alta com SSD a 0.5.
6. O uso original é documento × selfie, e nenhum teste com documentos foi feito aqui.
7. O modelo de 68 landmarks tem procedência de dados não documentada.

### Dá para reaproveitar?

- **A tecnologia de reconhecimento, sim, em parte.** Os pesos do embedding são
  os do dlib ResNet, que estão em domínio público. O POC mediu esse mesmo modelo
  em Python (`dlib-bin`) sem depender do face-api.js.
- **O serviço, não.** Ele está preso a uma biblioteca arquivada e a um runtime
  sem suporte, não tem liveness e o threshold é divergente.
- **Recomendação:** não integrar o serviço legado ao novo serviço. O sistema
  que o usa pode migrar para este serviço no futuro.

## 2. Alternativas avaliadas

Licenças verificadas nas fontes oficiais de cada projeto em 2026-09-30. "Medido"
indica que o componente passou pelo POC.

| Nome | Linguagem | Licença (código / modelos) | Detecção | Embedding | Verificação | Liveness | CPU | GPU | Modelo | Dim. | Maturidade | Integração | Offline | Produção |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **OpenCV YuNet + SFace** (medido) | C++/Python (`opencv-python-headless`) | MIT (YuNet) / Apache-2.0 (SFace), opencv_zoo | Sim (YuNet, 5 landmarks, rostos de ~10 a 300 px) | Sim (SFace) | Sim (cosseno) | Não | Sim | Opcional (DNN CUDA) | `face_detection_yunet_2023mar`, `face_recognition_sface_2021dec` | 128 | Mantido pelo projeto OpenCV | Baixa: só OpenCV e numpy | Sim | **Viável** após calibração. Procedência dos dados de treino do SFace não verificada |
| **dlib ResNet** (medido) | C++/Python (`dlib`/`dlib-bin`) | Boost / modelos 5 pts, ResNet e MMOD em domínio público; **modelo de 68 pts proíbe uso comercial** | Sim (HOG ou MMOD CNN) | Sim | Sim (euclidiana) | Não | Sim | Opcional (CUDA na compilação) | `dlib_face_recognition_resnet_model_v1` | 128 | Madura, pouca evolução | Baixa | Sim | Viável, mas mais lenta e com mais falhas de detecção no POC |
| face_recognition (ageitgey) | Python | MIT; usa os modelos dlib | Sim | Sim | Sim | Não | Sim | Via dlib | Mesmo do dlib | 128 | Pouco mantida | Baixa | Sim | Equivale ao dlib. Não medida separadamente |
| InsightFace (buffalo_l / ArcFace) | Python / ONNX | Código MIT; **modelos pré-treinados "non-commercial research purposes only"**, exigem licença comercial | Sim (SCRFD) | Sim | Sim | Não | Sim | Sim | ArcFace R50 (buffalo_l) | 512 | Alta | Média | Sim | **Não, sem licença comercial.** Excluída do POC |
| DeepFace | Python | MIT no wrapper; cada modelo herda a própria licença (o README pede verificação) | Vários backends | Vários (VGG-Face, FaceNet, ArcFace, SFace, Buffalo_L...) | Sim | Opcional (`anti_spoofing=True`, modelo de terceiros) | Sim | Sim (TensorFlow) | Depende | 128 a 4096 | Alta popularidade | Média, com dependências pesadas | Sim | Só com modelo de licença comercial. Não agrega sobre usar o modelo direto |
| MediaPipe Face Detector / Face Landmarker | Python / C++ | Código Apache-2.0; licença dos modelos segundo os model cards (não verificada) | Sim (BlazeFace) | **Não** | Não | Não, mas fornece 478 landmarks e 52 blendshapes (ex.: piscar) úteis para liveness ativo | Sim | Opcional | BlazeFace, FaceMesh v2 | — | Alta (Google) | Média | Sim | Componente de apoio para liveness ativo |
| Silent-Face-Anti-Spoofing (MiniFASNet) (medido) | Python / PyTorch | Apache-2.0; o modelo de alta precisão é comercial e não foi liberado | Não (usa detector externo) | Não | Não | Passivo RGB | Sim | Opcional | MiniFASNetV2 e V1SE (80×80) | — | Baixa, repositório sem evolução | Média (torch) | Sim | **Não como PAD único** (ver liveness-evaluation) |
| AWS Rekognition (CompareFaces + Face Liveness) | SaaS (SDK) | Proprietária | Sim | Interno | Sim | **Ativo** (oval + sequência de cores), iBeta PAD nível 1 e 2 | — | — | Proprietário | — | Alta | Média: exige Amplify SDK no cliente | **Não** | Viável. Biometria processada por terceiro (disponível em São Paulo) |
| Azure AI Face (Verify + Liveness) | SaaS (SDK) | Proprietária | Sim | Interno | Sim | Passivo ou passivo-ativo, iBeta PAD nível 1 e 2 | — | — | Proprietário | — | Alta | Média: exige SDK de cliente ou Quick Link | **Não** | Viável. Acesso ao Face costuma exigir aprovação da Microsoft (não verificado nesta avaliação) |

Não entraram no POC: fornecedores dedicados de liveness (FaceTec, iProov,
Unico e similares), que exigem contrato para avaliar, e modelos sem licença
clara para uso comercial.

## 3. Componentes necessários para o pipeline completo

Nenhuma biblioteca de reconhecimento facial avaliada entrega prova de vida.
Os componentes são independentes:

| Componente | Função | Candidato medido | Situação |
|---|---|---|---|
| Face Detection | Localizar rostos e landmarks | YuNet | Pronto para calibrar. Precisa de uma regra de tamanho mínimo para ignorar rostos de fundo |
| Face Quality | Recusar imagem inutilizável | Heurística (tamanho do rosto, nitidez por variância do Laplaciano, brilho) | **Pendente.** Não há modelo FIQA avaliado; os limites precisam de calibração |
| Face Embedding | Vetor de identidade | SFace (128-d, float32 = 512 bytes) | Pronto para calibrar |
| Face Verification | Similaridade e decisão | Cosseno + `FaceMatchPolicy` | Threshold em **PENDING CALIBRATION** |
| Liveness / PAD | Presença real | MiniFASNet (passivo) | **Insuficiente** no POC. Decisão pendente |

## 4. Encaixe no contrato `BiometricProvider`

O protótipo `poc/adapter_prototype/opencv_sface_provider.py` implementa a porta
e foi executado pelo `analyze_capture` real do core (`poc/scripts/provider_fit_check.py`),
sem alterar casos de uso nem o factory. O resultado foi o esperado:
- `NO_FACE` para as imagens sem rosto;
- `MULTIPLE_FACES` para as colagens;
- `LOW_QUALITY` para blur e rosto pequeno;
- embedding de 512 bytes e similaridade calculada.

O domínio continua sem importar OpenCV, torch ou dlib. O adapter real ficará
em `app/infrastructure/biometric/`.

Pontos do contrato atual que o POC revelou (propostas, **não aplicadas**):
1. `FaceDetectionResult` não carrega a caixa nem os landmarks. Por isso o
   adapter precisa detectar de novo em `assess_quality`, `check_liveness` e
   `generate_embedding` (4 detecções por captura). Proposta: um campo opaco do
   provider no resultado, ou cache interno por captura.
2. `check_liveness(capture)` recebe uma única imagem. Liveness ativo, ou os
   SDKs da AWS e da Azure, exigem sessão ou vídeo. Isso muda a porta, o
   `CaptureData` e a API (ver liveness-evaluation).
3. **Uma instância de YuNet compartilhada entre threads derrubou o processo com
   segfault** (código 139, `results/thread_safety.txt`). O adapter precisa de
   uma instância por thread ou worker; o protótipo já faz isso.
4. O worker `arq` é assíncrono e a inferência é CPU-bound. O adapter deve rodar
   a inferência em thread ou processo separado para não bloquear o event loop.
5. `model_name=opencv-sface` e `model_version=2021dec` precisam ser gravados
   com o template (o contrato já suporta).

## 5. Recomendação

Testar como primeiro provider real o **OpenCV YuNet + SFace**, para detecção,
embedding e verificação:
- **Licença:** permissiva (MIT e Apache-2.0).
- **Execução:** 100% self-hosted em CPU; a biometria não sai da infraestrutura.
- **Precisão no LFW:** EER de 1,47% contra 1,81% do dlib; 0 falhas de detecção
  contra 35 do dlib e 14% dos pares do motor atual.
- **Desempenho:** pipeline detecção + embedding cerca de 6 vezes mais rápido
  que o dlib (27 ms contra 157 ms, média de 100 execuções) e cerca de 10 vezes
  mais rápido que o serviço legado (289 ms por imagem).
- **Dependências:** uma só (`opencv-python-headless`).

**Liveness continua PENDING DECISION.** O único candidato open-source medido
não é adequado como PAD (ver [liveness-evaluation.md](liveness-evaluation.md)).
O motor facial pode avançar para calibração, mas o serviço não deve aprovar
ninguém em produção sem um PAD avaliado.
