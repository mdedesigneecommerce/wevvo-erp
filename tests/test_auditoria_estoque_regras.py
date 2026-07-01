from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_estoque_baixo_exige_minimo_maior_que_zero_no_app():
    texto = (ROOT / "app.py").read_text(encoding="utf-8", errors="ignore")
    assert "COALESCE(estoque_minimo,0) > 0" in texto
    assert "CASE WHEN COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0) THEN 'Baixo'" not in texto


def test_estoque_routes_nao_classifica_minimo_zero_como_baixo():
    texto = (ROOT / "routes" / "estoque_routes.py").read_text(encoding="utf-8", errors="ignore")
    assert 'float(item["estoque_minimo"] or 0) > 0' in texto


def test_rastreabilidade_usa_estoque_produto_acabado_com_left_join():
    texto = (ROOT / "app.py").read_text(encoding="utf-8", errors="ignore")
    assert "FROM estoque_produto_acabado e" in texto
    assert "LEFT JOIN produtos_finais pf" in texto
