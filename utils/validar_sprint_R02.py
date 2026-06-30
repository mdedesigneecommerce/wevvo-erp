from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
app = (ROOT / "app.py").read_text(encoding="utf-8")
compras = (ROOT / "routes" / "compras_routes.py").read_text(encoding="utf-8")

ROTAS = [
    "/compras_status",
    "/compras_criar_solicitacao",
    "/compras_registrar_cotacao",
    "/compras_gerar_pedido",
    "/compras_receber_pedido",
    "/compras_sincronizar_erp",
]

falhas = []

for rota in ROTAS:
    if rota not in compras:
        falhas.append(f"Rota ausente em routes/compras_routes.py: {rota}")
    if ("@app.route('" + rota + "'") in app or ('@app.route("' + rota + '"') in app:
        falhas.append(f"Rota ainda registrada diretamente no app.py: {rota}")

if "create_compras_blueprint" not in compras:
    falhas.append("Factory create_compras_blueprint ausente em routes/compras_routes.py")

if "app.register_blueprint(create_compras_blueprint" not in app:
    falhas.append("Blueprint de Compras não registrado no app.py")

for arquivo in [ROOT / "app.py", ROOT / "routes" / "compras_routes.py"]:
    try:
        ast.parse(arquivo.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        falhas.append(f"Erro de sintaxe em {arquivo.name}: {exc}")

if falhas:
    print("Sprint R02 com falhas:")
    for falha in falhas:
        print(f"- {falha}")
    raise SystemExit(1)

print("Sprint R02 validada com sucesso.")
print("Rotas de Compras migradas para Blueprint sem duplicidade direta no app.py.")
