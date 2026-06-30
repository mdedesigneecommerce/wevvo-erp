from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
app = ROOT / "app.py"
route = ROOT / "routes" / "busca_routes.py"

def exigir(condicao, mensagem):
    if not condicao:
        raise SystemExit(f"ERRO: {mensagem}")

app_text = app.read_text(encoding="utf-8")
route_text = route.read_text(encoding="utf-8")

exigir(route.exists(), "routes/busca_routes.py não encontrado")
exigir("from routes.busca_routes import busca_bp" in app_text, "busca_bp não importado no app.py")
exigir("app.register_blueprint(busca_bp)" in app_text, "busca_bp não registrado no app.py")
exigir('@busca_bp.route("/buscar")' in route_text, "rota /buscar não encontrada no blueprint")
exigir('@busca_bp.route("/sugerir_nutriente_ingrediente")' in route_text, "rota /sugerir_nutriente_ingrediente não encontrada no blueprint")
exigir('@app.route("/buscar")' not in app_text, "rota /buscar ainda está registrada diretamente no app.py")
exigir('@app.route("/sugerir_nutriente_ingrediente")' not in app_text, "rota /sugerir_nutriente_ingrediente ainda está registrada diretamente no app.py")

py_compile.compile(str(app), doraise=True)
py_compile.compile(str(route), doraise=True)

print("Sprint 10 validada: busca nutricional migrada para Blueprint com sucesso.")
