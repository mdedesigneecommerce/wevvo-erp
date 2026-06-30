"""Validação técnica da Sprint 09 do Wevvo ERP/PDV.

Executar na raiz do projeto:
    python utils/validar_sprint_09.py
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "app.py",
    "config.py",
    "templates/index.html",
    "routes/__init__.py",
    "routes/dashboard_routes.py",
    "routes/categorias_routes.py",
    "routes/configuracoes_routes.py",
    "routes/usuarios_routes.py",
    "routes/home_routes.py",
    "services/__init__.py",
    "services/core.py",
    "docs/SPRINT_09_VALIDACAO_ARQUITETURA.md",
]

PYTHON_FILES_TO_COMPILE = [
    "app.py",
    "config.py",
    "routes/dashboard_routes.py",
    "routes/categorias_routes.py",
    "routes/configuracoes_routes.py",
    "routes/usuarios_routes.py",
    "routes/home_routes.py",
    "utils/validar_sprint_09.py",
]


def fail(message: str) -> None:
    print(f"ERRO: {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"OK: {message}")


def assert_required_paths() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]
    if missing:
        fail("Arquivos/pastas ausentes: " + ", ".join(missing))
    ok("estrutura essencial encontrada")


def compile_python_files() -> None:
    for rel_path in PYTHON_FILES_TO_COMPILE:
        path = ROOT / rel_path
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        try:
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            fail(f"erro de sintaxe em {rel_path}: linha {exc.lineno}: {exc.msg}")
    ok("arquivos Python compilam sem erro de sintaxe")


def collect_app_routes() -> list[tuple[str, tuple[str, ...], str]]:
    app_path = ROOT / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    routes: list[tuple[str, tuple[str, ...], str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (isinstance(func, ast.Attribute) and func.attr == "route"):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            rota = str(dec.args[0].value)
            methods = ("GET",)
            for kw in dec.keywords:
                if kw.arg == "methods":
                    try:
                        parsed = ast.literal_eval(kw.value)
                        methods = tuple(sorted(str(m).upper() for m in parsed))
                    except Exception:
                        methods = ("GET",)
            routes.append((rota, methods, node.name))
    return routes


def assert_no_duplicate_app_routes() -> None:
    routes = collect_app_routes()
    counter = Counter((path, methods) for path, methods, _ in routes)
    duplicated = [(path, methods, qty) for (path, methods), qty in counter.items() if qty > 1]
    if duplicated:
        details = "; ".join(f"{path} {list(methods)} x{qty}" for path, methods, qty in duplicated)
        fail("rotas duplicadas no app.py: " + details)
    ok(f"{len(routes)} rotas do app.py sem duplicidade exata")


def assert_gitignore_protects_local_files() -> None:
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        fail(".gitignore não encontrado")
    content = gitignore.read_text(encoding="utf-8")
    required_patterns = [".venv/", "__pycache__/", "sistema.db", "*.py[cod]"]
    missing = [pattern for pattern in required_patterns if pattern not in content]
    if missing:
        fail(".gitignore sem padrões obrigatórios: " + ", ".join(missing))
    ok(".gitignore protege ambiente local, cache e banco")


def main() -> None:
    print("Validação Sprint 09 — Wevvo ERP/PDV")
    assert_required_paths()
    compile_python_files()
    assert_no_duplicate_app_routes()
    assert_gitignore_protects_local_files()
    print("\nSprint 09 validada com sucesso.")


if __name__ == "__main__":
    main()
