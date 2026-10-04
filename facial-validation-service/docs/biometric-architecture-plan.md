# Arquitetura biométrica desacoplada — diagnóstico e plano

- **Data:** 2026-09-30
- **Base:** commit `c3b6393`, com a leitura de README, `docs/`, ADR-001 a 005,
  `poc/` e todo o código de `app/` e `tests/`.
- **Objetivo:** YuNet + SFace como *primeiro* provider, sem acoplar o domínio
  nem os casos de uso a nenhum motor de reconhecimento ou de liveness.

## 1. Diagnóstico

**Runtime:**
- Python 3.12 (`python:3.12-slim-bookworm`), FastAPI, SQLAlchemy 2 async,
  Alembic, arq/Redis, boto3 (S3/SeaweedFS).
- Compose `facial-validation` com 5 containers:
  `api`, `worker`, `postgres`, `redis`, `s3`.
- `make up`, `make test` e `make lint` (Ruff).
- **Não há type checker** configurado (PENDING INFORMATION: adotar mypy?).

**Testes:** 104 aprovados e 7 pulados:
- unitários (70);
- API (24);
- integração com PostgreSQL, Redis e S3 reais (10);
- biométricos em SKIP (7).

**POC (`poc/`):** isolado do core. Contém `engines.py` (YuNet+SFace, dlib,
MiniFASNet), a avaliação LFW, os cenários, o benchmark, o protótipo
`adapter_prototype/opencv_sface_provider.py` e a checagem de thread-safety.

```text
ESTADO ATUAL
├── implementado
│   ├── Clean/Hexagonal: domain → application (ports, use_cases, dto) → infrastructure; composition root em app/container.py
│   ├── Estados explícitos (CREATED/PROCESSING/APPROVED/REJECTED/ERROR/EXPIRED), REJECTED ≠ ERROR
│   ├── FaceMatchPolicy versionada, sem threshold padrão (sem política → ERROR/POLICY_NOT_CONFIGURED)
│   ├── Casos de uso: RegisterFace, ProcessFaceRegistration, CreateVerification, ProcessVerification, GetVerification
│   ├── API: POST /subjects/{id}/face, POST /subjects/{id}/verifications, GET /verifications/{id}, /health, /ready
│   ├── Template cifrado AES-256-GCM; captura em S3 privado; logs JSON com redação; porta de métricas (no-op)
│   └── Adapter biométrico "none" (tudo termina em ERROR/PROVIDER_NOT_CONFIGURED)
├── parcialmente implementado
│   ├── Porta única BiometricProvider (detect/quality/liveness/embedding/compare num só contrato)
│   ├── Qualidade: só o booleano `acceptable` vindo do provider; nenhum critério no domínio
│   ├── FaceDetectionResult só tem face_count/confidence (sem caixa/landmarks) → o protótipo precisou detectar 4×
│   ├── Liveness: acoplado ao BiometricProvider e a uma única imagem (check_liveness(capture))
│   ├── FaceMatchPolicy sem status de calibração nem FAR alvo
│   └── Métricas: porta existe, nenhuma é emitida
├── ausente
│   ├── Adapter YuNet/SFace no core (existe só o protótipo do POC)
│   ├── Execução da inferência fora do event loop, com instância por thread
│   ├── Quality gate explícito e configurável
│   ├── Caso de uso de documento × selfie; sessão de liveness; endpoint de consulta do cadastro
│   ├── Testes de integração com modelo real e suíte "evaluation" separada
│   └── Download/verificação dos modelos (SHA-256) fora do POC
├── risco
│   ├── Segfault com YuNet compartilhado entre threads (POC, código 139)
│   ├── Worker arq é async com max_jobs=10: inferência síncrona no loop bloquearia tudo
│   ├── Threshold 0,3443 é candidato LFW; usar em produção sem calibração
│   ├── Rostos de fundo: YuNet gera MULTIPLE_FACES em ~20% das fotos LFW sem regra de tamanho mínimo
│   └── Capturas ficam no S3 sem descarte: armazenamento já existe sem política de retenção definida
└── decisão pendente
    ├── Liveness: fornecedor SaaS (AWS/Azure) × ativo próprio          PENDING DECISION
    ├── Threshold e FAR alvo                                          PENDING CALIBRATION / PENDING BUSINESS DECISION
    ├── Critérios de qualidade                                        PENDING CALIBRATION
    ├── Retenção de capturas/templates, base legal, descarte          PENDING LEGAL/BUSINESS DECISION
    ├── Expor score/threshold na API                                  PENDING BUSINESS DECISION
    └── Documento × selfie: escopo, formatos (PDF?), política própria PENDING INFORMATION
```

