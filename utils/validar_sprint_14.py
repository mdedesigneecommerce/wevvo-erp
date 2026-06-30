from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
ESTOQUE = ROOT / "routes" / "estoque_routes.py"
DOC = ROOT / "docs" / "SPRINT_14_ESTOQUE_MANUTENCAO_BLUEPRINT.md"

ROTAS = [
    "/estoque_ingredientes",
    "/movimentacoes_estoque",
    "/estoque_produto_acabado",
    "/movimentacoes_produto_acabado",
    "/movimentar_estoque",
    "/atualizar_estoque_minimo",
    "/movimentar_produto_final_estoque",
    "/atualizar_produto_final_estoque_minimo",
]

FUNCOES = [
    "def estoque_ingredientes",
    "def listar_movimentacoes_estoque",
    "def estoque_produto_acabado",
    "def listar_movimentacoes_produto_acabado",
    "def movimentar_estoque",
    "def atualizar_estoque_minimo",
    "def movimentar_produto_final_estoque",
    "def atualizar_produto_final_estoque_minimo",
]


def assert_true(condicao, mensagem):
    if not condicao:
        raise AssertionError(mensagem)


def main():
    assert_true(APP.exists(), "app.py não encontrado")
    assert_true(ESTOQUE.exists(), "routes/estoque_routes.py não encontrado")
    assert_true(DOC.exists(), "documentação da Sprint 14 não encontrada")

    app_txt = APP.read_text(encoding="utf-8")
    estoque_txt = ESTOQUE.read_text(encoding="utf-8")

    assert_true("from routes.estoque_routes import estoque_bp" in app_txt, "estoque_bp não importado no app.py")
    assert_true("app.register_blueprint(estoque_bp)" in app_txt, "estoque_bp não registrado no app.py")

    for rota in ROTAS:
        assert_true(f'@app.route("{rota}' not in app_txt and f"@app.route('{rota}" not in app_txt,
                    f"rota {rota} ainda está registrada diretamente no app.py")
        assert_true(f'@estoque_bp.route("{rota}' in estoque_txt or f"@estoque_bp.route('{rota}" in estoque_txt,
                    f"rota {rota} não encontrada no blueprint de estoque")

    for funcao in FUNCOES:
        assert_true(funcao in estoque_txt, f"{funcao} não encontrada em routes/estoque_routes.py")

    py_compile.compile(str(APP), doraise=True)
    py_compile.compile(str(ESTOQUE), doraise=True)

    print("Sprint 14 validada com sucesso: manutenção de estoque migrada para Blueprint.")


if __name__ == "__main__":
    main()
