"""Serviço central de estoque do Saúde e Nutri ERP.

Sprint 2 real: inicia a migração das consultas de estoque para services,
sem apagar as funções antigas do app.py. O serviço é tolerante a tabelas/colunas
ausentes e retorna payloads específicos para cada submenu de estoque.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, List


def _money(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        if isinstance(value, str):
            value = value.strip().replace("R$", "").replace("%", "").replace(" ", "")
            if "," in value and "." in value:
                value = value.replace(".", "").replace(",", ".")
            elif "," in value:
                value = value.replace(",", ".")
        return float(value)
    except Exception:
        return 0.0


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,))
        return cursor.fetchone() is not None
    except Exception:
        return False


def _rows(cursor: sqlite3.Cursor, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []


def _kpis_estoque(ingredientes: List[Dict[str, Any]], produtos: List[Dict[str, Any]], lotes: List[Dict[str, Any]], movimentos: List[Dict[str, Any]]) -> Dict[str, Any]:
    estoque_baixo_ing = [i for i in ingredientes if _money(i.get("estoque_minimo")) > 0 and _money(i.get("estoque_atual")) <= _money(i.get("estoque_minimo"))]
    estoque_baixo_prod = [p for p in produtos if _money(p.get("estoque_minimo")) > 0 and _money(p.get("estoque_atual")) <= _money(p.get("estoque_minimo"))]
    valor_ingredientes = sum(_money(i.get("estoque_atual")) * _money(i.get("custo_unitario")) for i in ingredientes)
    valor_produtos = sum(_money(p.get("estoque_atual")) * _money(p.get("preco_venda")) for p in produtos)
    hoje = date.today().isoformat()
    lotes_validade = [
        l for l in lotes
        if (l.get("validade") or "9999-99-99") <= hoje
        and _money(l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade")) > 0
    ]
    return {
        "ingredientes": len(ingredientes),
        "produtos": len(produtos),
        "movimentos": len(movimentos),
        "lotes": len(lotes),
        "estoque_baixo_ing": len(estoque_baixo_ing),
        "estoque_baixo_prod": len(estoque_baixo_prod),
        "valor_total": round(valor_ingredientes + valor_produtos, 2),
        "lotes_vencidos_ou_vencendo": len(lotes_validade),
        "_baixo_ing": estoque_baixo_ing,
        "_baixo_prod": estoque_baixo_prod,
        "_lotes_validade": lotes_validade,
    }


def carregar_base_estoque(cursor: sqlite3.Cursor) -> Dict[str, List[Dict[str, Any]]]:
    ingredientes: List[Dict[str, Any]] = []
    produtos: List[Dict[str, Any]] = []
    movimentos: List[Dict[str, Any]] = []
    lotes: List[Dict[str, Any]] = []

    if _table_exists(cursor, "ingredientes"):
        ingredientes = _rows(cursor, """
            SELECT id, nome, unidade,
                   COALESCE(estoque_atual, 0) AS estoque_atual,
                   COALESCE(estoque_minimo, 0) AS estoque_minimo,
                   COALESCE(preco_kg, 0) AS custo_unitario,
                   CASE WHEN COALESCE(estoque_minimo,0) > 0
                         AND COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0)
                        THEN 'Baixo' ELSE 'OK' END AS status
            FROM ingredientes
            ORDER BY nome COLLATE NOCASE ASC
            LIMIT 1000
        """)

    if _table_exists(cursor, "produtos_finais"):
        produtos = _rows(cursor, """
            SELECT id, nome,
                   COALESCE(estoque_atual, 0) AS estoque_atual,
                   COALESCE(estoque_minimo, 0) AS estoque_minimo,
                   COALESCE(preco_venda, 0) AS preco_venda,
                   CASE WHEN COALESCE(estoque_minimo,0) > 0
                         AND COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0)
                        THEN 'Baixo' ELSE 'OK' END AS status
            FROM produtos_finais
            WHERE COALESCE(ativo, 1) = 1
            ORDER BY nome COLLATE NOCASE ASC
            LIMIT 1000
        """)

    if _table_exists(cursor, "movimentacoes_estoque"):
        movimentos = _rows(cursor, """
            SELECT id, ingrediente_id, ingrediente_nome, tipo, quantidade, unidade, observacao, created_at
            FROM movimentacoes_estoque
            ORDER BY id DESC
            LIMIT 500
        """)

    if _table_exists(cursor, "lotes_producao"):
        lotes = _rows(cursor, """
            SELECT id, produto_nome, lote, codigo_lote, validade, saldo_atual, quantidade, status, created_at
            FROM lotes_producao
            ORDER BY COALESCE(validade, created_at) ASC, id DESC
            LIMIT 500
        """)
    if not lotes and _table_exists(cursor, "lotes"):
        lotes = _rows(cursor, """
            SELECT id, produto_nome, lote, validade, saldo AS saldo_atual, quantidade, status, created_at
            FROM lotes
            ORDER BY COALESCE(validade, created_at) ASC, id DESC
            LIMIT 500
        """)

    return {"ingredientes": ingredientes, "produtos": produtos, "movimentos": movimentos, "lotes": lotes}


def gerar_estoque_submodulo(cursor: sqlite3.Cursor, modulo: str) -> Dict[str, Any]:
    modulo = (modulo or "").strip().lower()
    base = carregar_base_estoque(cursor)
    ingredientes = base["ingredientes"]
    produtos = base["produtos"]
    movimentos = base["movimentos"]
    lotes = base["lotes"]
    kpi = _kpis_estoque(ingredientes, produtos, lotes, movimentos)

    baixo = kpi["_baixo_ing"] + kpi["_baixo_prod"]
    lotes_validade = kpi["_lotes_validade"]

    if modulo == "lancamentos-estoque":
        return {
            "titulo": "Lançamentos de estoque",
            "resumo": {"kpi1": kpi["movimentos"], "kpi2": kpi["ingredientes"], "kpi3": kpi["produtos"], "kpi4": kpi["valor_total"]},
            "linhas1": [[m.get("ingrediente_nome") or "-", m.get("tipo") or "-", f"{m.get('quantidade') or 0} {m.get('unidade') or ''}".strip()] for m in movimentos[:50]],
            "linhas2": [[i.get("nome") or "-", i.get("status") or "-", f"{i.get('estoque_atual') or 0} / mín. {i.get('estoque_minimo') or 0}"] for i in ingredientes[:50]],
        }

    if modulo == "conferencia-estoque":
        return {
            "titulo": "Conferência de estoque",
            "resumo": {"kpi1": len(baixo), "kpi2": kpi["ingredientes"], "kpi3": kpi["produtos"], "kpi4": kpi["lotes"]},
            "linhas1": [[x.get("nome") or "-", x.get("status") or "Verificar", f"{x.get('estoque_atual') or 0} / mín. {x.get('estoque_minimo') or 0}"] for x in baixo[:50]],
            "linhas2": [[l.get("produto_nome") or "-", l.get("lote") or l.get("codigo_lote") or "-", l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade") or 0] for l in lotes[:50]],
        }

    if modulo == "depositos":
        return {
            "titulo": "Depósitos",
            "resumo": {"kpi1": 1, "kpi2": kpi["ingredientes"], "kpi3": kpi["produtos"], "kpi4": kpi["valor_total"]},
            "linhas1": [["Depósito principal", "Ativo", f"R$ {kpi['valor_total']:.2f}"], ["Ingredientes", "Saldo físico", kpi["ingredientes"]], ["Produtos acabados", "Saldo por lote", kpi["produtos"]]],
            "linhas2": [[l.get("produto_nome") or "-", l.get("lote") or l.get("codigo_lote") or "-", l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade") or 0] for l in lotes[:50]],
        }

    if modulo in ("lotes-rastreabilidade", "qualidade-validades"):
        return {
            "titulo": "Lotes e rastreabilidade",
            "resumo": {"kpi1": kpi["lotes"], "kpi2": kpi["lotes_vencidos_ou_vencendo"], "kpi3": round(sum(_money(l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade")) for l in lotes), 3), "kpi4": len({l.get("produto_nome") for l in lotes if l.get("produto_nome")})},
            "linhas1": [[l.get("produto_nome") or "-", l.get("lote") or l.get("codigo_lote") or "-", l.get("validade") or "-"] for l in lotes[:50]],
            "linhas2": [[l.get("produto_nome") or "-", "Saldo", l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade") or 0] for l in lotes_validade[:50]],
        }

    return {
        "titulo": "Estoque",
        "resumo": {"kpi1": kpi["ingredientes"], "kpi2": len(baixo), "kpi3": kpi["movimentos"], "kpi4": kpi["valor_total"]},
        "linhas1": [[i.get("nome") or "-", i.get("status") or "-", f"{i.get('estoque_atual') or 0} / mín. {i.get('estoque_minimo') or 0}"] for i in ingredientes[:50]],
        "linhas2": [[l.get("produto_nome") or "-", l.get("lote") or l.get("codigo_lote") or "-", l.get("saldo_atual") if l.get("saldo_atual") is not None else l.get("quantidade") or 0] for l in lotes[:50]],
    }