## 2. Arquitetura proposta (adaptada ao projeto real)

A estrutura sugerida no pedido foi comparada à existente. Três pontos foram mantidos como estão:

| Sugestão | Decisão | Motivo |
|---|---|---|
| `app/domain/biometric/contracts/` | Contratos em `app/application/ports/biometric.py` | No projeto, portas de saída ficam em `application/ports` (ADR-002). O domínio mantém os value objects e as políticas. Mover quebraria a convenção sem ganho |
| `api/routes/` na raiz | Mantém `app/interfaces/http/routes/` | A camada HTTP já existe e está testada |
| `models/` na raiz | Adotado (Fase 2), fora do Git, com SHA-256 verificado | Os modelos ONNX não devem entrar na imagem nem no repositório sem controle |

```text
app/
├── domain/
│   ├── value_objects/biometric.py      DetectedFace, BoundingBox, FaceLandmarks, FaceDetectionResult,
│   │                                   QualityMeasurements, QualityIssue, QualityReport,
│   │                                   FaceEmbedding, LivenessEvidence, LivenessResult, LivenessVerdict
│   └── services/
│       ├── face_match_policy.py        FaceMatchPolicy (+ calibração, FAR alvo, finalidade)
│       └── quality_gate.py             QualityRequirements + QualityGate (decisão pura, sem OpenCV)
├── application/
│   ├── ports/biometric.py              FaceDetector, FaceQualityAssessor, FaceEmbedder,
│   │                                   FaceComparator, LivenessProvider (+ erros)
│   └── use_cases/
│       ├── capture_analysis.py         orquestra detector → gate → liveness → embedder
│       ├── process_face_registration.py / process_verification.py   (usam os contratos acima)
│       ├── verify_document_selfie.py   (fase futura; PENDING INFORMATION)
│       └── check_liveness.py           (Fase 7)
└── infrastructure/biometric/
    ├── unconfigured.py                 implementações "não configurado" de cada porta
    ├── factories.py                    monta os componentes a partir da configuração
    ├── opencv/                         (Fase 2) yunet_detector.py, sface_embedder.py, quality.py, runtime.py
    └── liveness/                       (Fase 7+) active/, aws/, azure/
```

```text
Use cases ──► FaceDetector ──────────► YuNetDetector          (cv2.FaceDetectorYN)
          ──► FaceQualityAssessor ───► OpenCVQualityAssessor  (Laplaciano, brilho, tamanho)
          ──► QualityGate (domínio, sem dependência externa)
          ──► LivenessProvider ──────► Unconfigured | Active | AWS | Azure
          ──► FaceEmbedder ──────────► SFaceEmbedder          (cv2.FaceRecognizerSF)
          ──► FaceComparator ────────► SFaceCosineComparator
          ──► FaceMatchPolicy (domínio: threshold vindo de configuração)
```

## 3. Contratos

Todos são assíncronos. As implementações OpenCV executarão a inferência num
executor dedicado (seção 5).

