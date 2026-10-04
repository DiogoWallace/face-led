# Avaliação de liveness (Presentation Attack Detection)

- **Data:** 2026-09-30
- **Status:** DECIDIDO em 2026-10-01: opção (b), liveness ativo próprio —
  [ADR-010](decisions/ADR-010-active-liveness.md). APCER/BPCER continuam não medidos.

Reconhecimento facial responde "é a mesma pessoa?". Liveness responde "há uma
pessoa real diante da câmera agora?". Nenhuma biblioteca de reconhecimento
avaliada (OpenCV SFace, dlib, InsightFace, face-api.js) entrega a segunda
resposta. O serviço legado também não tem liveness.

## 1. Passivo × ativo

```
Passivo:  captura (1 imagem ou poucos quadros) → análise → liveness
Ativo:    challenge (nonce do servidor) → vídeo/quadros → análise → liveness
```

| Critério | Passivo (imagem única) | Ativo (challenge + vídeo) |
|---|---|---|
| UX | Melhor: uma selfie, sem instruções | Pior: seguir instruções (virar, piscar, aproximar); 5 a 20 s nos serviços pesquisados |
| Complexidade | Baixa no servidor | Alta: SDK ou front de captura, sessão, nonce, sincronização, análise temporal |
| Segurança | Depende só do modelo. Sem certificação, não há como afirmar a proteção | Maior, se o desafio for imprevisível e verificado no servidor |
| Processamento | 1 inferência (~33 ms medidos com MiniFASNet) | N quadros × (detecção + landmarks/PAD); mais banda e armazenamento |
| Replay | **Vulnerável:** a mesma imagem pode ser reenviada ou injetada | Reduzido por nonce e janela de tempo, mas não eliminado sem proteção da câmera |
| Foto impressa | Depende do modelo (não testado aqui) | Desafio de movimento dificulta; a foto não pisca nem gira |
| Foto em tela | Depende do modelo (não testado aqui) | Idem; reflexos e moiré ajudam os modelos |
| Vídeo reproduzido | Fraco | Só resiste se o desafio for aleatório por sessão |
| Injeção digital (câmera virtual) | Não resolvido pelo modelo | Não resolvido só pelo modelo; exige SDK com atestação e RASP |
| Precisa de vídeo | Não | Sim (ou sequência de quadros) |
| Impacto na API | Nenhum no contrato atual (`image`) | **Muda o contrato** (abaixo) |

### Impacto do liveness ativo no contrato

- **API:** um passo novo antes da captura, por exemplo `POST /liveness-sessions`
  devolvendo `session_id`, desafio e expiração. As rotas de cadastro e validação
  passam a receber `liveness_session_id` ou o vídeo.
- **Domínio:** `LivenessSession` já existe. Ganharia desafio, nonce, expiração e
  estados próprios.
- **Porta:** `check_liveness(capture: CaptureData)` passaria a receber uma
  evidência de sessão (vídeo, quadros ou resultado do SDK do fornecedor).
- **Storage e retenção:** vídeo é dado biométrico mais volumoso e sensível.
- **SDKs de fornecedor:** AWS (Amplify FaceLivenessDetector) e Azure (Vision
  Face SDK ou Quick Link) controlam a captura no front. O serviço orquestra a
  sessão e consulta o resultado.

## 2. Opções

