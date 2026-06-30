from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ROUTE = ROOT / "routes" / "configuracoes_routes.py"
DOC = ROOT / "docs" / "SPRINT_06_CONFIGURACOES_BLUEPRINT.md"


def falhar(msg):
    raise SystemExit(f"[ERRO] {msg}")


def ok(msg):
    print(f"[OK] {msg}")


if not APP.exists():
    falhar("app.py nao encontrado")
if not ROUTE.exists():
    falhar("routes/configuracoes_routes.py nao encontrado")
if not DOC.exists():
    falhar("Documento da Sprint 06 nao encontrado")

app_text = APP.read_text(encoding="utf-8")
route_text = ROUTE.read_text(encoding="utf-8")

if "from routes.configuracoes_routes import configuracoes_bp" not in app_text:
    falhar("app.py nao importa configuracoes_bp")
ok("Import do configuracoes_bp encontrado")

if "app.register_blueprint(configuracoes_bp)" not in app_text:
    falhar("app.py nao registra configuracoes_bp")
ok("Registro do configuracoes_bp encontrado")

if '@app.route("/api/perfil_empresa"' in app_text or "@app.route('/api/perfil_empresa'" in app_text:
    falhar("Rotas de perfil_empresa ainda estao registradas diretamente no app.py")
ok("Rotas de perfil_empresa removidas do app.py")

for trecho in [
    'configuracoes_bp = Blueprint("configuracoes", __name__)',
    '@configuracoes_bp.route("/api/perfil_empresa", methods=["GET"])',
    '@configuracoes_bp.route("/api/perfil_empresa", methods=["POST"])',
    'def montar_perfil_empresa_dict(row):',
]:
    if trecho not in route_text:
        falhar(f"Trecho ausente em configuracoes_routes.py: {trecho}")
ok("Blueprint de configuracoes contem rotas esperadas")

py_compile.compile(str(APP), doraise=True)
py_compile.compile(str(ROUTE), doraise=True)
ok("Sintaxe Python validada")

print("Sprint 06 validada com sucesso.")
