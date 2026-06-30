"""Rotas principais do Wevvo ERP/PDV.

Sprint 08: migração da tela inicial e listagem de receitas salvas para Blueprint.
Mantém os mesmos endpoints usados pelo frontend atual:
- /
- /receitas_salvas
"""

import sqlite3

from flask import Blueprint, jsonify, render_template, request

from config import BANCO
from services.core import agora_brasilia


home_bp = Blueprint("home", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def calcular_custo_por_quantidade(preco, unidade, quantidade_base):
    """Calcula custo conforme unidade de estoque do ingrediente."""
    preco = float(preco or 0)
    quantidade_base = float(quantidade_base or 0)
    unidade = str(unidade or "KG").upper().strip()

    if unidade == "KG":
        return (quantidade_base / 1000) * preco
    if unidade == "G":
        return quantidade_base * preco
    if unidade == "L":
        return (quantidade_base / 1000) * preco
    if unidade == "ML":
        return quantidade_base * preco
    if unidade == "UN":
        return quantidade_base * preco

    return (quantidade_base / 1000) * preco


def calcular_totais_receita_por_id(cursor, receita_id):
    cursor.execute("""
        SELECT rendimento, margem_lucro
        FROM receitas
        WHERE id = ?
    """, (int(receita_id),))
    receita = cursor.fetchone()

    if not receita:
        return None

    rendimento = float(receita["rendimento"] or 1)
    if rendimento <= 0:
        rendimento = 1

    margem_lucro = float(receita["margem_lucro"] or 0) / 100

    cursor.execute("""
        SELECT ingrediente_id, gramas
        FROM receita_itens
        WHERE receita_id = ?
    """, (int(receita_id),))
    itens = cursor.fetchall()

    custo_total = 0
    total_gramas_receita = 0
    nutrientes = {
        "calorias": 0,
        "carbos": 0,
        "acucares_totais": 0,
        "acucares_adicionados": 0,
        "proteinas": 0,
        "gorduras": 0,
        "gorduras_saturadas": 0,
        "gorduras_trans": 0,
        "fibra": 0,
        "sodio": 0,
    }

    for item in itens:
        ingrediente_id = item["ingrediente_id"]
        gramas = float(item["gramas"] or 0)

        if not ingrediente_id or gramas <= 0:
            continue

        cursor.execute("""
            SELECT
                preco_kg,
                unidade,
                calorias_100g,
                carboidratos_100g,
                acucares_totais_100g,
                acucares_adicionados_100g,
                proteinas_100g,
                gorduras_100g,
                gorduras_saturadas_100g,
                gorduras_trans_100g,
                fibra_alimentar_100g,
                sodio_100g
            FROM ingredientes
            WHERE id = ?
        """, (int(ingrediente_id),))
        ingrediente = cursor.fetchone()

        if not ingrediente:
            continue

        total_gramas_receita += gramas
        custo_total += calcular_custo_por_quantidade(
            ingrediente["preco_kg"],
            ingrediente["unidade"],
            gramas,
        )

        proporcao = gramas / 100
        nutrientes["calorias"] += float(ingrediente["calorias_100g"] or 0) * proporcao
        nutrientes["carbos"] += float(ingrediente["carboidratos_100g"] or 0) * proporcao
        nutrientes["acucares_totais"] += float(ingrediente["acucares_totais_100g"] or 0) * proporcao
        nutrientes["acucares_adicionados"] += float(ingrediente["acucares_adicionados_100g"] or 0) * proporcao
        nutrientes["proteinas"] += float(ingrediente["proteinas_100g"] or 0) * proporcao
        nutrientes["gorduras"] += float(ingrediente["gorduras_100g"] or 0) * proporcao
        nutrientes["gorduras_saturadas"] += float(ingrediente["gorduras_saturadas_100g"] or 0) * proporcao
        nutrientes["gorduras_trans"] += float(ingrediente["gorduras_trans_100g"] or 0) * proporcao
        nutrientes["fibra"] += float(ingrediente["fibra_alimentar_100g"] or 0) * proporcao
        nutrientes["sodio"] += float(ingrediente["sodio_100g"] or 0) * proporcao

    preco_venda_total = custo_total * (1 + margem_lucro)

    if total_gramas_receita <= 0:
        total_gramas_receita = 1

    return {
        "custo_total": round(custo_total, 2),
        "custo_porcao": round(custo_total / rendimento, 2),
        "preco_venda": round(preco_venda_total, 2),
        "preco_venda_porcao": round(preco_venda_total / rendimento, 2),
        "calorias": round((nutrientes["calorias"] / total_gramas_receita) * 100, 1),
        "carboidratos": round((nutrientes["carbos"] / total_gramas_receita) * 100, 1),
        "acucares_totais": round((nutrientes["acucares_totais"] / total_gramas_receita) * 100, 1),
        "acucares_adicionados": round((nutrientes["acucares_adicionados"] / total_gramas_receita) * 100, 1),
        "proteinas": round((nutrientes["proteinas"] / total_gramas_receita) * 100, 1),
        "gorduras": round((nutrientes["gorduras"] / total_gramas_receita) * 100, 1),
        "gorduras_saturadas": round((nutrientes["gorduras_saturadas"] / total_gramas_receita) * 100, 1),
        "gorduras_trans": round((nutrientes["gorduras_trans"] / total_gramas_receita) * 100, 1),
        "fibra": round((nutrientes["fibra"] / total_gramas_receita) * 100, 1),
        "sodio": round((nutrientes["sodio"] / total_gramas_receita) * 100, 1),
    }


def recalcular_receitas_salvas(cursor, ingrediente_id=None):
    if ingrediente_id:
        cursor.execute("""
            SELECT DISTINCT receita_id
            FROM receita_itens
            WHERE ingrediente_id = ?
        """, (int(ingrediente_id),))
    else:
        cursor.execute("""
            SELECT id AS receita_id
            FROM receitas
        """)

    receitas = cursor.fetchall()
    total_recalculadas = 0

    for receita in receitas:
        receita_id = receita["receita_id"]
        totais = calcular_totais_receita_por_id(cursor, receita_id)

        if not totais:
            continue

        cursor.execute("""
            UPDATE receitas
            SET
                custo_total = ?,
                custo_porcao = ?,
                preco_venda = ?,
                preco_venda_porcao = ?,
                calorias = ?,
                carboidratos = ?,
                proteinas = ?,
                gorduras = ?,
                acucares_totais = ?,
                acucares_adicionados = ?,
                gorduras_saturadas = ?,
                gorduras_trans = ?,
                fibra = ?,
                sodio = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            totais["custo_total"],
            totais["custo_porcao"],
            totais["preco_venda"],
            totais["preco_venda_porcao"],
            totais["calorias"],
            totais["carboidratos"],
            totais["proteinas"],
            totais["gorduras"],
            totais["acucares_totais"],
            totais["acucares_adicionados"],
            totais["gorduras_saturadas"],
            totais["gorduras_trans"],
            totais["fibra"],
            totais["sodio"],
            agora_brasilia(),
            int(receita_id),
        ))
        total_recalculadas += 1

    return total_recalculadas


@home_bp.route("/")
def index():
    conn = conectar_banco()
    cursor = conn.cursor()

    # Garante que a tela inicial sempre mostre receitas atualizadas
    # com base nos preços e unidades atuais dos ingredientes.
    recalcular_receitas_salvas(cursor)
    conn.commit()

    cursor.execute("""
        SELECT id, nome, rendimento, custo_total, preco_venda, categoria_id, categoria_nome
        FROM receitas
        WHERE LOWER(COALESCE(nome, '')) NOT LIKE '%combo%'
          AND LOWER(COALESCE(categoria_nome, '')) NOT LIKE '%combo%'
          AND LOWER(COALESCE(categoria_produto, '')) NOT LIKE '%combo%'
        ORDER BY id DESC
    """)

    receitas_salvas = cursor.fetchall()
    conn.close()

    return render_template("index.html", receitas_salvas=receitas_salvas)


@home_bp.route("/receitas_salvas")
def receitas_salvas_json():
    tipo = request.args.get("tipo", "receita").strip().lower()
    conn = conectar_banco()
    cursor = conn.cursor()

    recalcular_receitas_salvas(cursor)
    conn.commit()

    filtro_combo = """
        LOWER(COALESCE(nome, '')) LIKE '%combo%'
        OR LOWER(COALESCE(categoria_nome, '')) LIKE '%combo%'
        OR LOWER(COALESCE(categoria_produto, '')) LIKE '%combo%'
    """

    where = f"WHERE NOT ({filtro_combo})"
    if tipo == "combo":
        where = f"WHERE ({filtro_combo})"
    elif tipo in ["todas", "todos"]:
        where = ""

    cursor.execute(f"""
        SELECT id, nome, rendimento, custo_total, preco_venda, categoria_id, categoria_nome
        FROM receitas
        {where}
        ORDER BY id DESC
    """)

    receitas = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "nome": item["nome"],
            "rendimento": item["rendimento"],
            "custo_total": item["custo_total"],
            "preco_venda": item["preco_venda"],
            "categoria_id": item["categoria_id"],
            "categoria_nome": item["categoria_nome"] or "Sem categoria",
        }
        for item in receitas
    ])
