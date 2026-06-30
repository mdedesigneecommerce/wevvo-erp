"""Rotas do Dashboard do Wevvo ERP/PDV.

Sprint 04: primeira extração real do app.py.
Estas rotas foram migradas mantendo os mesmos caminhos HTTP e o mesmo contrato JSON
para preservar compatibilidade com o frontend atual.
"""

from datetime import datetime, timedelta
import sqlite3

from flask import Blueprint, jsonify

from config import BANCO
from services.core import data_brasilia_obj
from services.dashboard_service import gerar_dashboard_executivo


dashboard_bp = Blueprint("dashboard", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


@dashboard_bp.route("/dashboard_gerencial")
def dashboard_gerencial():
    """Dashboard executivo: leitura consolidada dos módulos sem alterar vínculos existentes."""
    conn = conectar_banco()
    cursor = conn.cursor()

    hoje = data_brasilia_obj().date()
    hoje_iso = hoje.isoformat()
    limite_validade = hoje + timedelta(days=7)

    def valor_unico(sql, params=(), padrao=0):
        try:
            cursor.execute(sql, params)
            linha = cursor.fetchone()
            if not linha:
                return padrao
            valor = list(dict(linha).values())[0]
            return valor if valor is not None else padrao
        except Exception:
            return padrao

    def lista(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(linha) for linha in cursor.fetchall()]
        except Exception:
            return []

    filtro_combo = """
        LOWER(COALESCE(nome, '')) LIKE '%combo%'
        OR LOWER(COALESCE(categoria_nome, '')) LIKE '%combo%'
        OR LOWER(COALESCE(categoria_produto, '')) LIKE '%combo%'
    """
    filtro_receita = f"NOT ({filtro_combo})"

    total_receitas = int(valor_unico(f"SELECT COUNT(*) AS total FROM receitas WHERE {filtro_receita}") or 0)
    total_combos = int(valor_unico(f"SELECT COUNT(*) AS total FROM receitas WHERE {filtro_combo}") or 0)
    total_ingredientes = int(valor_unico("SELECT COUNT(*) AS total FROM ingredientes") or 0)
    total_produtos = int(valor_unico("SELECT COUNT(*) AS total FROM produtos_finais WHERE COALESCE(ativo, 1) = 1") or 0)

    producoes_hoje = int(valor_unico("""
        SELECT COUNT(*) AS total
        FROM producoes
        WHERE DATE(COALESCE(created_at, '')) = DATE(?)
    """, (hoje_iso,)) or 0)

    estoque_baixo_total = int(valor_unico("""
        SELECT COUNT(*) AS total
        FROM ingredientes
        WHERE estoque_minimo > 0
          AND estoque_atual <= estoque_minimo
    """) or 0)

    produtos_sem_preco = int(valor_unico("""
        SELECT COUNT(*) AS total
        FROM produtos_finais
        WHERE COALESCE(ativo, 1) = 1
          AND COALESCE(preco_venda, 0) <= 0
    """) or 0)

    receitas_sem_embalagem = int(valor_unico("""
        SELECT COUNT(*) AS total
        FROM produtos_finais pf
        LEFT JOIN cmv_precificacao cmv ON cmv.entidade_tipo = 'produto_final' AND cmv.entidade_id = pf.id
        WHERE COALESCE(pf.ativo, 1) = 1
          AND COALESCE(cmv.embalagem_unitaria, 0) <= 0
    """) or 0)

    lotes_alerta = []
    for lote in lista("""
        SELECT e.produto_final_nome, e.lote, e.saldo_atual, e.data_validade
        FROM estoque_produto_acabado e
        INNER JOIN produtos_finais pf ON pf.id = e.produto_final_id
        WHERE COALESCE(pf.ativo, 1) = 1
          AND e.saldo_atual > 0
          AND COALESCE(e.data_validade, '') <> ''
        ORDER BY e.data_validade ASC, e.produto_final_nome ASC
        LIMIT 80
    """):
        try:
            validade = datetime.strptime(str(lote.get("data_validade") or ""), "%d/%m/%Y").date()
        except Exception:
            continue
        if validade <= limite_validade:
            lote["dias"] = (validade - hoje).days
            lotes_alerta.append(lote)
    lotes_vencendo = len(lotes_alerta)

    estoque_baixo = lista("""
        SELECT nome, estoque_atual, estoque_minimo, unidade
        FROM ingredientes
        WHERE estoque_minimo > 0
          AND estoque_atual <= estoque_minimo
        ORDER BY nome ASC
        LIMIT 10
    """)

    valor_estoque = float(valor_unico("""
        SELECT COALESCE(SUM(epa.saldo_atual * COALESCE(pf.preco_venda, 0)), 0) AS total
        FROM estoque_produto_acabado epa
        INNER JOIN produtos_finais pf ON pf.id = epa.produto_final_id
        WHERE COALESCE(pf.ativo, 1) = 1
          AND epa.saldo_atual > 0
    """) or 0)

    produtos_financeiro = lista("""
        SELECT
            pf.id,
            pf.nome,
            COALESCE(pf.preco_venda, 0) AS preco_venda,
            COALESCE(r.custo_total, 0) AS custo_receita,
            COALESCE(r.rendimento, 1) AS rendimento,
            COALESCE(cmv.embalagem_unitaria, 0) AS embalagem_unitaria,
            COALESCE(cmv.custo_fixo_unitario, 0) AS custo_fixo_unitario,
            COALESCE(cmv.custo_variavel_percentual, 0) AS custo_variavel_percentual,
            COALESCE(cmv.preco_praticado, 0) AS preco_praticado
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        LEFT JOIN cmv_precificacao cmv ON cmv.entidade_tipo = 'produto_final' AND cmv.entidade_id = pf.id
        WHERE COALESCE(pf.ativo, 1) = 1
        ORDER BY pf.nome ASC
        LIMIT 300
    """)

    margens = []
    cmvs = []
    lucro_estimado = 0.0
    abaixo_cmv = 0
    produtos_atencao = []
    for item in produtos_financeiro:
        preco = float(item.get("preco_praticado") or item.get("preco_venda") or 0)
        rendimento = float(item.get("rendimento") or 1) or 1
        custo_receita = float(item.get("custo_receita") or 0)
        custo_base = custo_receita / rendimento if rendimento > 0 else custo_receita
        custo = custo_base + float(item.get("embalagem_unitaria") or 0) + float(item.get("custo_fixo_unitario") or 0)
        custo += preco * (float(item.get("custo_variavel_percentual") or 0) / 100.0)
        lucro = preco - custo
        if preco > 0:
            margem = (lucro / preco) * 100
            cmv_pct = (custo / preco) * 100
            margens.append(margem)
            cmvs.append(cmv_pct)
            lucro_estimado += lucro
            if lucro < 0:
                abaixo_cmv += 1
                produtos_atencao.append({"nome": item.get("nome"), "motivo": "Preço de venda abaixo do custo calculado", "status": "Abaixo do CMV"})
            elif margem < 25:
                produtos_atencao.append({"nome": item.get("nome"), "motivo": f"Margem baixa: {margem:.1f}%", "status": "Atenção"})
        else:
            produtos_atencao.append({"nome": item.get("nome"), "motivo": "Produto ativo sem preço de venda", "status": "Sem preço"})
    produtos_atencao = produtos_atencao[:10]

    margem_media = round(sum(margens) / len(margens), 2) if margens else 0
    cmv_medio = round(sum(cmvs) / len(cmvs), 2) if cmvs else 0

    alertas = []
    if estoque_baixo_total:
        alertas.append({"titulo": f"{estoque_baixo_total} ingrediente(s) com estoque baixo", "detalhe": "Revisar compras e mínimo de estoque.", "modulo": "estoque"})
    if lotes_vencendo:
        alertas.append({"titulo": f"{lotes_vencendo} lote(s) vencidos ou próximos", "detalhe": "Verificar venda, baixa ou produção conforme validade.", "modulo": "conferencia"})
    if produtos_sem_preco:
        alertas.append({"titulo": f"{produtos_sem_preco} produto(s) sem preço", "detalhe": "Cadastrar preço praticado no CMV/Precificação.", "modulo": "cmv"})
    if abaixo_cmv:
        alertas.append({"titulo": f"{abaixo_cmv} produto(s) abaixo do CMV", "detalhe": "Revisar custo, embalagem, taxas e preço de venda.", "modulo": "cmv"})
    if receitas_sem_embalagem:
        alertas.append({"titulo": f"{receitas_sem_embalagem} produto(s) sem embalagem no CMV", "detalhe": "Adicionar embalagem para custo real.", "modulo": "cmv"})

    alertas_ativos = len(alertas)
    penalidade = min(75, estoque_baixo_total * 2 + lotes_vencendo * 4 + produtos_sem_preco * 5 + abaixo_cmv * 8)
    saude_sistema = max(0, 100 - penalidade)
    if saude_sistema >= 85:
        saude_texto = "Operação saudável. Continue acompanhando estoque, validade e CMV."
    elif saude_sistema >= 65:
        saude_texto = "Operação em atenção. Existem pontos para corrigir antes que afetem lucro ou estoque."
    else:
        saude_texto = "Operação crítica. Priorize estoque baixo, lotes vencendo e produtos sem preço."

    resumo = {
        "receitas": total_receitas,
        "combos": total_combos,
        "ingredientes": total_ingredientes,
        "produtos_finais": total_produtos,
        "producoes": int(valor_unico("SELECT COUNT(*) AS total FROM producoes") or 0),
        "producoes_hoje": producoes_hoje,
        "estoque_baixo_ingredientes": estoque_baixo_total,
        "lotes_com_saldo": int(valor_unico("""
            SELECT COUNT(*) AS total
            FROM estoque_produto_acabado e
            INNER JOIN produtos_finais pf ON pf.id = e.produto_final_id
            WHERE COALESCE(pf.ativo, 1) = 1
              AND e.saldo_atual > 0
        """) or 0),
        "lotes_vencendo": lotes_vencendo,
        "alertas_ativos": alertas_ativos,
        "produtos_sem_preco": produtos_sem_preco,
        "produtos_abaixo_cmv": abaixo_cmv,
        "valor_estoque_produto_acabado": round(valor_estoque, 2),
        "custo_total_produzido": round(float(valor_unico("SELECT COALESCE(SUM(custo_total), 0) AS total FROM producoes") or 0), 2),
        "cmv_medio_percentual": cmv_medio,
        "margem_media_percentual": margem_media,
        "lucro_estimado": round(lucro_estimado, 2),
        "saude_sistema": saude_sistema,
        "saude_texto": saude_texto
    }

    conn.close()
    return jsonify({
        "status": "sucesso",
        "resumo": resumo,
        "alertas": alertas[:8],
        "estoque_baixo": estoque_baixo,
        "lotes": lotes_alerta[:10],
        "produtos_atencao": produtos_atencao
    })


@dashboard_bp.route("/dashboard_gerencial_fase3")
def dashboard_gerencial_fase3():
    """Dashboard gerencial: tendências, rankings e leitura operacional sem alterar módulos existentes."""
    conn = conectar_banco()
    cursor = conn.cursor()
    hoje = data_brasilia_obj().date()

    def lista(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(linha) for linha in cursor.fetchall()]
        except Exception:
            return []

    def valor_unico(sql, params=(), padrao=0):
        try:
            cursor.execute(sql, params)
            linha = cursor.fetchone()
            if not linha:
                return padrao
            valor = list(dict(linha).values())[0]
            return valor if valor is not None else padrao
        except Exception:
            return padrao

    meses = []
    for i in range(5, -1, -1):
        ano = hoje.year
        mes = hoje.month - i
        while mes <= 0:
            mes += 12
            ano -= 1
        chave = f"{ano:04d}-{mes:02d}"
        rotulo = f"{mes:02d}/{str(ano)[2:]}"
        meses.append({"chave": chave, "rotulo": rotulo})

    producoes_por_mes = []
    saidas_por_mes = []
    for mes in meses:
        qtd_producoes = float(valor_unico("""
            SELECT COALESCE(SUM(quantidade), 0) AS total
            FROM producoes
            WHERE substr(COALESCE(created_at, ''), 1, 7) = ?
        """, (mes["chave"],)) or 0)
        custo_produzido = float(valor_unico("""
            SELECT COALESCE(SUM(custo_total), 0) AS total
            FROM producoes
            WHERE substr(COALESCE(created_at, ''), 1, 7) = ?
        """, (mes["chave"],)) or 0)
        producoes_por_mes.append({
            "mes": mes["rotulo"],
            "quantidade": round(qtd_producoes, 3),
            "custo": round(custo_produzido, 2)
        })

        qtd_saidas = float(valor_unico("""
            SELECT COALESCE(SUM(quantidade), 0) AS total
            FROM movimentacoes_produto_acabado
            WHERE UPPER(COALESCE(tipo, '')) = 'SAIDA'
              AND substr(COALESCE(created_at, ''), 1, 7) = ?
        """, (mes["chave"],)) or 0)
        receita_saida = float(valor_unico("""
            SELECT COALESCE(SUM(m.quantidade * COALESCE(pf.preco_venda, 0)), 0) AS total
            FROM movimentacoes_produto_acabado m
            LEFT JOIN produtos_finais pf ON pf.id = m.produto_final_id
            WHERE UPPER(COALESCE(m.tipo, '')) = 'SAIDA'
              AND COALESCE(pf.ativo, 1) = 1
              AND substr(COALESCE(m.created_at, ''), 1, 7) = ?
        """, (mes["chave"],)) or 0)
        saidas_por_mes.append({
            "mes": mes["rotulo"],
            "quantidade": round(qtd_saidas, 3),
            "receita": round(receita_saida, 2)
        })

    ingredientes_consumidos = lista("""
        SELECT ingrediente_nome AS nome,
               COALESCE(SUM(quantidade_baixada), 0) AS quantidade,
               COALESCE(unidade, 'KG') AS unidade
        FROM producao_itens
        GROUP BY ingrediente_nome, unidade
        ORDER BY quantidade DESC
        LIMIT 10
    """)

    produtos_mais_movimentados = lista("""
        SELECT m.produto_final_nome AS nome,
               COALESCE(SUM(m.quantidade), 0) AS quantidade,
               COALESCE(SUM(m.quantidade * COALESCE(pf.preco_venda, 0)), 0) AS valor
        FROM movimentacoes_produto_acabado m
        LEFT JOIN produtos_finais pf ON pf.id = m.produto_final_id
        WHERE UPPER(COALESCE(m.tipo, '')) = 'SAIDA'
          AND COALESCE(pf.ativo, 1) = 1
        GROUP BY m.produto_final_nome
        ORDER BY quantidade DESC
        LIMIT 10
    """)

    categorias = lista("""
        SELECT COALESCE(NULLIF(TRIM(categoria), ''), 'Sem categoria') AS categoria,
               COUNT(*) AS total
        FROM produtos_finais
        WHERE COALESCE(ativo, 1) = 1
        GROUP BY COALESCE(NULLIF(TRIM(categoria), ''), 'Sem categoria')
        ORDER BY total DESC
        LIMIT 8
    """)

    receitas_por_categoria = lista("""
        SELECT COALESCE(NULLIF(TRIM(categoria_nome), ''), NULLIF(TRIM(categoria_produto), ''), 'Sem categoria') AS categoria,
               COUNT(*) AS total
        FROM receitas
        GROUP BY COALESCE(NULLIF(TRIM(categoria_nome), ''), NULLIF(TRIM(categoria_produto), ''), 'Sem categoria')
        ORDER BY total DESC
        LIMIT 8
    """)

    estoque_parado = lista("""
        SELECT pf.nome,
               COALESCE(pf.estoque_atual, 0) AS estoque,
               COALESCE(pf.preco_venda, 0) AS preco_venda,
               COALESCE(pf.estoque_atual * pf.preco_venda, 0) AS valor_estimado
        FROM produtos_finais pf
        WHERE COALESCE(pf.ativo, 1) = 1
          AND COALESCE(pf.estoque_atual, 0) > 0
          AND pf.id NOT IN (
              SELECT DISTINCT produto_final_id
              FROM movimentacoes_produto_acabado
              WHERE UPPER(COALESCE(tipo, '')) = 'SAIDA'
                AND DATE(COALESCE(created_at, '')) >= DATE('now', '-30 day')
                AND produto_final_id IS NOT NULL
          )
        ORDER BY valor_estimado DESC
        LIMIT 10
    """)

    maior_custo_receitas = lista("""
        SELECT nome,
               COALESCE(custo_total, 0) AS custo_total,
               COALESCE(rendimento, 1) AS rendimento,
               CASE WHEN COALESCE(rendimento, 0) > 0 THEN COALESCE(custo_total, 0) / rendimento ELSE COALESCE(custo_total, 0) END AS custo_unitario
        FROM receitas
        ORDER BY custo_unitario DESC
        LIMIT 10
    """)

    producoes_ultimas = lista("""
        SELECT COALESCE(produto_final_nome, receita_nome) AS nome,
               quantidade,
               custo_total,
               lote,
               created_at
        FROM producoes
        ORDER BY id DESC
        LIMIT 8
    """)

    total_saida_30d = float(valor_unico("""
        SELECT COALESCE(SUM(quantidade), 0) AS total
        FROM movimentacoes_produto_acabado
        WHERE UPPER(COALESCE(tipo, '')) = 'SAIDA'
          AND DATE(COALESCE(created_at, '')) >= DATE('now', '-30 day')
    """) or 0)
    custo_producao_30d = float(valor_unico("""
        SELECT COALESCE(SUM(custo_total), 0) AS total
        FROM producoes
        WHERE DATE(COALESCE(created_at, '')) >= DATE('now', '-30 day')
    """) or 0)
    receita_saida_30d = float(valor_unico("""
        SELECT COALESCE(SUM(m.quantidade * COALESCE(pf.preco_venda, 0)), 0) AS total
        FROM movimentacoes_produto_acabado m
        LEFT JOIN produtos_finais pf ON pf.id = m.produto_final_id
        WHERE UPPER(COALESCE(m.tipo, '')) = 'SAIDA'
          AND COALESCE(pf.ativo, 1) = 1
          AND DATE(COALESCE(m.created_at, '')) >= DATE('now', '-30 day')
    """) or 0)

    resultado_30d = receita_saida_30d - custo_producao_30d
    margem_operacional_30d = (resultado_30d / receita_saida_30d * 100) if receita_saida_30d > 0 else 0

    resumo = {
        "saida_30d": round(total_saida_30d, 3),
        "receita_saida_30d": round(receita_saida_30d, 2),
        "custo_producao_30d": round(custo_producao_30d, 2),
        "resultado_30d": round(resultado_30d, 2),
        "margem_operacional_30d": round(margem_operacional_30d, 2),
        "ticket_medio_saida": round(receita_saida_30d / total_saida_30d, 2) if total_saida_30d > 0 else 0,
        "categorias_produtos": len(categorias),
        "ingredientes_consumidos": len(ingredientes_consumidos),
        "estoque_parado_itens": len(estoque_parado)
    }

    conn.close()
    return jsonify({
        "status": "sucesso",
        "resumo": resumo,
        "producoes_por_mes": producoes_por_mes,
        "saidas_por_mes": saidas_por_mes,
        "ingredientes_consumidos": ingredientes_consumidos,
        "produtos_mais_movimentados": produtos_mais_movimentados,
        "categorias_produtos": categorias,
        "categorias_receitas": receitas_por_categoria,
        "estoque_parado": estoque_parado,
        "maior_custo_receitas": maior_custo_receitas,
        "producoes_ultimas": producoes_ultimas
    })


@dashboard_bp.route("/dashboard_financeiro")
def dashboard_financeiro():
    """Dashboard financeiro: leitura gerencial de CMV, margem e precificação sem alterar os demais módulos."""
    conn = conectar_banco()
    cursor = conn.cursor()

    def lista(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(linha) for linha in cursor.fetchall()]
        except Exception:
            return []

    def cfg_precificacao(tipo, item_id):
        try:
            cursor.execute("""
                SELECT * FROM cmv_precificacao
                WHERE entidade_tipo = ? AND entidade_id = ?
            """, (tipo, int(item_id)))
            linha = cursor.fetchone()
            return dict(linha) if linha else {}
        except Exception:
            return {}

    itens = []
    rows = lista("""
        SELECT
            pf.id, pf.nome, COALESCE(pf.preco_venda, 0) AS preco_venda,
            COALESCE(pf.estoque_atual, 0) AS estoque_atual,
            COALESCE(r.custo_porcao, 0) AS custo_porcao,
            COALESCE(r.custo_total, 0) AS custo_total,
            COALESCE(r.rendimento, 1) AS rendimento
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        WHERE COALESCE(pf.ativo, 1) = 1
        ORDER BY pf.nome ASC
    """)

    for row in rows:
        cfg = cfg_precificacao("produto", row.get("id"))
        rendimento = float(row.get("rendimento") or 1) or 1
        custo_base = float(row.get("custo_porcao") or 0)
        if custo_base <= 0:
            custo_base = float(row.get("custo_total") or 0) / rendimento
        embalagem = float(cfg.get("embalagem_unitaria") or 0)
        fixo = float(cfg.get("custo_fixo_unitario") or 0)
        variavel_pct = float(cfg.get("custo_variavel_percentual") or 0)
        margem_desejada = float(cfg.get("margem_desejada_percentual") or 30)
        preco = float(cfg.get("preco_praticado") or row.get("preco_venda") or 0)
        custo_sem_taxa = custo_base + embalagem + fixo
        taxa_variavel = preco * variavel_pct / 100 if preco > 0 else 0
        custo_total = custo_sem_taxa + taxa_variavel
        lucro = preco - custo_total
        margem = (lucro / preco * 100) if preco > 0 else 0
        cmv_pct = (custo_total / preco * 100) if preco > 0 else 0
        markup = (preco / custo_sem_taxa) if custo_sem_taxa > 0 else 0
        divisor_min = 1 - (variavel_pct / 100)
        preco_minimo = custo_sem_taxa / divisor_min if custo_sem_taxa > 0 and divisor_min > 0 else 0
        divisor_sug = 1 - ((variavel_pct + margem_desejada) / 100)
        preco_sugerido = custo_sem_taxa / divisor_sug if custo_sem_taxa > 0 and divisor_sug > 0 else 0

        if preco <= 0:
            status = "Sem preço"
        elif lucro < 0:
            status = "Abaixo do CMV"
        elif margem < 20:
            status = "Margem baixa"
        else:
            status = "Saudável"

        estoque = float(row.get("estoque_atual") or 0)
        itens.append({
            "id": row.get("id"),
            "nome": row.get("nome") or "-",
            "custo_unitario": round(custo_total, 2),
            "custo_base": round(custo_base, 2),
            "preco_venda": round(preco, 2),
            "preco_minimo": round(preco_minimo, 2),
            "preco_sugerido": round(preco_sugerido, 2),
            "lucro_unitario": round(lucro, 2),
            "margem_percentual": round(margem, 2),
            "cmv_percentual": round(cmv_pct, 2),
            "markup": round(markup, 2),
            "estoque_atual": round(estoque, 3),
            "valor_estoque_venda": round(preco * estoque, 2),
            "lucro_potencial_estoque": round(lucro * estoque, 2),
            "status": status
        })

    precificados = [i for i in itens if i["preco_venda"] > 0]
    receita_prevista = sum(i["valor_estoque_venda"] for i in precificados)
    lucro_previsto = sum(i["lucro_potencial_estoque"] for i in precificados)
    custo_previsto = receita_prevista - lucro_previsto
    margem_media = (lucro_previsto / receita_prevista * 100) if receita_prevista > 0 else 0
    cmv_medio = (custo_previsto / receita_prevista * 100) if receita_prevista > 0 else 0

    resumo = {
        "produtos_ativos": len(itens),
        "produtos_precificados": len(precificados),
        "sem_preco": sum(1 for i in itens if i["status"] == "Sem preço"),
        "abaixo_cmv": sum(1 for i in itens if i["status"] == "Abaixo do CMV"),
        "margem_baixa": sum(1 for i in itens if i["status"] == "Margem baixa"),
        "saudaveis": sum(1 for i in itens if i["status"] == "Saudável"),
        "receita_prevista_estoque": round(receita_prevista, 2),
        "custo_previsto_estoque": round(custo_previsto, 2),
        "lucro_previsto_estoque": round(lucro_previsto, 2),
        "margem_media": round(margem_media, 2),
        "cmv_medio": round(cmv_medio, 2)
    }

    ranking_lucro = sorted([i for i in precificados if i["lucro_unitario"] > 0], key=lambda x: x["lucro_unitario"], reverse=True)[:8]
    ranking_risco = sorted([i for i in itens if i["status"] != "Saudável"], key=lambda x: (x["preco_venda"] > 0, x["margem_percentual"]))[:10]
    ranking_margem = sorted([i for i in precificados], key=lambda x: x["margem_percentual"], reverse=True)[:8]

    conn.close()
    return jsonify({
        "status": "sucesso",
        "resumo": resumo,
        "ranking_lucro": ranking_lucro,
        "ranking_margem": ranking_margem,
        "produtos_risco": ranking_risco
    })


@dashboard_bp.route("/dashboard_inteligencia")
def dashboard_inteligencia():
    """Etapa 5: inteligência gerencial incremental, sem alterar dados ou vínculos existentes."""
    conn = conectar_banco()
    cursor = conn.cursor()

    def lista(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(linha) for linha in cursor.fetchall()]
        except Exception:
            return []

    def valor_unico(sql, params=(), padrao=0):
        try:
            cursor.execute(sql, params)
            linha = cursor.fetchone()
            if not linha:
                return padrao
            valor = list(dict(linha).values())[0]
            return valor if valor is not None else padrao
        except Exception:
            return padrao

    def cfg_precificacao(tipo, item_id):
        try:
            cursor.execute("""
                SELECT * FROM cmv_precificacao
                WHERE entidade_tipo = ? AND entidade_id = ?
            """, (tipo, int(item_id)))
            linha = cursor.fetchone()
            return dict(linha) if linha else {}
        except Exception:
            return {}

    aumentos = lista("""
        SELECT i.id, i.nome, i.unidade,
               COALESCE(i.preco_kg, 0) AS preco_atual,
               COALESCE(i.ultimo_reajuste_valor, 0) AS reajuste_percentual,
               COUNT(DISTINCT ri.receita_id) AS receitas_impactadas
        FROM ingredientes i
        LEFT JOIN receita_itens ri ON ri.ingrediente_id = i.id
        WHERE UPPER(COALESCE(i.ultimo_reajuste_tipo, '')) = 'PERCENTUAL'
          AND COALESCE(i.ultimo_reajuste_valor, 0) >= 10
        GROUP BY i.id, i.nome, i.unidade, i.preco_kg, i.ultimo_reajuste_valor
        ORDER BY reajuste_percentual DESC, receitas_impactadas DESC
        LIMIT 8
    """)

    produtos = lista("""
        SELECT pf.id, pf.nome, COALESCE(pf.preco_venda, 0) AS preco_venda,
               COALESCE(pf.estoque_atual, 0) AS estoque_atual,
               COALESCE(r.custo_porcao, 0) AS custo_porcao,
               COALESCE(r.custo_total, 0) AS custo_total,
               COALESCE(r.rendimento, 1) AS rendimento
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        WHERE COALESCE(pf.ativo, 1) = 1
        ORDER BY pf.nome ASC
    """)

    produtos_baixa_margem = []
    for row in produtos:
        cfg = cfg_precificacao('produto', row.get('id'))
        rendimento = float(row.get('rendimento') or 1) or 1
        custo_base = float(row.get('custo_porcao') or 0)
        if custo_base <= 0:
            custo_base = float(row.get('custo_total') or 0) / rendimento
        embalagem = float(cfg.get('embalagem_unitaria') or 0)
        fixo = float(cfg.get('custo_fixo_unitario') or 0)
        variavel_pct = float(cfg.get('custo_variavel_percentual') or 0)
        margem_desejada = float(cfg.get('margem_desejada_percentual') or 30)
        preco_atual = float(cfg.get('preco_praticado') or row.get('preco_venda') or 0)
        custo_sem_taxa = custo_base + embalagem + fixo
        taxa = preco_atual * variavel_pct / 100 if preco_atual > 0 else 0
        custo_total = custo_sem_taxa + taxa
        lucro = preco_atual - custo_total
        margem_real = (lucro / preco_atual * 100) if preco_atual > 0 else 0
        divisor = 1 - ((variavel_pct + margem_desejada) / 100)
        preco_sugerido = custo_sem_taxa / divisor if custo_sem_taxa > 0 and divisor > 0 else 0
        if preco_atual <= 0 or lucro < 0 or margem_real < 20:
            produtos_baixa_margem.append({
                'id': row.get('id'),
                'nome': row.get('nome') or '-',
                'preco_atual': round(preco_atual, 2),
                'preco_sugerido': round(preco_sugerido, 2),
                'custo_unitario': round(custo_total, 2),
                'margem_real': round(margem_real, 1),
                'lucro_unitario': round(lucro, 2),
                'status': 'Sem preço' if preco_atual <= 0 else ('Prejuízo' if lucro < 0 else 'Margem baixa')
            })
    produtos_baixa_margem = sorted(produtos_baixa_margem, key=lambda x: (x['preco_atual'] > 0, x['margem_real']))[:8]

    estoque_parado_ingredientes = lista("""
        SELECT i.id, i.nome, i.unidade,
               COALESCE(i.estoque_atual, 0) AS estoque_atual,
               COALESCE(i.preco_kg, 0) AS preco_unitario,
               COALESCE(i.estoque_atual * i.preco_kg, 0) AS valor_estimado
        FROM ingredientes i
        WHERE COALESCE(i.estoque_atual, 0) > 0
          AND i.id NOT IN (
              SELECT DISTINCT ingrediente_id
              FROM movimentacoes_estoque
              WHERE DATE(COALESCE(created_at, '')) >= DATE('now', '-90 day')
                AND ingrediente_id IS NOT NULL
          )
        ORDER BY valor_estimado DESC
        LIMIT 8
    """)

    valor_parado = float(valor_unico("""
        SELECT COALESCE(SUM(i.estoque_atual * i.preco_kg), 0) AS total
        FROM ingredientes i
        WHERE COALESCE(i.estoque_atual, 0) > 0
          AND i.id NOT IN (
              SELECT DISTINCT ingrediente_id
              FROM movimentacoes_estoque
              WHERE DATE(COALESCE(created_at, '')) >= DATE('now', '-90 day')
                AND ingrediente_id IS NOT NULL
          )
    """) or 0)

    compras_sugeridas = lista("""
        SELECT nome, unidade,
               COALESCE(estoque_atual, 0) AS estoque_atual,
               COALESCE(estoque_minimo, 0) AS estoque_minimo,
               CASE
                   WHEN COALESCE(estoque_minimo, 0) > COALESCE(estoque_atual, 0)
                   THEN ROUND(COALESCE(estoque_minimo, 0) - COALESCE(estoque_atual, 0), 3)
                   ELSE 0
               END AS quantidade_sugerida
        FROM ingredientes
        WHERE COALESCE(estoque_minimo, 0) > 0
          AND COALESCE(estoque_atual, 0) <= COALESCE(estoque_minimo, 0)
        ORDER BY quantidade_sugerida DESC, nome ASC
        LIMIT 8
    """)

    acoes = []
    if aumentos:
        top = aumentos[0]
        acoes.append({
            'tipo': 'Custo',
            'titulo': f"{top.get('nome')} aumentou {round(float(top.get('reajuste_percentual') or 0), 1)}%",
            'detalhe': f"Impacta {int(top.get('receitas_impactadas') or 0)} receitas. Sugestão: revisar CMV e preço de venda.",
            'modulo': 'cmv'
        })
    if produtos_baixa_margem:
        p = produtos_baixa_margem[0]
        acoes.append({
            'tipo': 'Preço',
            'titulo': f"{p.get('nome')} precisa de reajuste",
            'detalhe': f"Preço atual R$ {p.get('preco_atual'):.2f}; sugerido R$ {p.get('preco_sugerido'):.2f}.",
            'modulo': 'cmv'
        })
    if valor_parado > 0:
        acoes.append({
            'tipo': 'Estoque',
            'titulo': f"R$ {valor_parado:.2f} parados em ingredientes",
            'detalhe': 'Ingredientes sem movimentação há 90 dias. Sugestão: analisar compras, perdas ou uso em receitas.',
            'modulo': 'estoque'
        })
    if compras_sugeridas:
        acoes.append({
            'tipo': 'Compras',
            'titulo': f"{len(compras_sugeridas)} ingrediente(s) abaixo do mínimo",
            'detalhe': 'Sugestão: gerar lista de compras antes da próxima produção.',
            'modulo': 'estoque'
        })

    conn.close()
    return jsonify({
        'status': 'sucesso',
        'resumo': {
            'alertas': len(acoes),
            'produtos_margem_baixa': len(produtos_baixa_margem),
            'ingredientes_com_aumento': len(aumentos),
            'valor_estoque_parado_90d': round(valor_parado, 2),
            'compras_sugeridas': len(compras_sugeridas)
        },
        'acoes': acoes,
        'aumentos_custo': aumentos,
        'produtos_baixa_margem': produtos_baixa_margem,
        'estoque_parado_ingredientes': estoque_parado_ingredientes,
        'compras_sugeridas': compras_sugeridas
    })


@dashboard_bp.route('/api/dashboard_executivo_integrado')
def api_dashboard_executivo_integrado():
    """API consolidada do dashboard executivo.

    Mantém o mesmo contrato JSON anteriormente servido pelo app.py.
    A inicialização do banco continua ocorrendo no startup do app, preservando
    compatibilidade sem criar dependência circular com criar_tabelas().
    """
    dashboard = gerar_dashboard_executivo(BANCO)
    return jsonify(dashboard)


@dashboard_bp.route('/api/dashboard_inteligente_erp')
def api_dashboard_inteligente_erp():
    """Alias compatível para o dashboard inteligente do ERP."""
    dashboard = gerar_dashboard_executivo(BANCO)
    return jsonify(dashboard)


__all__ = ['dashboard_bp']
