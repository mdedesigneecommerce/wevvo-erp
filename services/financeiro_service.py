"""Serviço financeiro central do Wevvo ERP/PDV.

Este módulo concentra consultas financeiras em uma camada separada do app.py.
Ele não remove nem substitui rotas existentes; fornece uma base segura para
migrar os submódulos financeiros aos poucos.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, List


def _row_to_dict(row: sqlite3.Row | tuple, columns: List[str] | None = None) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if columns:
        return {columns[i]: row[i] for i in range(min(len(columns), len(row)))}
    return {}


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _money(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        if isinstance(value, str):
            value = value.strip().replace("R$", "").replace(" ", "")
            if "," in value and "." in value:
                value = value.replace(".", "").replace(",", ".")
            elif "," in value:
                value = value.replace(",", ".")
        return round(float(value), 2)
    except Exception:
        return 0.0


def _open_status(status: Any) -> bool:
    return str(status or "").strip().lower() not in {"pago", "recebido", "cancelado", "baixado"}


def _date_value(item: Dict[str, Any]) -> str:
    return str(item.get("data_vencimento") or item.get("vencimento") or item.get("data_emissao") or item.get("data") or "9999-99-99")


def _load_lancamentos(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "financeiro_lancamentos"):
        return []
    cursor.execute(
        """
        SELECT * FROM financeiro_lancamentos
        WHERE COALESCE(status, '') <> 'Cancelado'
        ORDER BY COALESCE(data_vencimento, data_emissao, created_at, '') DESC, id DESC
        LIMIT 1500
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def _sum(items: List[Dict[str, Any]]) -> float:
    return round(sum(_money(item.get("valor")) for item in items), 2)


def _group_sum(items: List[Dict[str, Any]], key: str, limit: int = 20) -> List[List[Any]]:
    groups: Dict[str, float] = {}
    for item in items:
        group = str(item.get(key) or "Sem informação")
        groups[group] = groups.get(group, 0.0) + _money(item.get("valor"))
    return [[name, "Total", round(value, 2)] for name, value in sorted(groups.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def gerar_financeiro_integrado(db_path: str) -> Dict[str, Any]:
    """Retorna visão financeira consolidada sem alterar o banco."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        lancamentos = _load_lancamentos(cursor)
        receitas = [x for x in lancamentos if str(x.get("tipo") or "").lower() == "receita"]
        despesas = [x for x in lancamentos if str(x.get("tipo") or "").lower() == "despesa"]
        receber = [x for x in receitas if _open_status(x.get("status"))]
        pagar = [x for x in despesas if _open_status(x.get("status"))]
        vencidos_receber = [x for x in receber if _date_value(x) < date.today().isoformat()]
        vencidos_pagar = [x for x in pagar if _date_value(x) < date.today().isoformat()]
        recebidos = [x for x in receitas if not _open_status(x.get("status"))]
        pagos = [x for x in despesas if not _open_status(x.get("status"))]

        entradas_realizadas = _sum(recebidos)
        saidas_realizadas = _sum(pagos)
        entradas_previstas = _sum(receber)
        saidas_previstas = _sum(pagar)

        return {
            "status": "sucesso",
            "resumo": {
                "saldo_realizado": round(entradas_realizadas - saidas_realizadas, 2),
                "saldo_previsto": round((entradas_realizadas + entradas_previstas) - (saidas_realizadas + saidas_previstas), 2),
                "contas_a_receber": entradas_previstas,
                "contas_a_pagar": saidas_previstas,
                "receber_vencido": _sum(vencidos_receber),
                "pagar_vencido": _sum(vencidos_pagar),
                "entradas_realizadas": entradas_realizadas,
                "saidas_realizadas": saidas_realizadas,
                "total_lancamentos": len(lancamentos),
            },
            "contas_a_receber": receber[:100],
            "contas_a_pagar": pagar[:100],
            "vencidos_receber": vencidos_receber[:100],
            "vencidos_pagar": vencidos_pagar[:100],
            "por_categoria": _group_sum(lancamentos, "categoria"),
            "por_forma_pagamento": _group_sum(lancamentos, "forma_pagamento"),
        }
    finally:
        conn.close()
