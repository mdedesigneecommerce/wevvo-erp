from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
app_py = ROOT / "app.py"
home_routes = ROOT / "routes" / "home_routes.py"
doc = ROOT / "docs" / "SPRINT_08_HOME_BLUEPRINT.md"

falhas = []

for arquivo in [app_py, home_routes, doc]:
    if not arquivo.exists():
        falhas.append(f"Arquivo ausente: {arquivo.relative_to(ROOT)}")

if home_routes.exists():
    texto = home_routes.read_text(encoding="utf-8")
    obrigatorios = [
        "home_bp = Blueprint",
        '@home_bp.route("/")',
        '@home_bp.route("/receitas_salvas")',
        "def index():",
        "def receitas_salvas_json():",
    ]
    for item in obrigatorios:
        if item not in texto:
            falhas.append(f"Item ausente em routes/home_routes.py: {item}")
    ast.parse(texto)

if app_py.exists():
    texto = app_py.read_text(encoding="utf-8")
    if "from routes.home_routes import home_bp" not in texto:
        falhas.append("Import do home_bp ausente no app.py")
    if "app.register_blueprint(home_bp)" not in texto:
        falhas.append("Registro do home_bp ausente no app.py")
    if '@app.route("/")' in texto:
        falhas.append("Rota / ainda esta registrada diretamente no app.py")
    if '@app.route("/receitas_salvas")' in texto:
        falhas.append("Rota /receitas_salvas ainda esta registrada diretamente no app.py")
    ast.parse(texto)

if falhas:
    print("Sprint 08 com pendencias:")
    for falha in falhas:
        print(f"- {falha}")
    raise SystemExit(1)

print("Sprint 08 validada com sucesso.")
print("Rotas principais migradas para routes/home_routes.py.")
