"""Validação da Sprint R01 — Dashboard API Blueprint.

Executar na raiz do projeto:
    python utils/validar_sprint_R01.py
"""
from pathlib import Path
import ast
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
DASH = ROOT / "routes" / "dashboard_routes.py"
SERVICE = ROOT / "services" / "dashboard_service.py"

ROTAS = [
    "/api/dashboard_executivo_integrado",
    "/api/dashboard_inteligente_erp",
]


def ler(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Arquivo não encontrado: {path}")
    return path.read_text(encoding="utf-8")


def coletar_rotas(path: Path):
    tree = ast.parse(ler(path), filename=str(path))
    rotas = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route":
                    if dec.args and isinstance(dec.args[0], ast.Constant):
                        rotas.append((dec.args[0].value, node.name))
    return rotas


def main() -> int:
    for arquivo in [APP, DASH, SERVICE]:
        py_compile.compile(str(arquivo), doraise=True)

    app_text = ler(APP)
    dash_text = ler(DASH)

    for rota in ROTAS:
        if rota in app_text:
            raise AssertionError(f"Rota ainda aparece no app.py: {rota}")
        if rota not in dash_text:
            raise AssertionError(f"Rota não encontrada em dashboard_routes.py: {rota}")

    dash_rotas = [r for r, _ in coletar_rotas(DASH)]
    for rota in ROTAS:
        qtd = dash_rotas.count(rota)
        if qtd != 1:
            raise AssertionError(f"Rota {rota} deveria aparecer 1 vez em dashboard_routes.py, apareceu {qtd}")

    if "from routes.dashboard_routes import dashboard_bp" not in app_text:
        raise AssertionError("app.py não importa dashboard_bp")
    if "app.register_blueprint(dashboard_bp)" not in app_text:
        raise AssertionError("app.py não registra dashboard_bp")

    print("OK - Sprint R01 validada: APIs do Dashboard migradas para dashboard_bp sem duplicidade.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERRO - Validação falhou: {exc}")
        raise SystemExit(1)
