"""Retenção da captura (selfie) depois do processamento.

PENDING LEGAL/BUSINESS DECISION: qual política vale em produção, por quanto
tempo e com que base legal. O padrão `KEEP` só preserva o comportamento que já
existia; não é uma decisão de retenção.

- KEEP: a captura fica no storage (comportamento original).
- DELETE_AFTER_PROCESSING: ao terminar o processamento (APPROVED, REJECTED,
  ERROR ou EXPIRED), a captura é apagada do storage e a chave é removida do
  registro. O template cifrado do cadastro NÃO é afetado.
"""

from enum import StrEnum


class CaptureRetention(StrEnum):
    KEEP = "KEEP"
    DELETE_AFTER_PROCESSING = "DELETE_AFTER_PROCESSING"
