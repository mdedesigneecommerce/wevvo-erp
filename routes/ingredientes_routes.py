"""Rotas de consulta de Ingredientes do Wevvo ERP/PDV.

Sprint 11: migração segura das rotas de leitura de ingredientes para Blueprint.
Nesta etapa migramos apenas endpoints de consulta, sem alterar regras de gravação, estoque,
CMV ou vínculos com receitas. Isso reduz risco e prepara a próxima extração do CRUD completo.

Endpoints preservados:
- /ingredientes_precos
- /ingredientes_admin
- /ingrediente_admin/<int:ingrediente_id>
"""

import sqlite3

from flask import Blueprint, jsonify, request

from config import BANCO


ingredientes_bp = Blueprint("ingredientes", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def montar_ingrediente_admin(item):
    preco_compra = item["preco_compra"] if "preco_compra" in item.keys() else None
    quantidade_compra = item["quantidade_compra"] if "quantidade_compra" in item.keys() else None
    unidade_compra = item["unidade_compra"] if "unidade_compra" in item.keys() else None

    if preco_compra in [None, ""]:
        preco_compra = item["preco_kg"] or 0
    if quantidade_compra in [None, "", 0]:
        quantidade_compra = 1
    if not unidade_compra:
        unidade_compra = item["unidade"] or "KG"

    return {
        "id": item["id"],
        "nome": item["nome"],
        "preco_compra": preco_compra,
        "quantidade_compra": quantidade_compra,
        "unidade_compra": unidade_compra,
        "preco_unitario": item["preco_kg"],
        "unidade": item["unidade"],
        "calorias_100g": item["calorias_100g"],
        "carboidratos_100g": item["carboidratos_100g"],
        "acucares_totais_100g": item["acucares_totais_100g"],
        "acucares_adicionados_100g": item["acucares_adicionados_100g"],
        "proteinas_100g": item["proteinas_100g"],
        "gorduras_100g": item["gorduras_100g"],
        "gorduras_saturadas_100g": item["gorduras_saturadas_100g"],
        "gorduras_trans_100g": item["gorduras_trans_100g"],
        "fibra_alimentar_100g": item["fibra_alimentar_100g"],
        "sodio_100g": item["sodio_100g"],
        "fonte_nutricional": item["fonte_nutricional"],
        "taco_match_nome": item["taco_match_nome"],
        "taco_match_similaridade": item["taco_match_similaridade"],
        "receitas_usando": item["receitas_usando"] if "receitas_usando" in item.keys() else 0,
        "updated_at": item["updated_at"]
    }


@ingredientes_bp.route("/ingredientes_precos")
def ingredientes_precos():
    termo = request.args.get("q", "").strip()

    conn = conectar_banco()
    cursor = conn.cursor()

    if termo:
        cursor.execute("""
            SELECT id, nome, preco_kg, unidade, updated_at
            FROM ingredientes
            WHERE nome LIKE ?
            ORDER BY nome ASC
            LIMIT 80
        """, (f"%{termo}%",))
    else:
        cursor.execute("""
            SELECT id, nome, preco_kg, unidade, updated_at
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
            "preco": item["preco_kg"],
            "unidade": item["unidade"],
            "updated_at": item["updated_at"]
        }
        for item in linhas
    ])


@ingredientes_bp.route("/ingredientes_admin")
def ingredientes_admin():
    termo = request.args.get("q", "").strip()
    excluir_tecnicos = request.args.get("excluir_tecnicos", "0") == "1"

    conn = conectar_banco()
    cursor = conn.cursor()

    parametros = []
    filtros = []
    if termo:
        filtros.append("i.nome LIKE ?")
        parametros.append(f"%{termo}%")
    if excluir_tecnicos:
        filtros.append("COALESCE(i.fonte_nutricional, '') <> 'RECEITA_TECNICA'")

    filtro = "WHERE " + " AND ".join(filtros) if filtros else ""

    cursor.execute(f"""
        SELECT
            i.*,
            (
                SELECT COUNT(DISTINCT ri.receita_id)
                FROM receita_itens ri
                WHERE ri.ingrediente_id = i.id
            ) AS receitas_usando
        FROM ingredientes i
        {filtro}
        ORDER BY i.nome ASC
        LIMIT 150
    """, parametros)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([montar_ingrediente_admin(item) for item in linhas])


@ingredientes_bp.route("/ingrediente_admin/<int:ingrediente_id>")
def ingrediente_admin_detalhe(ingrediente_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            i.*,
            (
                SELECT COUNT(DISTINCT ri.receita_id)
                FROM receita_itens ri
                WHERE ri.ingrediente_id = i.id
            ) AS receitas_usando
        FROM ingredientes i
        WHERE i.id = ?
    """, (int(ingrediente_id),))
    ingrediente = cursor.fetchone()
    conn.close()

    if not ingrediente:
        return jsonify({"status": "erro", "mensagem": "Ingrediente nao encontrado."}), 404

    return jsonify({"status": "sucesso", "ingrediente": montar_ingrediente_admin(ingrediente)})
