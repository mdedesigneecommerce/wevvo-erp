"""Rotas de Categorias do Wevvo ERP/PDV.

Sprint 05: migração do módulo de categorias para Blueprint.
Mantém os mesmos endpoints e contratos JSON utilizados pelo frontend atual.
"""

import sqlite3

from flask import Blueprint, jsonify, request, render_template_string

from config import BANCO
from services.core import agora_brasilia, coluna_existe


categorias_bp = Blueprint("categorias", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def garantir_categoria(cursor, nome):
    nome = str(nome or "").strip()
    if not nome:
        return None

    cursor.execute("SELECT id FROM categorias WHERE LOWER(nome) = LOWER(?)", (nome,))
    categoria = cursor.fetchone()
    if categoria:
        return int(categoria["id"])

    cursor.execute("""
        INSERT INTO categorias (nome, created_at, updated_at)
        VALUES (?, ?, ?)
    """, (nome, agora_brasilia(), agora_brasilia()))
    return cursor.lastrowid


def sincronizar_categorias_existentes(cursor):
    fontes = []

    if coluna_existe(cursor, "receitas", "categoria_produto"):
        cursor.execute("""
            SELECT DISTINCT TRIM(categoria_produto) AS nome
            FROM receitas
            WHERE COALESCE(TRIM(categoria_produto), '') <> ''
        """)
        fontes.extend([item["nome"] for item in cursor.fetchall()])

    if coluna_existe(cursor, "receitas", "categoria_nome"):
        cursor.execute("""
            SELECT DISTINCT TRIM(categoria_nome) AS nome
            FROM receitas
            WHERE COALESCE(TRIM(categoria_nome), '') <> ''
        """)
        fontes.extend([item["nome"] for item in cursor.fetchall()])

    if coluna_existe(cursor, "produtos_finais", "categoria"):
        cursor.execute("""
            SELECT DISTINCT TRIM(categoria) AS nome
            FROM produtos_finais
            WHERE COALESCE(TRIM(categoria), '') <> ''
        """)
        fontes.extend([item["nome"] for item in cursor.fetchall()])

    for nome in sorted(set(fontes)):
        garantir_categoria(cursor, nome)

    cursor.execute("""
        SELECT id, nome
        FROM categorias
    """)
    categorias = cursor.fetchall()

    for categoria in categorias:
        cursor.execute("""
            UPDATE receitas
            SET categoria_id = ?,
                categoria_nome = ?
            WHERE COALESCE(categoria_id, 0) = 0
              AND (
                    LOWER(COALESCE(categoria_produto, '')) = LOWER(?)
                    OR LOWER(COALESCE(categoria_nome, '')) = LOWER(?)
              )
        """, (categoria["id"], categoria["nome"], categoria["nome"], categoria["nome"]))

@categorias_bp.route("/categorias_admin")
def categorias_admin():
    conn = conectar_banco()
    cursor = conn.cursor()
    sincronizar_categorias_existentes(cursor)
    conn.commit()

    cursor.execute("""
        SELECT id, nome, descricao
        FROM categorias
        ORDER BY nome ASC
    """)
    categorias = [dict(item) for item in cursor.fetchall()]

    resposta = []
    for categoria in categorias:
        cursor.execute("""
            SELECT id, nome, custo_total, preco_venda, rendimento
            FROM receitas
            WHERE categoria_id = ?
               OR LOWER(COALESCE(categoria_nome, '')) = LOWER(?)
               OR LOWER(COALESCE(categoria_produto, '')) = LOWER(?)
            ORDER BY nome ASC
        """, (categoria["id"], categoria["nome"], categoria["nome"]))
        receitas = [dict(item) for item in cursor.fetchall()]
        categoria["receitas"] = receitas
        categoria["total_receitas"] = len(receitas)
        categoria["custo_total"] = round(sum(float(item["custo_total"] or 0) for item in receitas), 2)
        resposta.append(categoria)

    cursor.execute("""
        SELECT id, nome, custo_total, preco_venda, rendimento
        FROM receitas
        WHERE COALESCE(categoria_id, 0) = 0
          AND COALESCE(TRIM(categoria_nome), '') = ''
          AND COALESCE(TRIM(categoria_produto), '') = ''
        ORDER BY nome ASC
    """)
    sem_categoria = [dict(item) for item in cursor.fetchall()]
    if sem_categoria:
        resposta.append({
            "id": None,
            "nome": "Sem categoria",
            "descricao": "",
            "receitas": sem_categoria,
            "total_receitas": len(sem_categoria),
            "custo_total": round(sum(float(item["custo_total"] or 0) for item in sem_categoria), 2)
        })

    conn.close()
    return jsonify(resposta)


@categorias_bp.route("/categorias_select")
def categorias_select():
    conn = conectar_banco()
    cursor = conn.cursor()
    sincronizar_categorias_existentes(cursor)
    conn.commit()
    cursor.execute("SELECT id, nome FROM categorias ORDER BY nome ASC")
    categorias = cursor.fetchall()
    conn.close()
    return jsonify([{"id": item["id"], "nome": item["nome"]} for item in categorias])


@categorias_bp.route("/salvar_categoria", methods=["POST"])
def salvar_categoria():
    dados = request.json or {}
    categoria_id = dados.get("id")
    nome = str(dados.get("nome", "")).strip()
    descricao = str(dados.get("descricao", "")).strip()

    if not nome:
        return jsonify({"status": "erro", "mensagem": "Nome da categoria e obrigatorio."}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    try:
        if categoria_id:
            categoria_id = int(categoria_id)
            cursor.execute("SELECT nome FROM categorias WHERE id = ?", (categoria_id,))
            antiga = cursor.fetchone()
            if not antiga:
                conn.close()
                return jsonify({"status": "erro", "mensagem": "Categoria nao encontrada."}), 404

            cursor.execute("""
                UPDATE categorias
                SET nome = ?, descricao = ?, updated_at = ?
                WHERE id = ?
            """, (nome, descricao, agora_brasilia(), categoria_id))

            cursor.execute("""
                UPDATE receitas
                SET categoria_nome = ?,
                    categoria_produto = CASE
                        WHEN COALESCE(categoria_produto, '') = '' OR LOWER(categoria_produto) = LOWER(?) THEN ?
                        ELSE categoria_produto
                    END,
                    updated_at = ?
                WHERE categoria_id = ?
                   OR LOWER(COALESCE(categoria_nome, '')) = LOWER(?)
                   OR LOWER(COALESCE(categoria_produto, '')) = LOWER(?)
            """, (nome, antiga["nome"], nome, agora_brasilia(), categoria_id, antiga["nome"], antiga["nome"]))
        else:
            categoria_id = garantir_categoria(cursor, nome)
            cursor.execute("""
                UPDATE categorias
                SET descricao = ?, updated_at = ?
                WHERE id = ?
            """, (descricao, agora_brasilia(), categoria_id))

        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Ja existe uma categoria com esse nome."}), 400

    conn.close()
    return jsonify({"status": "sucesso", "id": categoria_id})


@categorias_bp.route("/excluir_categoria/<int:categoria_id>", methods=["DELETE"])
def excluir_categoria(categoria_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT nome FROM categorias WHERE id = ?", (categoria_id,))
    categoria = cursor.fetchone()
    if not categoria:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Categoria nao encontrada."}), 404

    cursor.execute("""
        UPDATE receitas
        SET categoria_id = NULL,
            categoria_nome = NULL,
            categoria_produto = NULL,
            updated_at = ?
        WHERE categoria_id = ?
           OR LOWER(COALESCE(categoria_nome, '')) = LOWER(?)
           OR LOWER(COALESCE(categoria_produto, '')) = LOWER(?)
    """, (agora_brasilia(), categoria_id, categoria["nome"], categoria["nome"]))

    cursor.execute("""
        UPDATE produtos_finais
        SET categoria = NULL,
            updated_at = ?
        WHERE LOWER(COALESCE(categoria, '')) = LOWER(?)
    """, (agora_brasilia(), categoria["nome"]))

    cursor.execute("DELETE FROM categorias WHERE id = ?", (categoria_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso"})


@categorias_bp.route("/vincular_receita_categoria", methods=["POST"])
def vincular_receita_categoria():
    dados = request.json or {}
    receita_id = int(dados.get("receita_id") or 0)
    categoria_id = dados.get("categoria_id")

    conn = conectar_banco()
    cursor = conn.cursor()

    if categoria_id:
        cursor.execute("SELECT id, nome FROM categorias WHERE id = ?", (int(categoria_id),))
        categoria = cursor.fetchone()
        if not categoria:
            conn.close()
            return jsonify({"status": "erro", "mensagem": "Categoria nao encontrada."}), 404
        cursor.execute("""
            UPDATE receitas
            SET categoria_id = ?, categoria_nome = ?, categoria_produto = ?, updated_at = ?
            WHERE id = ?
        """, (categoria["id"], categoria["nome"], categoria["nome"], agora_brasilia(), receita_id))
    else:
        cursor.execute("""
            UPDATE receitas
            SET categoria_id = NULL, categoria_nome = NULL, categoria_produto = NULL, updated_at = ?
            WHERE id = ?
        """, (agora_brasilia(), receita_id))

    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso"})


@categorias_bp.route("/imprimir_categoria/<int:categoria_id>")
def imprimir_categoria(categoria_id):
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, descricao FROM categorias WHERE id = ?", (categoria_id,))
    categoria = cursor.fetchone()
    if not categoria:
        conn.close()
        return "Categoria nao encontrada.", 404

    cursor.execute("""
        SELECT id, nome, rendimento, custo_total, custo_porcao, preco_venda, preco_venda_porcao
        FROM receitas
        WHERE categoria_id = ?
           OR LOWER(COALESCE(categoria_nome, '')) = LOWER(?)
           OR LOWER(COALESCE(categoria_produto, '')) = LOWER(?)
        ORDER BY nome ASC
    """, (categoria_id, categoria["nome"], categoria["nome"]))
    receitas = cursor.fetchall()
    conn.close()

    return render_template_string("""
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Categoria {{ categoria["nome"] }}</title>
<style>
body { font-family: Arial, sans-serif; padding: 24px; color:#111827; }
h1 { margin-bottom: 4px; }
table { width:100%; border-collapse: collapse; margin-top: 18px; }
th, td { border:1px solid #d1d5db; padding:8px; text-align:left; }
th { background:#e5e7eb; }
@media print { button { display:none; } }
</style>
</head>
<body>
<button onclick="window.print()">Imprimir</button>
<h1>{{ categoria["nome"] }}</h1>
<p>{{ categoria["descricao"] or "" }}</p>
<table>
<thead><tr><th>Receita</th><th>Rendimento</th><th>Custo total</th><th>Custo por porcao</th><th>Venda total</th><th>Venda por porcao</th></tr></thead>
<tbody>
{% for receita in receitas %}
<tr>
<td>{{ receita["nome"] }}</td>
<td>{{ receita["rendimento"] }}</td>
<td>R$ {{ "%.2f"|format(receita["custo_total"] or 0) }}</td>
<td>R$ {{ "%.2f"|format(receita["custo_porcao"] or 0) }}</td>
<td>R$ {{ "%.2f"|format(receita["preco_venda"] or 0) }}</td>
<td>R$ {{ "%.2f"|format(receita["preco_venda_porcao"] or 0) }}</td>
</tr>
{% else %}
<tr><td colspan="6">Nenhuma receita nesta categoria.</td></tr>
{% endfor %}
</tbody>
</table>
<script>window.onload = () => setTimeout(() => window.print(), 300);</script>
</body>
</html>
    """, categoria=categoria, receitas=receitas)
