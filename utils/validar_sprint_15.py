from pathlib import Path

APP = Path('app.py')
ING = Path('routes/ingredientes_routes.py')

ROTAS_MIGRADAS = [
    '/ingredientes_precos',
    '/ingredientes_admin',
    '/ingrediente_admin/<int:ingrediente_id>',
    '/salvar_ingrediente_admin',
    '/excluir_ingrediente/<int:ingrediente_id>',
    '/atualizar_preco_ingrediente',
    '/reajustar_preco_ingrediente',
    '/cadastrar_ingrediente',
]


def falhar(msg):
    print(f'[ERRO] {msg}')
    raise SystemExit(1)


def ok(msg):
    print(f'[OK] {msg}')


if not APP.exists():
    falhar('app.py não encontrado na raiz do projeto.')
if not ING.exists():
    falhar('routes/ingredientes_routes.py não encontrado.')

app_text = APP.read_text(encoding='utf-8')
ing_text = ING.read_text(encoding='utf-8')

for rota in ROTAS_MIGRADAS:
    if f'@app.route("{rota}' in app_text or f"@app.route('{rota}" in app_text or f"@app.route('{rota}'" in app_text:
        falhar(f'Rota migrada ainda possui decorador @app.route no app.py: {rota}')
    if rota not in ing_text:
        falhar(f'Rota migrada não encontrada no Blueprint de ingredientes: {rota}')

if 'from routes.ingredientes_routes import ingredientes_bp' not in app_text:
    falhar('Import do ingredientes_bp não encontrado no app.py.')
if 'app.register_blueprint(ingredientes_bp)' not in app_text:
    falhar('Registro do ingredientes_bp não encontrado no app.py.')

ok('Rotas migradas de ingredientes não estão mais registradas diretamente no app.py.')
ok('Blueprint de ingredientes contém os endpoints migrados.')
ok('Sprint 15 validada com sucesso.')
