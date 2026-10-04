# ADR-006 — Componentes biométricos independentes

- **Status:** ACCEPTED (Fase 1 do [plano](../biometric-architecture-plan.md))
- **Data:** 2026-09-30
- **Substitui:** a porta única `BiometricProvider` da fundação

## Contexto

A fundação tinha uma única porta, `BiometricProvider`, reunindo detecção,
qualidade, liveness, embedding e comparação. A avaliação (ADR-003) mostrou
três problemas:
- o primeiro motor (YuNet + SFace) não tem liveness, que virá de outro
  fornecedor ou de uma implementação própria;
- a qualidade precisa de critérios configuráveis no domínio;
- `FaceDetectionResult` sem landmarks obrigava a detectar 4 vezes por captura.

## Decisão

Cinco portas em `app/application/ports/biometric.py`, uma por responsabilidade:
`FaceDetector`, `FaceQualityAssessor`, `FaceEmbedder`, `FaceComparator` e
`LivenessProvider`. Cada uma é escolhida por uma variável própria
(`BIOMETRIC_DETECTOR`, `BIOMETRIC_QUALITY_ASSESSOR`, `BIOMETRIC_EMBEDDER`,
`BIOMETRIC_COMPARATOR`, `LIVENESS_PROVIDER`) em
`app/infrastructure/biometric/factories.py`.

As decisões ficam no domínio, sem dependências externas:
- **`QualityGate` + `QualityRequirements`** (`domain/services/quality_gate.py`):
  - quantidade de rostos considerados, ignorando os menores que `QUALITY_MIN_FACE_PX`;
  - tamanho absoluto e relativo, nitidez e brilho;
  - scores nomeados para medidores futuros, como FIQA;
  - critério sem valor não é aplicado e fica PENDING CALIBRATION;
  - critério configurado sem medição resulta em `ERROR/QUALITY_MEASUREMENT_UNAVAILABLE`.
- **`FaceMatchPolicy`**: passa a ter `calibration_status`, `target_far`,
  `purpose` (`SELFIE_VS_REFERENCE` e `DOCUMENT_VS_SELFIE`) e
  `calibration_reference`. O registry mantém uma política ativa por finalidade.
- **`LivenessRequirement`**:
  - `REQUIRED` (padrão): sem provider, o resultado é `ERROR/LIVENESS_NOT_CONFIGURED`;
  - `DISABLED_FOR_EVALUATION`: pula o liveness.

A orquestração fica em `FaceAnalysisPipeline`
(`application/use_cases/capture_analysis.py`), sem dependência de motor:

```text
detector → gate (rostos) → assessor → gate (critérios) → liveness → embedder
```

A comparação fica em `ProcessVerification`: comparator → `FaceMatchPolicy`.

Value objects novos:
- `DetectedFace` (caixa, confiança e 5 landmarks genéricos), para que o embedder
  alinhe sem nova detecção;
- `QualityMeasurements` e `QualityReport`;
- `LivenessEvidence` (captura hoje; referência de sessão no futuro).

**Guards de ambiente** na composição, fora de `APP_ENV=local|test`, o serviço
não sobe se:
- a política estiver PENDING_CALIBRATION;
- a exigência de liveness for `DISABLED_FOR_EVALUATION`;
- algum critério central do `QualityGate` não estiver configurado.

Um teste de arquitetura (`tests/unit/test_architecture.py`) falha se
`app/domain` ou `app/application` importarem OpenCV, numpy, torch, SDKs de nuvem,
SQLAlchemy, FastAPI, Redis/arq, ou camadas externas.

## Consequências

- **Trocar ou adicionar componentes** (outro detector, outro embedder, AWS,
  Azure, liveness ativo) é registrar um adapter no catálogo de `factories.py`.
  Domínio e casos de uso não mudam.
- **Eventos de auditoria** passam a registrar os nomes dos componentes e o
  `QualityReport`. São só códigos e medidas agregadas, nunca imagem nem embedding.
- **Configuração:** `BIOMETRIC_PROVIDER` foi removida e substituída pelas cinco
  variáveis.
- **O comportamento com `none` não muda:** todo processamento termina em
  `ERROR/PROVIDER_NOT_CONFIGURED`.
- **Fase 2:** os adapters OpenCV precisam executar a inferência fora do event
  loop, com uma instância por thread (ver plano, seção 5).
- **PENDING:** a evidência de liveness por sessão ou vídeo vai mudar a API na
  Fase 7. O caso `VerifyDocumentSelfie` ainda não tem escopo definido.

## Fase 2 — adapters OpenCV (2026-09-30)

- `opencv-yunet` (detector), `opencv-sface` (embedder e comparator) em
  `app/infrastructure/biometric/opencv/`, registrados nos catálogos.
- Detector e embedder compartilham um `OpenCVRuntime` (via `ComponentContext`
  em `factories.py`): um `ThreadPoolExecutor` dedicado, uma instância de cada
  modelo por thread, carregadas no initializer. Nenhuma instância global.
- Criar o runtime não lê nada do disco. `BiometricSetup.start()` confere o
  SHA-256 e carrega os modelos; só o worker chama (`Container.start_inference`
  no `on_startup`). Sem modelo válido, o worker não sobe. Adapter usado sem
  runtime iniciado → `BiometricProviderError` → `ERROR/PROVIDER_FAILURE`.
- O embedder recebe o `DetectedFace` e reconstrói a linha do YuNet para o
  `alignCrop`: uma detecção por captura, sem redetectar.
- O comparator calcula o cosseno em numpy (mesmo valor de `FR_COSINE`,
  verificado em teste) e não usa o executor. Recusa template que não seja
  `opencv-sface/2021dec`.

## Fase 3 — medidor de qualidade e retenção (2026-09-30)

- `BIOMETRIC_QUALITY_ASSESSOR=opencv` (`OpenCVQualityAssessor`, nome
  `opencv-quality/v1`): mede brilho (média de cinza da caixa do rosto) e
  nitidez (variância do Laplaciano da caixa redimensionada para 112×112). Não
  usa modelo, mas roda no executor do `OpenCVRuntime` (decodificar e medir fora
  do event loop). A definição das medidas é versionada no nome: limites
  calibrados valem para uma definição específica.
- A decisão continua no `QualityGate`, com `QUALITY_*` vindos de configuração
  (PENDING CALIBRATION). Caixa fora da imagem → medição ausente → com o critério
  configurado, `ERROR/QUALITY_MEASUREMENT_UNAVAILABLE`.
- `CAPTURE_RETENTION` (ver ADR-004): aplicado pelos casos de uso de
  processamento depois do commit do resultado (`release_capture`).
