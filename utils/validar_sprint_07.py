"""Validação da Sprint 07 — Usuários em Blueprint."""

from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROUTE = ROOT / "routes" / "usuarios_routes.py"
DOC = ROOT / "docs" / "SPRINT_07_USUARIOS_BLUEPRINT.md"

checks = []

def check(cond, msg):
    checks.append((cond, msg))

app_text = APP.read_text(encoding="utf-8")
route_text = ROUTE.read_text(encoding="utf-8") if ROUTE.exists() else ""

check(ROUTE.exists(), "routes/usuarios_routes.py existe")
check(DOC.exists(), "docs/SPRINT_07_USUARIOS_BLUEPRINT.md existe")
check("from routes.usuarios_routes import usuarios_bp" in app_text, "app.py importa usuarios_bp")
check("app.register_blueprint(usuarios_bp)" in app_text, "app.py registra usuarios_bp")

for endpoint in [
    "@usuarios_bp.route('/api/usuarios_sistema', methods=['GET'])",
    "@usuarios_bp.route('/api/usuarios_sistema', methods=['POST'])",
    "@usuarios_bp.route('/api/usuarios_sistema/<int:usuario_id>/status', methods=['POST'])",
    "@usuarios_bp.route('/api/login_sistema', methods=['POST'])",
]:
    check(endpoint in route_text, f"endpoint migrado: {endpoint}")

for old in [
    "@app.route('/api/usuarios_sistema', methods=['GET'])",
    "@app.route('/api/usuarios_sistema', methods=['POST'])",
    "@app.route('/api/usuarios_sistema/<int:usuario_id>/status', methods=['POST'])",
    "@app.route('/api/login_sistema', methods=['POST'])",
]:
    check(old not in app_text, f"endpoint removido do app.py: {old}")

try:
    ast.parse(app_text)
    check(True, "app.py possui sintaxe Python válida")
except SyntaxError as exc:
    check(False, f"app.py com erro de sintaxe: {exc}")

try:
    ast.parse(route_text)
    check(True, "usuarios_routes.py possui sintaxe Python válida")
except SyntaxError as exc:
    check(False, f"usuarios_routes.py com erro de sintaxe: {exc}")

falhas = [msg for ok, msg in checks if not ok]
for ok, msg in checks:
    print(("OK  " if ok else "ERRO") + " - " + msg)

if falhas:
    raise SystemExit("\nSprint 07 inválida. Corrija os itens acima.")

print("\nSprint 07 validada com sucesso.")