| Contrato | Responsabilidade | Entrada → saída | Não faz |
|---|---|---|---|
| `FaceDetector` | Encontrar rostos e 5 landmarks | `CaptureData` → `FaceDetectionResult(faces: tuple[DetectedFace])` | Não decide se a quantidade é aceitável |
| `FaceQualityAssessor` | **Medir** qualidade de um rosto | `CaptureData`, `DetectedFace` → `QualityMeasurements` (tamanho, proporção, nitidez, brilho, pose/oclusão/FIQA opcionais) | Não decide aprovação |
| `QualityGate` (domínio) | **Decidir** com `QualityRequirements` configuráveis | detecção + medições → `QualityReport(passed, issues)` | Não mede nada |
| `FaceEmbedder` | Gerar o template | `CaptureData`, `DetectedFace` → `FaceEmbedding(vector, model_name, model_version)` | Não compara |
| `FaceComparator` | Similaridade entre embeddings **do mesmo modelo** | 2 × `FaceEmbedding` → `float` (maior = mais parecido) | Não aplica threshold |
| `FaceMatchPolicy` (domínio) | Decidir MATCH/NO_MATCH | similaridade + modelo → `MatchDecision` | Não sabe de OpenCV |
| `LivenessProvider` | Prova de vida | `LivenessEvidence` (captura hoje; sessão/vídeo futuramente) → `LivenessResult` | Não depende do SFace |

`DetectedFace` carrega caixa, confiança e 5 landmarks genéricos (olhos, nariz,
cantos da boca). Assim o embedder alinha sem nova detecção, e qualquer detector
que produza esses 5 pontos serve. `FaceEmbedding` já guarda modelo e versão, e
comparar modelos diferentes resulta em `ERROR/MODEL_MISMATCH` (já implementado).

A porta atual `BiometricProvider` será **substituída** por essas cinco. É a
única mudança estrutural da Fase 1. Ela afeta `capture_analysis.py`, os dois
casos de uso de processamento, `container.py`, `infrastructure/biometric` e os
dublês de teste. Não afeta domínio de estados, banco, API nem migrations.

## 4. Fluxos

```text
Cadastro      captura → detecção → gate (nº de rostos, tamanho, nitidez, brilho...) → liveness → embedding
              → template cifrado (AES-GCM) → APPROVED       [armazenamento: ver seção 10]
Verificação   captura → detecção → gate → liveness → embedding → referência ativa → comparator
              → FaceMatchPolicy (threshold configurado) → APPROVED | REJECTED(FACE_MISMATCH)
Liveness      sessão → desafio/captura → LivenessProvider → resultado        (Fase 7; hoje: evidência = captura)
Doc × selfie  documento (imagem/PDF?) + selfie → detecção em cada → gate próprio (documento tem outra qualidade)
              → embeddings → comparator → FaceMatchPolicy de finalidade DOCUMENT_SELFIE   (PENDING INFORMATION)
```

A **ordem gate → liveness → embedding** é mantida: não se gasta liveness nem
embedding com imagem reprovada. `VerifyFace` (1:1 contra referência cadastrada)
e `VerifyDocumentSelfie` (1:1 sem cadastro, contra documento) são casos de uso
distintos. O segundo terá política, requisitos de qualidade e retenção próprios.

**Liveness no fluxo:** o `LivenessRequirement` é configurável:
- `REQUIRED` (padrão): sem provider configurado, o processo termina em `ERROR/LIVENESS_NOT_CONFIGURED`.
- `DISABLED_FOR_EVALUATION`: permitido **somente** em `APP_ENV=local|test`, para
  medir o reconhecimento facial isoladamente. O serviço recusa subir com esse
  modo em staging ou produção.

## 5. Concorrência (implementação na Fase 2)

Fatos medidos no POC:
- YuNet compartilhado entre threads causa segfault.
- O OpenCV já usa 4 threads internas por chamada.
- A vazão do processo foi de 41 a 49 req/s, sem ganho acima de 2 threads.

