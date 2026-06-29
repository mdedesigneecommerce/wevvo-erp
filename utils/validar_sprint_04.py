"""Validação estrutural da Sprint 04.

Executar na raiz do projeto:
    python utils/validar_sprint_04.py
"""

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
DASHBOARD = ROOT / "routes" / "dashboard_routes.py"

ROTAS_ESPERADAS = {
    "/dashboard_gerencial",
    "/dashboard_gerencial_fase3",
    "/dashboard_financeiro",
    "/dashboard_inteligencia",
}


def rotas_do_arquivo(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rotas = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route":
                    if dec.args and isinstance(dec.args[0], ast.Constant):
                        rotas.add(dec.args[0].value)
    return rotas


def main():
    erros = []
    if not APP.exists():
        erros.append("app.py não encontrado")
    if not DASHBOARD.exists():
        erros.append("routes/dashboard_routes.py não encontrado")

    if not erros:
        app_text = APP.read_text(encoding="utf-8")
        dash_text = DASHBOARD.read_text(encoding="utf-8")
        if "app.register_blueprint(dashboard_bp)" not in app_text:
            erros.append("dashboard_bp não está registrado no app.py")
        if "dashboard_bp = Blueprint" not in dash_text:
            erros.append("dashboard_bp não foi declarado no arquivo de rotas")

        rotas_app = rotas_do_arquivo(APP)
        rotas_dash = rotas_do_arquivo(DASHBOARD)

        faltando = ROTAS_ESPERADAS - rotas_dash
        duplicadas = ROTAS_ESPERADAS & rotas_app
        if faltando:
            erros.append(f"Rotas esperadas ausentes em dashboard_routes.py: {sorted(faltando)}")
        if duplicadas:
            erros.append(f"Rotas de dashboard ainda duplicadas no app.py: {sorted(duplicadas)}")

    if erros:
        print("Sprint 04 inválida:")
        for erro in erros:
            print(f"- {erro}")
        sys.exit(1)

    print("Sprint 04 validada com sucesso.")
    print("Dashboard migrado para Blueprint sem duplicidade de rotas.")


if __name__ == "__main__":
    main()
