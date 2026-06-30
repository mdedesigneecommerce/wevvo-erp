"""Validação técnica da Sprint 13 — Estoque em Blueprint."""

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROUTE = ROOT / "routes" / "estoque_routes.py"
DOC = ROOT / "docs" / "SPRINT_13_ESTOQUE_CONSULTA_BLUEPRINT.md"


def fail(msg):
    print(f"[ERRO] {msg}")
    sys.exit(1)


def ok(msg):
    print(f"[OK] {msg}")


for path in [APP, ROUTE, DOC]:
    if not path.exists():
        fail(f"Arquivo obrigatório ausente: {path.relative_to(ROOT)}")
    ok(f"Arquivo encontrado: {path.relative_to(ROOT)}")

app_text = APP.read_text(encoding="utf-8")
route_text = ROUTE.read_text(encoding="utf-8")

checks_app = [
    "from routes.estoque_routes import estoque_bp",
    "app.register_blueprint(estoque_bp)",
]
for item in checks_app:
    if item not in app_text:
        fail(f"app.py não contém: {item}")
    ok(f"app.py contém: {item}")

checks_route = [
    'estoque_bp = Blueprint("estoque", __name__)',
    '@estoque_bp.route("/estoque_ingredientes")',
    '@estoque_bp.route("/movimentacoes_estoque")',
    '@estoque_bp.route("/estoque_produto_acabado")',
    '@estoque_bp.route("/movimentacoes_produto_acabado")',
]
for item in checks_route:
    if item not in route_text:
        fail(f"routes/estoque_routes.py não contém: {item}")
    ok(f"routes/estoque_routes.py contém: {item}")

try:
    ast.parse(APP.read_text(encoding="utf-8"))
    ast.parse(ROUTE.read_text(encoding="utf-8"))
except SyntaxError as exc:
    fail(f"Erro de sintaxe: {exc}")

ok("Sintaxe Python validada")
ok("Sprint 13 validada com sucesso")