| Aspecto | Estratégia |
|---|---|
| Onde roda | **Somente no worker.** A API só valida, grava e enfileira, e não carrega modelos |
| Fora do event loop | `loop.run_in_executor(executor, ...)` em cada chamada de adapter OpenCV |
| Isolamento | `ThreadPoolExecutor(max_workers=N, initializer=...)` com **uma instância de YuNet e SFace por thread** (`threading.local`). Nenhuma instância global |
| Lifecycle | Carregados no `on_startup` do worker, após verificar o SHA-256 dos arquivos em `models/`. Falha de carga impede o worker de subir. O executor é encerrado no `on_shutdown` |
| Workers | `BIOMETRIC_INFERENCE_THREADS` (padrão 1) e `BIOMETRIC_OPENCV_THREADS` (`cv2.setNumThreads`, padrão do OpenCV). Escalar = mais réplicas do container worker, não mais threads |
| Carga | O `max_jobs` do arq (10) limita jobs em voo. A fila absorve picos. O executor serializa a inferência por processo. A latência sob carga será medida na Fase 4, sem otimização antes da medição |

## 6. Testes

| Suíte | Marcador | Conteúdo | Executa em |
|---|---|---|---|
| Unitários | (nenhum) | QualityGate, QualityRequirements, FaceMatchPolicy (calibração, finalidade, modelo), LivenessRequirement, casos de uso com dublês por porta, erros, DTOs | `make test-unit`, sempre |
| API | (nenhum) | Contrato HTTP (existente) | sempre |
| Integração | `integration` | PostgreSQL, Redis, S3 (existente) + **imagem → YuNet → SFace → resultado** com modelos reais (Fase 2/4) | `make test-integration` |
| Evaluation | `evaluation` | FAR, FRR, EER, acurácia, distribuição de scores, FTA e latência sobre dataset externo (`EVALUATION_DATASET_DIR`) | só sob demanda; SKIP sem dataset |
| Biometric (existente) | `biometric` | Cenários reais (sem rosto, múltiplos, ruim, diferente, mesma, liveness) | SKIP até haver dataset autorizado |

Imagens e datasets nunca são versionados. Os testes de integração com modelo
real usam imagens **sintéticas sem pessoas** (ex.: sem rosto) ou datasets
externos apontados por variável de ambiente (PENDING DECISION: fonte das
imagens de rosto para CI).

## 7. Quality gate

Requisitos em `QualityRequirements`, todos vindos de configuração
(`QUALITY_*`). Valor vazio significa **não configurado**, e o critério fica
marcado como `PENDING CALIBRATION`:

| Critério | Configuração | Estado |
|---|---|---|
| Quantidade de rostos | exatamente 1 rosto **considerado** (rostos menores que `QUALITY_MIN_FACE_PX` não contam, para ignorar rostos de fundo) | Regra ativa. Tamanho mínimo PENDING CALIBRATION |
| Tamanho mínimo do rosto (px) | `QUALITY_MIN_FACE_PX` | PENDING CALIBRATION |
| Tamanho relativo (rosto / imagem) | `QUALITY_MIN_FACE_RATIO` | PENDING CALIBRATION |
| Nitidez (variância do Laplaciano) | `QUALITY_MIN_SHARPNESS` | PENDING CALIBRATION |
| Iluminação (brilho médio) | `QUALITY_MIN_BRIGHTNESS`, `QUALITY_MAX_BRIGHTNESS` | PENDING CALIBRATION |
| Oclusão | medição opcional do assessor | PENDING DECISION: sem medidor avaliado |
| Pose | medição opcional (landmarks) | PENDING CALIBRATION |
| FIQA | medição opcional (score) | PENDING DECISION: nenhum modelo avaliado |

Se um critério estiver configurado e a medição não existir, o resultado é
`ERROR`, nunca aprovação silenciosa. Fora de `local|test`, o serviço exige que
os critérios obrigatórios estejam configurados (a lista obrigatória é PENDING
BUSINESS DECISION; proposta: tamanho mínimo e nitidez).

