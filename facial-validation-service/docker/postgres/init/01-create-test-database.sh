#!/bin/sh
# Executado somente na primeira inicialização do volume: cria o banco de testes.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE DATABASE "${POSTGRES_TEST_DB}" OWNER "${POSTGRES_USER}";
SQL
