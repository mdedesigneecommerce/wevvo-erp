from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROUTE = ROOT / "routes" / "ingredientes_routes.py"
DOC = ROOT / "docs" / "SPRINT_12_INGREDIENTES_MANUTENCAO_BLUEPRINT.md"

checks = []

def ok(cond, msg):
    checks.append((cond, msg))

app_text = APP.read_text(encoding="utf-8")
route_text = ROUTE.read_text(encoding="utf-8") if ROUTE.exists() else ""

ok(ROUTE.exists(), "routes/ingredientes_routes.py existe")
ok(DOC.exists(), "documentação da Sprint 12 existe")
ok("app.register_blueprint(ingredientes_bp)" in app_text, "app.py registra ingredientes_bp")
ok("def _delegar_para_app" in route_text, "ponte segura para app.py declarada")
for endpoint in [
    "/salvar_ingrediente_admin",
    "/excluir_ingrediente/<int:ingrediente_id>",
    "/atualizar_preco_ingrediente",
    "/reajustar_preco_ingrediente",
    "/cadastrar_ingrediente",
]:
    ok(endpoint in route_text, f"endpoint {endpoint} presente no blueprint")

for file_path in [APP, ROUTE]:
    try:
        ast.parse(file_path.read_text(encoding="utf-8"))
        ok(True, f"{file_path.name} sem erro de sintaxe Python")
    except SyntaxError as exc:
        ok(False, f"{file_path.name} com erro de sintaxe: {exc}")

print("Validação Sprint 12 — Ingredientes Manutenção Blueprint")
failed = False
for cond, msg in checks:
    status = "OK" if cond else "ERRO"
    print(f"[{status}] {msg}")
    failed = failed or not cond

if failed:
    raise SystemExit(1)

print("Sprint 12 validada com sucesso.")