## 8. Threshold

```text
Modelo                   opencv-sface / 2021dec (YuNet 2023mar, alinhamento 5 pts, cosseno)
LFW candidate threshold  0.3443   (FAR 0,10% / FRR 2,13% no LFW View 2)
Status                   PENDING CALIBRATION
FAR alvo                 PENDING BUSINESS DECISION
```

A `FaceMatchPolicy` ganha `calibration_status` (`PENDING_CALIBRATION` ou
`CALIBRATED`), `target_far`, `purpose` (`SELFIE_VS_REFERENCE`,
`DOCUMENT_VS_SELFIE`) e `calibration_reference`.
- O threshold vem só de configuração (`FACE_MATCH_*`). Não há valor padrão
  nem hardcode no adapter.
- Uma política `PENDING_CALIBRATION` só é aceita em `APP_ENV=local|test`.
  Fora disso, o serviço não sobe.

## 9. Liveness: pendências

| Item | Estado |
|---|---|
| Fornecedor SaaS (AWS/Azure) × ativo próprio | **Decidido: ativo próprio** (ADR-010) |
| Evidência: captura única, vídeo ou sessão do fornecedor | **Decidido:** sessão de desafio + quadros JPEG/PNG + selfie (ADR-010) |
| Endpoints de sessão (`POST /liveness-sessions`) | **Pronto** (ADR-010) |
| Parâmetros do desafio e limiar de mesma pessoa | PENDING CALIBRATION (só roda em local/test) |
| Injeção digital (câmera virtual, quadros montados) | Não coberto; atestação de dispositivo PENDING DECISION |
| Liveness no cadastro e na validação: mesmo nível? | PENDING BUSINESS DECISION |
| Dataset de PAD autorizado (real/impressa/tela/replay) | PENDING LEGAL/BUSINESS DECISION (consentimento) |
| Transferência de biometria a terceiro (se SaaS) | PENDING LEGAL/BUSINESS DECISION |
| MiniFASNet | Não será PAD único. No máximo sinal auxiliar calibrado |

## 10. Segurança e LGPD

| Tema | Estado atual | Situação |
|---|---|---|
| Finalidade | Cadastro e validação 1:1 | PENDING LEGAL/BUSINESS DECISION (base legal, consentimento) |
| Retenção da captura | Fica no S3 sem prazo | **PENDING LEGAL/BUSINESS DECISION.** Proposta: descartar após o processamento, com a configuração pronta na Fase 3 e padrão a decidir |
| Retenção do template | Até o recadastro, sem expurgo | PENDING LEGAL/BUSINESS DECISION |
| Criptografia | Template AES-256-GCM (chave em env) | Rotação/KMS pendente (ADR-004) |
| Acesso e auditoria | API key por tenant; `verification_events` sem biometria | Mecanismo definitivo pendente (ADR-002) |
| Descarte a pedido do titular | Inexistente | PENDING LEGAL/BUSINESS DECISION |

Nada novo de armazenamento é implementado neste plano.

## 11. Plano por fases

