# POC de avaliação biométrica - isolado do core (não é usado por api/worker).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

ARG UID=1000
ARG GID=1000
RUN groupadd --gid "${GID}" poc && useradd --uid "${UID}" --gid poc --create-home poc \
    && apt-get update && apt-get install -y --no-install-recommends curl bzip2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.* \
    && pip install -r /tmp/requirements.txt

WORKDIR /poc
USER poc
CMD ["bash"]
