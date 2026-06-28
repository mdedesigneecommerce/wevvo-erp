"""Indicadores executivos reais do Wevvo ERP/PDV.

Este serviço centraliza o dashboard para evitar que a tela use valores simulados
ou consultas espalhadas pelo app.py. Todas as consultas são defensivas: se uma
coluna/tabela ainda não existir na instalação do usuário, o indicador retorna
zero e a auditoria pode apontar a pendência sem quebrar o sistema.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _col_exists(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    try:
        return any(row[1] == col for row in cur.execute(f"PRAGMA table_info({table})"))
    except Exception:
        return False


def _scalar(cur: sqlite3.Cursor, sql: str, params: tuple = (), default: Any = 0) -> Any:
    try:
        row = cur.execute(sql, params).fetchone()
        if row is None:
            return default
        value = row[0]
        return default if value is None else value
    except Exception:
        return default


def _money(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def gerar_dashboard_executivo(caminho_banco: str) -> Dict[str, Any]:
    """Retorna indicadores do ERP com dados reais do banco.

    A função não altera dados. Ela consolida as leituras dos módulos principais
    para servir Dashboard, relatórios e futuras telas de BI.
    """
    conn = sqlite3.connect(caminho_banco)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    hoje = datetime.now().date().isoformat()
    mes = hoje[:7]

    dashboard: Dict[str, Any] = {
        "status": "sucesso",
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "financeiro": {},
        "vendas": {},
        "compras": {},
        "estoque": {},
        "producao": {},
        "qualidade": {},
        "cmv": {},
        "alertas": {},
    }

    if _table_exists(cur, "financeiro_lancamentos"):
        dashboard["financeiro"] = {
            "contas_a_receber_abertas": _money(_scalar(cur, """
                SELECT SUM(valor) FROM financeiro_lancamentos
                WHERE LOWER(COALESCE(tipo,'')) IN ('receita','entrada')
                  AND LOWER(COALESCE(status,'')) NOT IN ('recebido','pago','cancelado')
            """)),
            "contas_a_pagar_abertas": _money(_scalar(cur, """
                SELECT SUM(valor) FROM financeiro_lancamentos
                WHERE LOWER(COALESCE(tipo,'')) IN ('despesa','saida','saída')
                  AND LOWER(COALESCE(status,'')) NOT IN ('pago','recebido','cancelado')
            """)),
            "receber_vencido": _money(_scalar(cur, """
                SELECT SUM(valor) FROM financeiro_lancamentos
                WHERE LOWER(COALESCE(tipo,'')) IN ('receita','entrada')
                  AND COALESCE(data_vencimento,'9999-99-99') < ?
                  AND LOWER(COALESCE(status,'')) NOT IN ('recebido','pago','cancelado')
            """, (hoje,))),
            "pagar_vencido": _money(_scalar(cur, """
                SELECT SUM(valor) FROM financeiro_lancamentos
                WHERE LOWER(COALESCE(tipo,'')) IN ('despesa','saida','saída')
                  AND COALESCE(data_vencimento,'9999-99-99') < ?
                  AND LOWER(COALESCE(status,'')) NOT IN ('pago','recebido','cancelado')
            """, (hoje,))),
        }
        dashboard["financeiro"]["saldo_previsto"] = _money(
            dashboard["financeiro"]["contas_a_receber_abertas"] - dashboard["financeiro"]["contas_a_pagar_abertas"]
        )

    if _table_exists(cur, "vendas_erp"):
        dashboard["vendas"] = {
            "faturamento_mes": _money(_scalar(cur, "SELECT SUM(valor_total) FROM vendas_erp WHERE substr(COALESCE(created_at,''),1,7)=?", (mes,))),
            "faturamento_total": _money(_scalar(cur, "SELECT SUM(valor_total) FROM vendas_erp")),
            "lucro_estimado_mes": _money(_scalar(cur, "SELECT SUM(lucro_estimado) FROM vendas_erp WHERE substr(COALESCE(created_at,''),1,7)=?", (mes,))),
            "pedidos_mes": int(_scalar(cur, "SELECT COUNT(*) FROM vendas_erp WHERE substr(COALESCE(created_at,''),1,7)=?", (mes,), 0) or 0),
        }

    if _table_exists(cur, "compras_pedidos"):
        dashboard["compras"] = {
            "compras_pendentes": int(_scalar(cur, "SELECT COUNT(*) FROM compras_pedidos WHERE LOWER(COALESCE(status,'')) NOT IN ('recebido','cancelado','pago')", default=0) or 0),
            "valor_pendente": _money(_scalar(cur, "SELECT SUM(valor_total) FROM compras_pedidos WHERE LOWER(COALESCE(status,'')) NOT IN ('recebido','cancelado','pago')")),
            "valor_recebido_mes": _money(_scalar(cur, "SELECT SUM(valor_total) FROM compras_pedidos WHERE substr(COALESCE(data_recebimento,created_at,''),1,7)=? AND LOWER(COALESCE(status,'')) IN ('recebido','pago')", (mes,))),
        }

    if _table_exists(cur, "ingredientes"):
        dashboard["estoque"].update({
            "ingredientes_cadastrados": int(_scalar(cur, "SELECT COUNT(*) FROM ingredientes", default=0) or 0),
            "ingredientes_criticos": int(_scalar(cur, "SELECT COUNT(*) FROM ingredientes WHERE COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<=COALESCE(estoque_minimo,0)", default=0) or 0),
        })
    if _table_exists(cur, "produtos_finais"):
        dashboard["estoque"].update({
            "produtos_cadastrados": int(_scalar(cur, "SELECT COUNT(*) FROM produtos_finais", default=0) or 0),
            "produtos_criticos": int(_scalar(cur, "SELECT COUNT(*) FROM produtos_finais WHERE COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<=COALESCE(estoque_minimo,0)", default=0) or 0),
            "saldo_produto_acabado": _money(_scalar(cur, "SELECT SUM(estoque_atual) FROM produtos_finais")),
        })

    if _table_exists(cur, "producoes"):
        dashboard["producao"] = {
            "producoes_mes": int(_scalar(cur, "SELECT COUNT(*) FROM producoes WHERE substr(COALESCE(created_at,''),1,7)=?", (mes,), 0) or 0),
            "quantidade_produzida_mes": _money(_scalar(cur, "SELECT SUM(quantidade) FROM producoes WHERE substr(COALESCE(created_at,''),1,7)=?", (mes,))),
        }
    if _table_exists(cur, "pcp_ordens_producao"):
        dashboard["producao"]["ordens_abertas"] = int(_scalar(cur, "SELECT COUNT(*) FROM pcp_ordens_producao WHERE LOWER(COALESCE(status,'')) NOT IN ('concluida','concluída','cancelada')", default=0) or 0)

    if _table_exists(cur, "qualidade_nao_conformidades"):
        dashboard["qualidade"] = {
            "nao_conformidades_abertas": int(_scalar(cur, "SELECT COUNT(*) FROM qualidade_nao_conformidades WHERE LOWER(COALESCE(status,'')) NOT IN ('concluida','concluída','resolvida','cancelada')", default=0) or 0),
            "nao_conformidades_mes": int(_scalar(cur, "SELECT COUNT(*) FROM qualidade_nao_conformidades WHERE substr(COALESCE(created_at,data_registro,''),1,7)=?", (mes,), 0) or 0),
        }

    if _table_exists(cur, "cmv_precificacao"):
        dashboard["cmv"] = {
            "margem_media": _money(_scalar(cur, "SELECT AVG(margem_real_percentual) FROM cmv_precificacao WHERE COALESCE(preco_praticado,0)>0")),
            "lucro_total_estimado": _money(_scalar(cur, "SELECT SUM(lucro_real) FROM cmv_precificacao")),
            "itens_prejuizo": int(_scalar(cur, "SELECT COUNT(*) FROM cmv_precificacao WHERE COALESCE(lucro_real,0)<0", default=0) or 0),
            "itens_sem_preco": int(_scalar(cur, "SELECT COUNT(*) FROM cmv_precificacao WHERE COALESCE(preco_praticado,0)<=0", default=0) or 0),
        }

    if _table_exists(cur, "erp_motor_alertas"):
        dashboard["alertas"] = {
            "abertos": int(_scalar(cur, "SELECT COUNT(*) FROM erp_motor_alertas WHERE COALESCE(resolvido,0)=0", default=0) or 0),
            "criticos": int(_scalar(cur, "SELECT COUNT(*) FROM erp_motor_alertas WHERE COALESCE(resolvido,0)=0 AND UPPER(COALESCE(nivel,'')) IN ('CRITICO','CRÍTICO')", default=0) or 0),
        }

    conn.close()
    return dashboard
