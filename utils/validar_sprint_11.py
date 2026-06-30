from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROUTE = ROOT / "routes" / "ingredientes_routes.py"
DOC = ROOT / "docs" / "SPRINT_11_INGREDIENTES_CONSULTA_BLUEPRINT.md"

checks = []

def ok(cond, msg):
    checks.append((cond, msg))

app_text = APP.read_text(encoding="utf-8")
route_text = ROUTE.read_text(encoding="utf-8") if ROUTE.exists() else ""

ok(ROUTE.exists(), "routes/ingredientes_routes.py existe")
ok(DOC.exists(), "documentação da Sprint 11 existe")
ok("from routes.ingredientes_routes import ingredientes_bp" in app_text, "app.py importa ingredientes_bp")
ok("app.register_blueprint(ingredientes_bp)" in app_text, "app.py registra ingredientes_bp")
ok("ingredientes_bp = Blueprint" in route_text, "Blueprint de ingredientes declarado")
for endpoint in ["/ingredientes_precos", "/ingredientes_admin", "/ingrediente_admin/<int:ingrediente_id>"]:
    ok(endpoint in route_text, f"endpoint {endpoint} presente no blueprint")

for file_path in [APP, ROUTE]:
    try:
        ast.parse(file_path.read_text(encoding="utf-8"))
        ok(True, f"{file_path.name} sem erro de sintaxe Python")
    except SyntaxError as exc:
        ok(False, f"{file_path.name} com erro de sintaxe: {exc}")

print("Validação Sprint 11 — Ingredientes Consulta Blueprint")
failed = False
for cond, msg in checks:
    status = "OK" if cond else "ERRO"
    print(f"[{status}] {msg}")
    failed = failed or not cond

if failed:
    raise SystemExit(1)

print("Sprint 11 validada com sucesso.")