| Opção | Tipo | Evidência de PAD | Execução | Custo | Observações |
|---|---|---|---|---|---|
| MiniFASNet (Silent-Face-Anti-Spoofing) | Passivo RGB | Nenhuma certificação. O próprio README avisa que a robustez varia com a câmera e o cenário. O modelo de alta precisão é comercial e não foi liberado | Self-hosted CPU | Grátis (Apache-2.0) | **Medido no POC** (seção 3) |
| DeepFace `anti_spoofing=True` | Passivo | Modelo de terceiros com licença própria (o README pede verificação) | Self-hosted | Grátis ou a verificar | Não medido |
| Ativo in-house (MediaPipe Face Landmarker + nonce) | Ativo | Nenhuma até teste independente | Self-hosted | Desenvolvimento | 478 landmarks e blendshapes como `eyeBlinkLeft` permitem desafios de piscar e virar. Não implementado |
| AWS Rekognition Face Liveness | Ativo (oval + cores) | iBeta PAD nível 1 e 2, ISO/IEC 30107-3, segundo o FAQ oficial | SaaS; região São Paulo disponível | US$ 0,015/check até 500 mil (depois 0,0125 e 0,010) | Exige Amplify SDK; devolve score de 0 a 100, imagem de referência e imagens de auditoria |
| Azure AI Face Liveness | Passivo ou passivo-ativo | iBeta PAD nível 1 e 2, ISO/IEC 30107-3, segundo a documentação oficial | SaaS | Não levantado | Exige SDK (iOS/Android/Web) ou Quick Link. A Microsoft recomenda mobile, pois o web é mais exposto |
| Fornecedores dedicados (FaceTec, iProov, Unico etc.) | Ativo e/ou passivo | Declaram certificações; exigem contrato para avaliar | SaaS ou SDK | Sob contrato | Não avaliados |

As certificações acima são declaração dos fornecedores nas fontes oficiais.
Não foram testadas neste POC.

## 3. Resultados do POC (MiniFASNetV2 + V1SE)

Não havia capturas autorizadas de pessoa real, foto impressa, foto em tela nem
replay de vídeo. **Essas quatro categorias ficaram NOT TESTED.** O POC mediu
apenas o comportamento do modelo nestes casos:

| Conjunto (n=50; casos com 1 rosto) | REAL | SPOOF | Score "real" médio |
|---|---|---|---|
| Fotos LFW originais | 39 | 1 | 0,934 |
| LFW escurecido (simulação) | 37 | 3 | 0,861 |
| LFW clareado (simulação) | 27 | 12 | 0,672 |
| LFW "outra câmera" (simulação) | 11 | 28 | 0,278 |
| LFW com blur (simulação) | 11 | 31 | 0,298 |
| **Vídeo sintético de foto estática** (10 vídeos × 30 quadros) | **9 de 9 vídeos** | 0 | 0,78 a 1,00 |

O que isso permite afirmar:
- **O modelo aceitou como REAL, em todos os quadros, um vídeo montado a partir
  de uma única foto.** Isso equivale a um replay ou injeção digital. Liveness
  passivo quadro a quadro não detecta esse ataque.
- A degradação comum de uma captura genuína (outra câmera, compressão, blur)
  virou SPOOF em 72% ("outra câmera", 28 de 39) e 74% (blur, 31 de 42) dos
  casos com um rosto. Isso indica alto risco de rejeitar
  pessoas reais (BPCER), sem calibração possível sem dados reais.
- **Nada** permite afirmar proteção contra foto impressa, tela ou vídeo físico.

## 4. Recomendação

1. **Não usar o MiniFASNet como PAD único em produção.** Pode ficar como
   sinal auxiliar, se calibrado com dados reais.
2. Decidir entre dois caminhos:
   - **(a) PAD certificado de fornecedor** (AWS Face Liveness em `sa-east-1`
     ou Azure), com a verificação 1:1 feita no nosso serviço (SFace). Envolve
     transferência de biometria a terceiro (LGPD, contrato, DPA) e SDK no front.
   - **(b) Liveness ativo próprio** (desafio com nonce do servidor + MediaPipe
     + passivo auxiliar). Exige desenvolvimento, SDK de captura próprio e
     avaliação independente (idealmente laboratório ISO/IEC 30107-3).
3. Em qualquer caminho, montar o **dataset de PAD autorizado**
   (`poc/dataset/liveness/{real,print,screen,video_replay}`) e medir
   APCER e BPCER antes de ativar o provider.
4. Definir se o cadastro e a validação terão o mesmo nível de liveness.
