# ADR-004 — Storage de capturas e templates

- **Status:** ACCEPTED para a interface; PENDING DECISION para produção
- **Data:** 2026-09-30

## Decisão

- Capturas (imagens enviadas) vão para object storage **S3 compatível**, via
  porta `CaptureStorage` e adapter `S3CaptureStorage` (boto3, path-style,
  SigV4). Bucket privado; acesso temporário por URL pré-assinada.
- Chaves: `tenants/{tenant_uuid}/subjects/{subject_uuid}/{registrations|verifications}/{uuid}`.
  Somente UUIDs internos (o `external_id` pode ser dado pessoal).
- O template biométrico **não** vai para o storage: fica em
  `face_registrations.template` (BYTEA), cifrado com AES-256-GCM.

### Storage local: SeaweedFS em vez de MinIO

O pedido sugeria MinIO. Durante a auditoria (2026-09-30), `minio/minio` no
Docker Hub respondeu `access denied` e `quay.io/minio/minio` não tinha
manifesto `latest`. Para não depender de imagem indisponível, o ambiente local
usa **SeaweedFS 4.48** (`chrislusf/seaweedfs`, Apache-2.0) com gateway S3. Como
a aplicação só fala S3, trocar por MinIO, AWS S3 ou outro compatível é mudança
de configuração (`S3_ENDPOINT`), sem alteração de código.

### Embedding em PostgreSQL, sem pgvector

O requisito atual é 1:1: busca-se a referência do subject pelo id, sem busca
por similaridade. `pgvector` só será avaliado se surgir requisito 1:N.

## PENDING DECISION

- Provedor de object storage em produção e criptografia server-side (SSE-KMS).
- Gestão da chave do template: hoje `TEMPLATE_ENCRYPTION_KEY` (env). Alvo:
  KMS/Vault com rotação (o formato já carrega 1 byte de versão da chave).
- **Retenção**: por quanto tempo manter capturas de cadastro e de validação,
  templates e eventos; exclusão a pedido do titular (LGPD). Desde a Fase 3 a
  captura tem retenção configurável (`CAPTURE_RETENTION=KEEP|DELETE_AFTER_PROCESSING`,
  padrão `KEEP`); **qual política vale em produção continua PENDING**. Templates
  e eventos seguem sem expurgo, e capturas de itens presos em `CREATED` não são
  alcançadas (dependem da varredura do ADR-005).
- Tipo definitivo do campo de embedding após a escolha do motor (ADR-003).