| Fase | Objetivo | Arquivos | Dependências | Testes | Riscos | Concluída quando |
|---|---|---|---|---|---|---|
| **1. Arquitetura + contratos** | Substituir `BiometricProvider` por portas separadas; quality gate e liveness requirement no domínio e na aplicação; política de match com calibração | `domain/value_objects/biometric.py`, `domain/services/{quality_gate,face_match_policy}.py`, `application/ports/{__init__,biometric}.py`, `application/use_cases/{capture_analysis,process_face_registration,process_verification}.py`, `infrastructure/biometric/{__init__,unconfigured,factories}.py`, `container.py`, `config.py`, `.env.example`, `tests/fakes.py`, `tests/unit/*` | nenhuma nova | unitários de gate, política, liveness requirement e casos de uso; suíte completa verde | regressão nos fluxos existentes | 0 falhas, Ruff limpo, containers saudáveis, API e worker sobem, comportamento com `none` preservado |
| **2. Adapter YuNet + SFace** | Implementar detector, embedder e comparator OpenCV com executor e instância por thread | `infrastructure/biometric/opencv/*`, `models/` (fora do Git) + script de download com SHA-256, `pyproject.toml` (`opencv-python-headless`), `requirements/*.lock`, `worker.py` (lifecycle) | lock regenerado | integração: imagem sintética sem rosto → NO_FACE; teste de concorrência sem segfault | tamanho da imagem, ABI do OpenCV | worker processa com provider `opencv` em local; API inalterada |
| **3. Quality gate** | Medições OpenCV (tamanho, proporção, nitidez, brilho) e motivos detalhados; retenção configurável da captura | `infrastructure/biometric/opencv/quality.py`, `QualityReport` nos eventos | Fase 2 | unitários do gate; integração com medições | limites sem calibração | critérios configuráveis, rejeições com `issues` auditáveis |
| **4. Testes** | Suíte `evaluation` separada (FAR/FRR/EER/acurácia/FTA/latência) portada do POC; carga no worker | `tests/evaluation/*`, `pyproject` (marcador) | dataset externo | a própria suíte | dataset não representativo | relatório reproduzível via `make evaluate` |
| **5. Calibração** | Threshold e requisitos de qualidade com dataset autorizado e FAR alvo | configuração + ADR | dataset e FAR alvo (PENDING) | evaluation | viés por subgrupo | política `CALIBRATED` documentada |
| **6. API** | Resposta estruturada (`quality.issues`); consulta do cadastro; decisão sobre expor score/threshold | `interfaces/http/*` | decisões de negócio | testes de API | vazar informação útil a atacante | contrato versionado em `/api/v1` |
| **7. Liveness PoC** (mecanismo pronto, ADR-010; falta APCER/BPCER) | Provider escolhido atrás de `LivenessProvider`, com sessão e evidência | `infrastructure/biometric/liveness/*`, `use_cases/check_liveness.py`, rotas de sessão | decisão de fornecedor ou próprio | dataset de PAD | contrato da API muda | APCER/BPCER medidos |
| **8. Migração do sistema legado** | Migrar do serviço legado (documento × selfie) | fora deste repositório | Fases 1 a 7 e `VerifyDocumentSelfie` | contrato | quebra no checkout/cadastro | plano de migração aprovado |

## 12. Riscos

1. **Segfault / event loop:** mitigado pela estratégia da seção 5. Precisa de
   teste de concorrência na Fase 2.
2. **Threshold LFW usado como produção:** mitigado pelo guard de calibração
   (seção 8).
3. **Rostos de fundo:** mitigado por "rostos considerados" com tamanho mínimo
   configurável (seção 7).
4. **Retenção indefinida de capturas:** já existe hoje. Depende de decisão
   jurídica.
5. **Mudança de contrato do liveness** (sessão/vídeo) na Fase 7: o
   `LivenessEvidence` já prevê isso, mas a API vai mudar.
6. **Imagem do worker maior** com OpenCV: a medir na Fase 2.
7. **Sem type checker:** PENDING INFORMATION.

## 13. Arquivos da Fase 1

| Ação | Arquivo |
|---|---|
| alterar | `app/domain/value_objects/biometric.py`, `app/domain/value_objects/status.py`, `app/domain/value_objects/__init__.py` |
| criar | `app/domain/services/quality_gate.py` |
| alterar | `app/domain/services/face_match_policy.py`, `app/domain/services/__init__.py` |
| criar | `app/application/ports/biometric.py` |
| alterar | `app/application/ports/__init__.py` |
| alterar | `app/application/use_cases/capture_analysis.py`, `process_face_registration.py`, `process_verification.py` |
| alterar | `app/infrastructure/biometric/__init__.py`, `app/infrastructure/biometric/unconfigured.py` |
| criar | `app/infrastructure/biometric/factories.py` |
| alterar | `app/container.py`, `app/config.py`, `app/main.py`, `app/worker.py`, `.env.example` |
| alterar | `tests/fakes.py`, `tests/conftest.py`, `tests/unit/*`, `tests/api/test_http_api.py`, `tests/integration/test_flow_with_real_infrastructure.py` |
| criar | `tests/unit/test_quality_gate.py` |
| alterar | `docs/architecture.md`, `docs/decisions/ADR-003-biometric-provider.md` |
| criar | `docs/decisions/ADR-006-biometric-components.md` |

