"""Validação técnica da Sprint 03.

Executar na raiz do projeto:
    python utils/validar_sprint_03.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BLUEPRINTS_ESPERADOS = {
    "dashboard": "routes.dashboard_routes",
    "estoque": "routes.estoque_routes",
    "financeiro": "routes.financeiro_routes",
    "compras": "routes.compras_routes",
    "producao": "routes.producao_routes",
    "qualidade": "routes.qualidade_routes",
    "api": "routes.api_routes",
}

ROTAS_CRITICAS = {
    "/",
    "/dashboard_gerencial",
    "/dashboard_financeiro",
    "/dashboard_inteligencia",
    "/estoque_ingredientes",
    "/compras_status",
    "/financeiro_status",
    "/qualidade_status",
}


def validar_blueprints() -> list[str]:
    erros: list[str] = []
    for nome, modulo_nome in BLUEPRINTS_ESPERADOS.items():
        modulo = importlib.import_module(modulo_nome)
        atributo = f"{nome}_bp"
        blueprint = getattr(modulo, atributo, None)
        if blueprint is None:
            erros.append(f"Blueprint ausente: {atributo} em {modulo_nome}")
        elif getattr(blueprint, "name", None) != nome:
            erros.append(f"Blueprint {atributo} com nome incorreto: {getattr(blueprint, 'name', None)}")
    return erros


def validar_app() -> list[str]:
    erros: list[str] = []
    modulo_app = importlib.import_module("app")
    app = getattr(modulo_app, "app", None)
    if app is None:
        return ["Objeto Flask 'app' não encontrado em app.py"]

    rotas = {regra.rule for regra in app.url_map.iter_rules()}
    for rota in sorted(ROTAS_CRITICAS):
        if rota not in rotas:
            erros.append(f"Rota crítica ausente: {rota}")
    return erros


def main() -> int:
    erros = []
    erros.extend(validar_blueprints())
    erros.extend(validar_app())

    if erros:
        print("ERRO: validação da Sprint 03 encontrou problemas:")
        for erro in erros:
            print(f"- {erro}")
        return 1

    print("OK: Sprint 03 validada com sucesso.")
    print("Blueprints criados e rotas críticas preservadas no app.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
