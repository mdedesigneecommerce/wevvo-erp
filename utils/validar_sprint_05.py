"""Validador da Sprint 05 — Categorias em Blueprint."""

from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROTAS = ROOT / "routes" / "categorias_routes.py"

ROTAS_ESPERADAS = [
    'route("/categorias_admin")',
    'route("/categorias_select")',
    'route("/salvar_categoria", methods=["POST"])',
    'route("/excluir_categoria/<int:categoria_id>", methods=["DELETE"])',
    'route("/vincular_receita_categoria", methods=["POST"])',
    'route("/imprimir_categoria/<int:categoria_id>")',
]

APP_ROTAS_PROIBIDAS = [
    '@app.route("/categorias_admin")',
    '@app.route("/categorias_select")',
    '@app.route("/salvar_categoria"',
    '@app.route("/excluir_categoria',
    '@app.route("/vincular_receita_categoria"',
    '@app.route("/imprimir_categoria',
]


def falhar(msg):
    raise SystemExit(f"[ERRO] {msg}")


def main():
    if not APP.exists():
        falhar("app.py não encontrado.")
    if not ROTAS.exists():
        falhar("routes/categorias_routes.py não encontrado.")

    app_txt = APP.read_text(encoding="utf-8")
    rotas_txt = ROTAS.read_text(encoding="utf-8")

    if "from routes.categorias_routes import categorias_bp" not in app_txt:
        falhar("app.py não importa categorias_bp.")
    if "app.register_blueprint(categorias_bp)" not in app_txt:
        falhar("app.py não registra categorias_bp.")
    if "categorias_bp = Blueprint" not in rotas_txt:
        falhar("categorias_routes.py não define categorias_bp.")

    for proibida in APP_ROTAS_PROIBIDAS:
        if proibida in app_txt:
            falhar(f"Rota de categoria ainda existe em app.py: {proibida}")

    for rota in ROTAS_ESPERADAS:
        if rota not in rotas_txt:
            falhar(f"Rota esperada não encontrada em categorias_routes.py: {rota}")

    py_compile.compile(str(APP), doraise=True)
    py_compile.compile(str(ROTAS), doraise=True)

    print("[OK] Sprint 05 validada: categorias migradas para Blueprint.")


if __name__ == "__main__":
    main()
