# ADR-003 — Motor biométrico

- **Status:** PROPOSED. O motor facial está recomendado para calibração; liveness,
  threshold e qualidade estão em PENDING DECISION.
- **Data:** 2026-09-30 (revisa a versão da fundação)
- **Evidências:** [biometric-engine-evaluation.md](../biometric-engine-evaluation.md),
  [liveness-evaluation.md](../liveness-evaluation.md),
  [biometric-poc-results.md](../biometric-poc-results.md), `poc/results/`

## Contexto

O serviço precisa de detecção, qualidade, liveness, embedding e verificação 1:1.
O domínio depende só da porta `BiometricProvider`. O adapter atual é `none`, e
todo processamento termina em `ERROR / PROVIDER_NOT_CONFIGURED`.

Já existe um serviço legado de comparação facial. Ele usa face-api.js 1.7.15, uma
biblioteca arquivada desde fevereiro de 2025, sobre Node 18, sem liveness, e com
thresholds divergentes: 0,60 no serviço e 0,5 no sistema que o chama.

Restrições:
- licença que permita uso comercial;
- preferência por self-hosted (LGPD);
- apenas CPU na infraestrutura atual;
- verificação 1:1, sem 1:N.

## Opções avaliadas

| Opção | Prós | Contras |
|---|---|---|
| **OpenCV YuNet + SFace** | MIT/Apache-2.0; self-hosted; só CPU; 1 dependência; EER 1,47% no LFW; 0 FTA; pipeline em ~27 ms | Sem liveness; procedência dos dados de treino do SFace não verificada; YuNet não é thread-safe; detecta rostos de fundo |
| dlib ResNet (mesmos pesos do serviço legado) | Modelos 5 pts e ResNet em domínio público; madura | EER 1,81%; ~157 ms; um único núcleo; HOG falha com rosto pequeno e oclusão; modelo de 68 pts proíbe uso comercial |
| Reaproveitar o serviço legado (face-api.js) | Já está em uso | Biblioteca arquivada, Node 18 sem suporte, 14% de FTA e FRR de 24% com o threshold do sistema que o chama (fold 1 do LFW), 525 MB RSS, sem liveness |
| InsightFace (buffalo_l) | Estado da arte, 512-d | Modelos pré-treinados só para uso não comercial. Excluída |
| DeepFace | Muitos modelos | Wrapper; herda a licença de cada modelo; dependências pesadas |
| MiniFASNet (liveness passivo) | Apache-2.0, ~25–34 ms | Aceitou um vídeo de foto estática como REAL (9 de 9); marcou como SPOOF 72–74% das imagens degradadas; sem certificação |
| AWS Rekognition Face Liveness / Azure Face Liveness | PAD iBeta nível 1 e 2 (ISO/IEC 30107-3) segundo a documentação oficial; SDK pronto | SaaS: biometria processada por terceiro; SDK obrigatório no front; custo por check (AWS: US$ 0,015); muda o contrato da API |
| Liveness ativo próprio (MediaPipe + nonce) | Self-hosted, controle total | Desenvolvimento e avaliação independente necessários; ainda não existe |

## Resultados do POC

| | SFace | dlib | serviço legado¹ |
|---|---|---|---|
| EER (LFW, 6.000 pares) | 1,47% | 1,81% | — |
| FRR com FAR ≤ 0,1% | 2,13% (thr 0,3443) | 9,46% (thr 0,5439) | — |
| Falha de detecção | 0 | 35 imagens | 14% dos pares |
| Pipeline (média, 100 execuções) | ~27 ms | ~157 ms | ~289 ms |
| Memória após carga | ~150 MB | ~206 MB | 525 MB |
| Cenários: mesma pessoa / pessoa diferente (50 cada) | 50/50 corretos | 48 MATCH / 2 falsos aceites | — |

¹ Fold 1 (600 pares), mesma configuração do serviço legado.

- **Liveness:** pessoa real, foto impressa, tela e replay de vídeo ficaram
  **NOT TESTED**, por falta de capturas autorizadas.
- **Encaixe:** o protótipo do provider rodou no `analyze_capture` do core sem
  alterá-lo.

## Riscos

1. **Liveness:** sem PAD avaliado, qualquer aprovação fica exposta a foto,
   tela, replay e injeção.
2. **Representatividade:** o LFW não reflete selfies, documentos nem a
   demografia do público. O threshold não está calibrado e pode haver viés não medido.
3. **Procedência dos dados de treino** do SFace e do YuNet não foi auditada
   (a licença do modelo é Apache/MIT).
4. **Concorrência:** instância de YuNet compartilhada causa segfault. É preciso
   uma instância por thread ou worker.
5. **Contrato:** o `FaceDetectionResult` sem landmarks obriga a detectar 4 vezes
   por captura. Liveness ativo ou de fornecedor exige mudar a API.
6. **LGPD:** um PAD em SaaS implica transferir biometria a terceiro (DPA,
   região, retenção).
7. **Reaproveitamento do serviço legado:** o risco operacional é alto (biblioteca
   arquivada e runtime EOL).

## Decisão

1. **Primeiro provider a testar:** `OpenCV YuNet 2023mar + SFace 2021dec`
   (`BIOMETRIC_MODEL=opencv-sface`, `BIOMETRIC_MODEL_VERSION=2021dec`), para
   detecção, embedding e verificação. Nesta etapa ele **não vai para produção**.
   O próximo passo é o adapter em `app/infrastructure/biometric/`, com a
   calibração.
2. **Threshold:** **PENDING CALIBRATION.** O candidato do LFW (0,3443 com FAR
   0,1%) é só ponto de partida e não foi colocado na `FaceMatchPolicy`.
3. **Liveness:** **PENDING DECISION** entre um PAD certificado de fornecedor
   (AWS/Azure) e um liveness ativo próprio. O MiniFASNet **não** será usado como
   PAD único.
4. **Qualidade:** **PENDING DECISION.** Heurísticas de tamanho, nitidez e brilho
   por enquanto; os limites precisam de calibração.
5. **Serviço legado:** não será reaproveitado nem alterado.

## Consequências

- A partir do ADR-006, detector, qualidade, embedder, comparator e liveness
  são portas independentes. O SFace está registrado como `BIOMETRIC_EMBEDDER`
  e `BIOMETRIC_COMPARATOR` (`opencv-sface`), e o YuNet como `BIOMETRIC_DETECTOR`
  (`opencv-yunet`) — implementados na Fase 2, ainda **fora de produção**: sem
  calibração e sem liveness. A Fase 4 reproduziu o POC pelos adapters do
  serviço (EER 1,47% no LFW, [evaluation-lfw.md](../evaluation-lfw.md)).

- O adapter real exigirá:
  - uma instância por worker;
  - inferência fora do event loop;
  - gravação de `model_name`/`model_version` com o template, já suportada;
  - de preferência, a evolução do `FaceDetectionResult` para evitar redetecção.
- Antes de ativar em produção, é preciso:
  - montar um dataset autorizado (verificação e PAD);
  - definir a FAR alvo;
  - calibrar a `FaceMatchPolicy` versionada (FAR/FRR por subgrupo);
  - escolher o PAD.
- Se o PAD escolhido for de fornecedor, a API ganha sessões de liveness e o front
  precisa integrar o SDK.
- Trocar de motor depois continua isolado no adapter. Templates de modelos
  diferentes não são comparáveis e vão gerar `MODEL_MISMATCH`, o que exige
  recadastro (`PUT /subjects/{id}/face`, ADR-008).
