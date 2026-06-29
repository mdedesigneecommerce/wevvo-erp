"""Testes mínimos da fundação modular da Sprint 03."""

from routes import BLUEPRINTS


def test_blueprints_base_criados():
    nomes = {bp.name for bp in BLUEPRINTS}
    assert nomes == {
        "dashboard",
        "estoque",
        "financeiro",
        "compras",
        "producao",
        "qualidade",
        "api",
    }
