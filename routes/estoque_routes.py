"""Rotas de consulta de Estoque do Wevvo ERP/PDV.

Sprint 13: migração segura dos endpoints de leitura de estoque para Blueprint.
Nesta etapa foram migradas apenas rotas GET, sem alterar regras de baixa, entrada,
produção, vendas ou financeiro.

Endpoints preservados:
- /estoque_ingredientes
- /movimentacoes_estoque
- /estoque_produto_acabado
- /movimentacoes_produto_acabado
"""

import sqlite3
from datetime import timedelta

from flask import Blueprint, jsonify, request

from config import BANCO
from services.core import agora_brasilia, data_brasil, data_brasilia_obj


estoque_bp = Blueprint("estoque", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def gerar_lote_ajuste_produto_acabado(cursor, produto_final_id, nome_base):
    """Gera lote interno para estoque inicial/ajuste de produto acabado sem produção registrada."""
    hoje = data_brasilia_obj()
    prefixo = "".join([c for c in str(nome_base or "PA").upper() if c.isalnum()])[:3]
    if len(prefixo) < 3:
        prefixo = (prefixo + "PAX")[:3]
    base = f"AJ{prefixo}{hoje.strftime('%y%m%d')}{int(produto_final_id):04d}"
    cursor.execute("SELECT COUNT(*) AS total FROM estoque_produto_acabado WHERE lote LIKE ?", (base + "%",))
    sequencia = int(cursor.fetchone()["total"] or 0) + 1
    return f"{base}{sequencia:03d}"


def sincronizar_produto_acabado_por_estoque(cursor, produto_final_id):
    """
    Mantém Produto Acabado coerente com o estoque digitado em Produto Final.
    Esta cópia local preserva compatibilidade enquanto o serviço definitivo de estoque
    é extraído nas próximas Sprints.
    """
    if not produto_final_id:
        return

    cursor.execute("""
        SELECT pf.id, pf.nome, pf.estoque_atual, pf.validade_dias, pf.receita_id, r.nome AS receita_nome
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        WHERE pf.id = ?
    """, (int(produto_final_id),))
    produto = cursor.fetchone()

    if not produto:
        return

    estoque_alvo = float(produto["estoque_atual"] or 0)

    cursor.execute("""
        SELECT COALESCE(SUM(saldo_atual), 0) AS saldo_lotes
        FROM estoque_produto_acabado
        WHERE produto_final_id = ?
    """, (int(produto_final_id),))
    saldo_lotes = float(cursor.fetchone()["saldo_lotes"] or 0)

    diferenca = round(estoque_alvo - saldo_lotes, 6)

    if abs(diferenca) < 0.000001:
        return

    if diferenca > 0:
        fabricacao_data = data_brasilia_obj()
        validade_dias = int(produto["validade_dias"] or 0)
        validade_data = fabricacao_data + timedelta(days=validade_dias) if validade_dias > 0 else fabricacao_data
        data_fabricacao = data_brasil(fabricacao_data)
        data_validade = data_brasil(validade_data)
        lote = gerar_lote_ajuste_produto_acabado(cursor, int(produto_final_id), produto["nome"])

        cursor.execute("""
            INSERT INTO estoque_produto_acabado (
                produto_final_id, produto_final_nome, receita_id, receita_nome, producao_id, lote,
                quantidade_produzida, saldo_atual, data_fabricacao, data_validade, observacao, created_at
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(produto_final_id), produto["nome"], produto["receita_id"], produto["receita_nome"],
            lote, diferenca, diferenca, data_fabricacao, data_validade,
            "Lote automático criado a partir do estoque informado no cadastro do Produto Final.",
            agora_brasilia()
        ))
        return

    restante_baixar = abs(diferenca)
    cursor.execute("""
        SELECT id, saldo_atual
        FROM estoque_produto_acabado
        WHERE produto_final_id = ? AND saldo_atual > 0
        ORDER BY id DESC
    """, (int(produto_final_id),))
    lotes = cursor.fetchall()

    for lote in lotes:
        if restante_baixar <= 0:
            break
        saldo_atual = float(lote["saldo_atual"] or 0)
        baixa = min(saldo_atual, restante_baixar)
        novo_saldo = round(saldo_atual - baixa, 6)
        cursor.execute("""
            UPDATE estoque_produto_acabado
            SET saldo_atual = ?,
                observacao = COALESCE(observacao, '') || ' | Ajuste automático por edição do estoque do Produto Final.'
            WHERE id = ?
        """, (novo_saldo, int(lote["id"])))
        restante_baixar = round(restante_baixar - baixa, 6)


@estoque_bp.route("/estoque_ingredientes")
def estoque_ingredientes():
    termo = request.args.get("q", "").strip()

    conn = conectar_banco()
    cursor = conn.cursor()

    if termo:
        cursor.execute("""
            SELECT
                id,
                nome,
                unidade,
                COALESCE(preco_kg, 0) AS preco_kg,
                COALESCE(estoque_atual, 0) AS estoque_atual,
                COALESCE(estoque_minimo, 0) AS estoque_minimo
            FROM ingredientes
            WHERE nome LIKE ?
            ORDER BY nome ASC
            LIMIT 80
        """, (f"%{termo}%",))
    else:
        cursor.execute("""
            SELECT
                id,
                nome,
                unidade,
                COALESCE(preco_kg, 0) AS preco_kg,
                COALESCE(estoque_atual, 0) AS estoque_atual,
                COALESCE(estoque_minimo, 0) AS estoque_minimo
            FROM ingredientes
            ORDER BY nome ASC
            LIMIT 80
        """)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "nome": item["nome"],
            "unidade": item["unidade"],
            "preco": item["preco_kg"],
            "estoque_atual": item["estoque_atual"],
            "estoque_minimo": item["estoque_minimo"],
            "status": "BAIXO" if item["estoque_atual"] <= item["estoque_minimo"] else "OK"
        }
        for item in linhas
    ])


@estoque_bp.route("/movimentacoes_estoque")
def listar_movimentacoes_estoque():
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            ingrediente_nome,
            tipo,
            quantidade,
            unidade,
            observacao,
            created_at
        FROM movimentacoes_estoque
        ORDER BY id DESC
        LIMIT 80
    """)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "ingrediente_nome": item["ingrediente_nome"],
            "tipo": item["tipo"],
            "quantidade": item["quantidade"],
            "unidade": item["unidade"],
            "observacao": item["observacao"],
            "created_at": item["created_at"]
        }
        for item in linhas
    ])