## 14. Andamento

| Fase | Estado | Referência |
|---|---|---|
| 1. Arquitetura + contratos | concluída | `50ffe25`, ADR-006 |
| 2. Adapter YuNet + SFace | concluída | `bff91a5`, ADR-006 § Fase 2 |
| 3. Quality gate + retenção | concluída | `6b10f36`, ADR-006 § Fase 3 |
| 4. Testes (suíte de avaliação) | concluída (LFW) | branch `feat/evaluation-suite`, [evaluation-lfw.md](evaluation-lfw.md) |
| 5. Calibração | pendente — depende de dataset de selfies autorizado e FAR alvo | — |
| 6. API | concluída | branch `feat/api-contract`, ADR-007, [api.md](api.md) |
| 7 e 8 | pendentes — a 7 depende da escolha do liveness | — |

**Fase 2 — o que foi feito e o que divergiu do plano:**

- Feito como planejado: executor dedicado com instância por thread, carga no
  `on_startup` do worker após o SHA-256, `models/` fora do Git e da imagem,
  `BIOMETRIC_INFERENCE_THREADS` e `BIOMETRIC_OPENCV_THREADS`, teste de
  concorrência sem segfault, imagem sintética sem rosto → `NO_FACE` (também de
  ponta a ponta, pela API e pelo worker).
- Download dos modelos virou comando da CLI (`python -m app.cli download-models`,
  `make models`) em vez de script avulso: reaproveita o manifesto que o worker usa.
- O comparator não usa o executor: o cosseno de 128 valores é feito em numpy.
- Nova configuração: `BIOMETRIC_DETECTOR_SCORE_THRESHOLD` (0,6, valor do POC).
  Afeta quantos rostos o detector devolve; revisar na calibração (Fase 5).
- Não há teste com rosto real: nenhuma foto é versionada e a Fase 2 não tem
  dataset. Detecção e embedding de rostos reais foram medidos no POC; a
  validação dentro do serviço fica para a suíte `evaluation` (Fase 4).
- O `FaceQualityAssessor` continua `none` (Fase 3): com OpenCV, captura com
  rosto termina em `ERROR/PROVIDER_NOT_CONFIGURED`.
- **Risco medido:** a detecção roda na resolução original. 640×480 ≈ 35 ms,
  1920×1080 ≈ 183 ms, 4000×3000 ≈ 1,3 s (1 thread, Intel i5-10400). Reduzir a imagem
  antes de detectar é decisão da Fase 4, com medição sob carga.

**Fase 3 — o que foi feito e o que divergiu do plano:**

- `OpenCVQualityAssessor` mede nitidez e brilho; tamanho absoluto e relativo já
  vinham da detecção. Oclusão, pose e FIQA continuam sem medidor (PENDING).
- Nitidez medida na caixa redimensionada para 112×112. Medido com textura
  sintética: na resolução original a medida caiu de 64 para 2,4 entre rostos de
  112 e 900 px; normalizada, ficou entre 61 e 64. Sem isso, um único limite de
  nitidez não serviria para rostos de tamanhos diferentes.
- Nenhum limite `QUALITY_*` foi definido: todos seguem PENDING CALIBRATION
  (Fase 5).
