#!/bin/sh
# Gera a configuração de identidade do S3 a partir de variáveis de ambiente
# (nada de credenciais em arquivos versionados) e inicia o SeaweedFS.
set -eu
umask 077
CONFIG=/tmp/s3-identities.json
cat > "$CONFIG" <<JSON
{
  "identities": [
    {
      "name": "facial-validation",
      "credentials": [{"accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}"}],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
exec weed server -dir=/data -ip.bind=0.0.0.0 -master.volumeSizeLimitMB=256 -volume.max=0 \
  -s3 -s3.port=8333 -s3.config="$CONFIG"