@estoque_bp.route("/estoque_produto_acabado")
def estoque_produto_acabado():
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM produtos_finais WHERE ativo = 1")
    for produto in cursor.fetchall():
        sincronizar_produto_acabado_por_estoque(cursor, produto["id"])
    conn.commit()

    cursor.execute("""
        SELECT e.id, e.produto_final_id, e.produto_final_nome, e.receita_nome, e.lote,
               e.quantidade_produzida, e.saldo_atual, e.data_fabricacao,
               e.data_validade, e.observacao, e.created_at,
               COALESCE(pf.preco_venda, 0) AS preco_venda
        FROM estoque_produto_acabado e
        INNER JOIN produtos_finais pf ON pf.id = e.produto_final_id
        WHERE COALESCE(pf.ativo, 1) = 1
          AND e.saldo_atual > 0
        ORDER BY e.id DESC
        LIMIT 150
    """)
    linhas = cursor.fetchall()
    conn.close()
    return jsonify([dict(item) for item in linhas])


@estoque_bp.route("/movimentacoes_produto_acabado")
def listar_movimentacoes_produto_acabado():
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, produto_final_nome, lote, tipo, quantidade, saldo_anterior, saldo_atual, observacao, created_at
        FROM movimentacoes_produto_acabado
        ORDER BY id DESC
        LIMIT 150
    """)
    linhas = cursor.fetchall()
    conn.close()
    return jsonify([dict(item) for item in linhas])
