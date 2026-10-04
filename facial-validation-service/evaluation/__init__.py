"""Avaliação biométrica fora do runtime do serviço (Fase 4 do plano).

Não é importado por app/ e não entra na imagem de runtime (Dockerfile copia só
app/ e migrations/). Roda no container de desenvolvimento: python -m evaluation.
"""
