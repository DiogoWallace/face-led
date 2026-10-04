"""Regras de dependência entre camadas, verificadas pelos imports reais do código."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Bibliotecas de motor/fornecedor e de infraestrutura que domínio e aplicação não podem importar.
FORBIDDEN_IN_CORE = {
    "cv2",
    "numpy",
    "onnxruntime",
    "torch",
    "dlib",
    "insightface",
    "mediapipe",
    "deepface",
    "face_recognition",
    "boto3",
    "botocore",
    "azure",
    "sqlalchemy",
    "fastapi",
    "starlette",
    "redis",
    "arq",
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_core_does_not_import_engines_or_infrastructure_libs(layer):
    offenders = {
        str(p.relative_to(APP)): sorted(imported_roots(p) & FORBIDDEN_IN_CORE)
        for p in (APP / layer).rglob("*.py")
        if imported_roots(p) & FORBIDDEN_IN_CORE
    }
    assert offenders == {}


def test_domain_does_not_depend_on_outer_layers():
    offenders = {
        str(p.relative_to(APP)): sorted(
            m
            for m in imported_modules(p)
            if m.startswith(("app.application", "app.infrastructure", "app.interfaces"))
        )
        for p in (APP / "domain").rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_application_does_not_depend_on_infrastructure_or_interfaces():
    offenders = {
        str(p.relative_to(APP)): sorted(
            m
            for m in imported_modules(p)
            if m.startswith(("app.infrastructure", "app.interfaces", "app.container"))
        )
        for p in (APP / "application").rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}
