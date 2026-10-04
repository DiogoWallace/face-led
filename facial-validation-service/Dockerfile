# syntax=docker/dockerfile:1
# Python 3.12: mesma série do WSL (3.12.3) e a de maior compatibilidade atual com
# bibliotecas de visão/ML candidatas a motor biométrico (ver ADR-001).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv/app

ARG UID=1000
ARG GID=1000
RUN groupadd --gid "${GID}" app \
    && useradd --uid "${UID}" --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /srv/app

COPY requirements/runtime.lock requirements/runtime.lock
RUN pip install --require-hashes -r requirements/runtime.lock

# ---------- desenvolvimento local (código montado via volume) ----------
FROM base AS dev
COPY requirements/dev.lock requirements/dev.lock
RUN pip install --require-hashes -r requirements/dev.lock
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]

# ---------- imagem de execução (sem dependências de teste) ----------
FROM base AS runtime
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini pyproject.toml ./
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
