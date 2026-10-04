# POC — avaliação do motor biométrico

POC isolado do core: imagem Docker própria (`facial-validation/poc:local`),
containers descartáveis com `--network none` (exceto o download inicial), sem
portas e sem acesso ao banco/fila/storage do serviço. Nada em `app/` importa o POC.

Resultados e análise: [`docs/biometric-poc-results.md`](../docs/biometric-poc-results.md).

## Estrutura

```
poc/
├── Dockerfile, requirements.txt   imagem do POC (OpenCV, dlib, torch CPU)
├── scripts/
│   ├── setup_assets.py            baixa modelos + LFW, registra licença e SHA-256 (results/assets.json)
│   ├── engines.py                 candidatos: OpenCV YuNet+SFace, dlib ResNet, MiniFASNet
│   ├── prepare_dataset.py         monta os cenários (LFW + derivações sintéticas)
│   ├── evaluate_lfw.py            protocolo LFW View 2 (6000 pares): EER, FAR/FRR, 10-fold
│   ├── evaluate_scenarios.py      cenários do item 7 (input, resultado, score, tempo, CPU, memória)
│   ├── liveness_eval.py           liveness passivo em imagem e vídeo
│   ├── benchmark.py               1/10/100 execuções por etapa + concorrência
│   ├── thread_safety_check.py     instância YuNet compartilhada entre threads
│   └── provider_fit_check.py      protótipo de BiometricProvider no pipeline real do core
├── adapter_prototype/             protótipo do provider (NÃO registrado no factory)
├── dataset/
│   ├── same_person/  different_person/  quality/     gerados a partir do LFW (não versionados)
│   ├── liveness/{real,print,screen,video_replay}/     VAZIOS: exigem capturas autorizadas
│   └── _raw/                                          LFW original (não versionado)
├── models/  vendor/                baixados por setup_assets.py (não versionados)
└── results/                        métricas (JSON/CSV) versionadas; sem imagens
```

## Executar

```bash
cd facial-validation-service/poc
docker build -t facial-validation/poc:local .

# única etapa com rede: modelos + LFW
docker run --rm -u $(id -u):$(id -g) -v $PWD:/poc -w /poc/scripts facial-validation/poc:local python setup_assets.py

# demais etapas sem rede
POC="docker run --rm --network none -u $(id -u):$(id -g) -v $PWD:/poc -w /poc/scripts facial-validation/poc:local"
$POC python prepare_dataset.py
$POC python evaluate_lfw.py opencv && $POC python evaluate_lfw.py dlib
$POC python evaluate_scenarios.py opencv && $POC python evaluate_scenarios.py dlib
$POC python liveness_eval.py
$POC python benchmark.py
$POC python thread_safety_check.py      # termina com segfault (resultado esperado, ver docs)

# protótipo no pipeline do core (projeto montado em /srv/app, somente leitura)
docker run --rm --network none -u $(id -u):$(id -g) -v $PWD/..:/srv/app:ro -v $PWD:/poc \
  -w /poc/scripts facial-validation/poc:local python provider_fit_check.py

```

## Datasets

| Nome | Origem | Licença | Finalidade | Limitações |
|---|---|---|---|---|
| LFW (Labeled Faces in the Wild), 13.233 imagens, 5.749 pessoas, `pairs.txt` View 2 | vis-www.cs.umass.edu/lfw; baixado do mirror figshare usado pelo scikit-learn | Sem licença explícita; distribuído para pesquisa. Imagens de pessoas públicas coletadas de notícias | Medir FAR/FRR 1:1 e servir de base para cenários derivados | Não representa selfies, documentos, câmeras de celular nem a demografia do público; rostos de fundo; os mantenedores alertam que o benchmark não comprova adequação a uso comercial. **Uso restrito a avaliação interna, nunca em produção nem para treino** |
| Derivações do LFW (iluminação, câmera, blur, rosto pequeno, oclusão, múltiplos rostos) | `prepare_dataset.py` | idem LFW | Cenários do item 7 | São **simulações**: não substituem câmera, iluminação ou oclusão reais |
| Sem rosto | ruído sintético | — | Cenário "sem rosto" | Sintético |
| Liveness (real / impressa / tela / replay) | **não existe** | — | Testes do item 8 | Exige sessão de captura com voluntários e termo de consentimento |

Nenhuma imagem pessoal real (de colaboradores ou clientes) foi usada. Imagens,
modelos e o dataset bruto estão no `.gitignore`.

### Como coletar o dataset de liveness (pendente)

Com autorização formal e consentimento dos participantes, gravar pelo menos:
`real/` (selfie e vídeo de 3–5 s ao vivo), `print/` (foto impressa diante da
câmera), `screen/` (foto exibida em celular/monitor) e `video_replay/` (vídeo da
pessoa reproduzido em tela), variando aparelho, iluminação e distância.
Depois executar `liveness_eval.py`.