- Retenção: `CAPTURE_RETENTION=KEEP|DELETE_AFTER_PROCESSING`, padrão `KEEP`
  (comportamento anterior). A política de produção continua PENDING
  LEGAL/BUSINESS DECISION; o serviço **não** recusa subir sem ela.
- Verificado de ponta a ponta (API + worker + PostgreSQL + S3): com
  `DELETE_AFTER_PROCESSING`, a captura sumiu do bucket, a chave virou `NULL` e
  o evento `CAPTURE_DELETED` foi gravado.
- Continua sem teste com rosto real (nenhuma foto versionada).

**Fase 4 — o que foi feito e o que divergiu do plano:**

- Pacote `evaluation/` na raiz, em vez de `tests/evaluation/*`: é ferramenta, não
  teste, e fica fora da imagem de runtime. `make evaluate` gera o relatório
  reproduzível (JSON, Markdown, CSV) em `evaluation-results/` (fora do Git); o
  teste de regressão no fold 1 fica em `tests/evaluation` (marcador `evaluation`,
  fora da suíte padrão).
- Avalia os componentes **configurados**, pelas portas, com o mesmo setup do
  worker — não o motor do POC. Reproduziu o POC: EER 1,47%, 10-fold 98,83%,
  0 FTA. É a primeira verificação com rosto real dentro do serviço.
- "Carga no worker" foi medida como vazão do runtime de inferência (1, 2 e 4
  threads), sem fila e sem banco; carga de ponta a ponta pela fila fica para
  quando houver ambiente de homologação.
- Dataset: só LFW. A pasta `datasets/` aceita outro dataset no futuro, mas o
  carregador de selfies autorizadas depende do formato que for definido.
- Decisões que continuam PENDING com base nos números: redução de resolução
  antes da detecção (1,2 s a 12 MP; efeito na acurácia não mensurável no LFW)
  e `QUALITY_MIN_FACE_PX` (20% do LFW recusado como `MULTIPLE_FACES` sem ele).
- Corrigido: as latências da Fase 2 estavam rotuladas como i5-7400; foram
  medidas num i5-10400.

**Fase 6 — o que foi feito:**

- Decisões do usuário (2026-10-01), registradas no ADR-007: o score não é
  exposto; a qualidade sai só em códigos (`quality: {passed, issues}`), sem
  medidas; a consulta do cadastro ganhou dois endpoints
  (`/face-registrations/{id}` e `/subjects/{id}/face`).
- Os códigos de qualidade foram gravados no agregado (`quality_issues`, JSONB,
  migration `0002`, com `downgrade`). Antes eles só existiam nos eventos.
  Registros antigos ficam NULL.
- Contrato v1 e regra de compatibilidade (só mudanças aditivas) em `docs/api.md`.
  Um teste falha se algum schema da OpenAPI documentar score, threshold,
  medidas ou template.
- A Fase 6 do plano não previa recadastro (hoje 409) nem webhook; os dois
  continuam pendentes (ADR-002).

**Fora das fases — recadastro (ADR-008):** `PUT /subjects/{id}/face`, com as
decisões do usuário de confiar no consumidor e apagar o template substituído.
Foi a primeira verificação do fluxo completo com rosto real pela API e pelo
worker, usando fotos do LFW (liveness desligado para avaliação e política de
teste local): cadastro aprovado, mesma pessoa aprovada, outra pessoa
`FACE_MISMATCH`, recadastro aprovado com o template antigo apagado no banco.

**Fora das fases — webhook de resultado (ADR-009):** URL por tenant via CLI,
corpo igual ao GET, assinatura HMAC, outbox transacional com varredura a cada
30 s, e cerca de 24 h de tentativas (decisões do usuário). Verificado de ponta a
ponta com worker real e um receptor no compose que respondeu 500 na primeira
tentativa: a nova tentativa veio depois de 79 s, as duas com assinatura válida,
o mesmo `event_id` e corpo igual ao GET; a entrega terminou `DELIVERED` e o
segredo ficou cifrado no banco.
