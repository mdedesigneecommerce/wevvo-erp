"""Motor central incremental do Wevvo ERP/PDV.

Este módulo concentra sincronizações que antes ficavam espalhadas nas telas.
Ele foi criado para ser seguro: não apaga tabelas, não remove dados e pode ser
executado várias vezes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List


def agora_brasilia_iso() -> str:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")


def normalizar_decimal(valor: Any, padrao: float = 0.0) -> float:
    if valor is None:
        return float(padrao)
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if not texto:
        return float(padrao)
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except Exception:
        return float(padrao)


def tabela_existe(cursor: sqlite3.Cursor, tabela: str) -> bool:
    row = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?", (tabela,)
    ).fetchone()
    return row is not None


def coluna_existe_local(cursor: sqlite3.Cursor, tabela: str, coluna: str) -> bool:
    try:
        return any(row[1] == coluna for row in cursor.execute(f"PRAGMA table_info({tabela})"))
    except Exception:
        return False


def garantir_tabelas_motor(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_motor_execucoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            status TEXT NOT NULL,
            resumo TEXT,
            detalhes_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS erp_motor_alertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo TEXT NOT NULL,
            referencia TEXT,
            descricao TEXT NOT NULL,
            nivel TEXT NOT NULL DEFAULT 'ATENCAO',
            resolvido INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def calcular_cmv(custo_base: float, embalagem: float, fixo: float, variavel: float, margem: float, preco: float) -> Dict[str, float | str]:
    variavel = max(0.0, min(99.0, normalizar_decimal(variavel)))
    margem = max(0.0, min(99.0, normalizar_decimal(margem, 30)))
    custo_base = normalizar_decimal(custo_base)
    embalagem = normalizar_decimal(embalagem)
    fixo = normalizar_decimal(fixo)
    preco = normalizar_decimal(preco)

    cmv_real = round(custo_base + embalagem + fixo, 6)
    divisor_minimo = 1 - (variavel / 100.0)
    preco_minimo = round(cmv_real / divisor_minimo, 2) if cmv_real > 0 and divisor_minimo > 0 else 0.0
    divisor_sugerido = 1 - ((variavel + margem) / 100.0)
    preco_sugerido = round(cmv_real / divisor_sugerido, 2) if cmv_real > 0 and divisor_sugerido > 0 else 0.0
    taxas_valor = round(preco * variavel / 100.0, 6)
    lucro_real = round(preco - cmv_real - taxas_valor, 2)
    margem_real = round((lucro_real / preco * 100), 2) if preco > 0 else 0.0
    markup = round((preco / cmv_real), 4) if cmv_real > 0 else 0.0

    if preco <= 0:
        status = "Sem preço"
    elif lucro_real < 0:
        status = "Prejuízo"
    elif margem_real < 20:
        status = "Atenção"
    else:
        status = "Saudável"

    return {
        "custo_base": round(custo_base, 6),
        "embalagem_unitaria": round(embalagem, 6),
        "custo_fixo_unitario": round(fixo, 6),
        "custo_variavel_percentual": round(variavel, 6),
        "margem_desejada_percentual": round(margem, 6),
        "preco_praticado": round(preco, 6),
        "cmv_real": cmv_real,
        "preco_minimo": preco_minimo,
        "preco_sugerido": preco_sugerido,
        "lucro_real": lucro_real,
        "margem_real_percentual": margem_real,
        "markup": markup,
        "status": status,
    }


def custo_receita(cursor: sqlite3.Cursor, receita_id: int) -> float:
    if not tabela_existe(cursor, "receita_itens") or not tabela_existe(cursor, "ingredientes"):
        return 0.0
    rows = cursor.execute(
        """
        SELECT ri.gramas, i.preco_kg
        FROM receita_itens ri
        LEFT JOIN ingredientes i ON i.id = ri.ingrediente_id
        WHERE ri.receita_id = ?
        """,
        (receita_id,),
    ).fetchall()
    total = 0.0
    for row in rows:
        total += (normalizar_decimal(row[0]) / 1000.0) * normalizar_decimal(row[1])
    return round(total, 6)


def sincronizar_cmv_produtos(cursor: sqlite3.Cursor) -> int:
    if not (tabela_existe(cursor, "produtos_finais") and tabela_existe(cursor, "receitas") and tabela_existe(cursor, "cmv_precificacao")):
        return 0
    agora = agora_brasilia_iso()
    produtos = cursor.execute(
        """
        SELECT pf.id, pf.receita_id, pf.preco_venda,
               r.custo_total, r.custo_porcao, r.rendimento
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        """
    ).fetchall()
    atualizados = 0
    for p in produtos:
        produto_id, receita_id, preco_venda, custo_total, custo_porcao, rendimento = p
        rendimento = normalizar_decimal(rendimento, 1) or 1
        custo = normalizar_decimal(custo_porcao)
        if custo <= 0:
            custo = normalizar_decimal(custo_total) / rendimento if normalizar_decimal(custo_total) > 0 else 0
        if custo <= 0 and receita_id:
            custo = custo_receita(cursor, int(receita_id)) / rendimento

        cfg = cursor.execute(
            "SELECT * FROM cmv_precificacao WHERE entidade_tipo='produto' AND entidade_id=?",
            (produto_id,),
        ).fetchone()
        embalagem = normalizar_decimal(cfg[3] if cfg else 0)
        fixo = normalizar_decimal(cfg[4] if cfg else 0)
        variavel = normalizar_decimal(cfg[5] if cfg else 0)
        margem = normalizar_decimal(cfg[6] if cfg else 30)
        preco = normalizar_decimal(cfg[7] if cfg else preco_venda)
        if preco <= 0:
            preco = normalizar_decimal(preco_venda)
        calc = calcular_cmv(custo, embalagem, fixo, variavel, margem, preco)
        cursor.execute(
            """
            INSERT INTO cmv_precificacao (
                entidade_tipo, entidade_id, embalagem_unitaria, custo_fixo_unitario,
                custo_variavel_percentual, margem_desejada_percentual, preco_praticado,
                custo_base, cmv_real, preco_minimo, preco_sugerido, lucro_real,
                margem_real_percentual, markup, status, observacao, updated_at
            ) VALUES ('produto', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entidade_tipo, entidade_id) DO UPDATE SET
                custo_base=excluded.custo_base,
                cmv_real=excluded.cmv_real,
                preco_minimo=excluded.preco_minimo,
                preco_sugerido=excluded.preco_sugerido,
                lucro_real=excluded.lucro_real,
                margem_real_percentual=excluded.margem_real_percentual,
                markup=excluded.markup,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                produto_id,
                calc["embalagem_unitaria"],
                calc["custo_fixo_unitario"],
                calc["custo_variavel_percentual"],
                calc["margem_desejada_percentual"],
                calc["preco_praticado"],
                calc["custo_base"],
                calc["cmv_real"],
                calc["preco_minimo"],
                calc["preco_sugerido"],
                calc["lucro_real"],
                calc["margem_real_percentual"],
                calc["markup"],
                calc["status"],
                "Atualizado pelo motor central do ERP",
                agora,
            ),
        )
        atualizados += 1
    return atualizados



def sincronizar_cmv_receitas(cursor: sqlite3.Cursor) -> int:
    """Recalcula o CMV das receitas usando a mesma regra dos produtos.

    Preserva as configurações manuais de embalagem, fixo, taxas, margem e preço
    já salvas em cmv_precificacao. Quando não houver custo salvo na receita,
    calcula pela ficha técnica em receita_itens.
    """
    if not (tabela_existe(cursor, "receitas") and tabela_existe(cursor, "cmv_precificacao")):
        return 0
    agora = agora_brasilia_iso()
    receitas = cursor.execute(
        """
        SELECT id, nome, rendimento, custo_total, custo_porcao, preco_venda, preco_venda_porcao
        FROM receitas
        """
    ).fetchall()
    atualizadas = 0
    for r in receitas:
        receita_id = int(r["id"])
        rendimento = normalizar_decimal(r["rendimento"], 1) or 1
        custo = normalizar_decimal(r["custo_porcao"])
        if custo <= 0:
            custo_total = normalizar_decimal(r["custo_total"])
            if custo_total <= 0:
                custo_total = custo_receita(cursor, receita_id)
            custo = custo_total / rendimento if rendimento else custo_total

        cfg = cursor.execute(
            """
            SELECT embalagem_unitaria, custo_fixo_unitario, custo_variavel_percentual,
                   margem_desejada_percentual, preco_praticado
            FROM cmv_precificacao
            WHERE entidade_tipo='receita' AND entidade_id=?
            """,
            (receita_id,),
        ).fetchone()
        embalagem = normalizar_decimal(cfg["embalagem_unitaria"] if cfg else 0)
        fixo = normalizar_decimal(cfg["custo_fixo_unitario"] if cfg else 0)
        variavel = normalizar_decimal(cfg["custo_variavel_percentual"] if cfg else 0)
        margem = normalizar_decimal(cfg["margem_desejada_percentual"] if cfg else 30)
        preco = normalizar_decimal(cfg["preco_praticado"] if cfg else 0)
        if preco <= 0:
            preco = normalizar_decimal(r["preco_venda_porcao"]) or normalizar_decimal(r["preco_venda"])

        calc = calcular_cmv(custo, embalagem, fixo, variavel, margem, preco)
        cursor.execute(
            """
            INSERT INTO cmv_precificacao (
                entidade_tipo, entidade_id, embalagem_unitaria, custo_fixo_unitario,
                custo_variavel_percentual, margem_desejada_percentual, preco_praticado,
                custo_base, cmv_real, preco_minimo, preco_sugerido, lucro_real,
                margem_real_percentual, markup, status, observacao, updated_at
            ) VALUES ('receita', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entidade_tipo, entidade_id) DO UPDATE SET
                custo_base=excluded.custo_base,
                cmv_real=excluded.cmv_real,
                preco_minimo=excluded.preco_minimo,
                preco_sugerido=excluded.preco_sugerido,
                lucro_real=excluded.lucro_real,
                margem_real_percentual=excluded.margem_real_percentual,
                markup=excluded.markup,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                receita_id,
                calc["embalagem_unitaria"],
                calc["custo_fixo_unitario"],
                calc["custo_variavel_percentual"],
                calc["margem_desejada_percentual"],
                calc["preco_praticado"],
                calc["custo_base"],
                calc["cmv_real"],
                calc["preco_minimo"],
                calc["preco_sugerido"],
                calc["lucro_real"],
                calc["margem_real_percentual"],
                calc["markup"],
                calc["status"],
                "Receita atualizada pelo motor central do ERP",
                agora,
            ),
        )
        atualizadas += 1
    return atualizadas

def sincronizar_saldo_produtos(cursor: sqlite3.Cursor) -> int:
    if not (tabela_existe(cursor, "produtos_finais") and tabela_existe(cursor, "estoque_produto_acabado")):
        return 0
    rows = cursor.execute(
        """
        SELECT produto_final_id, SUM(COALESCE(saldo_atual, 0))
        FROM estoque_produto_acabado
        GROUP BY produto_final_id
        """
    ).fetchall()
    atualizados = 0
    for produto_id, saldo in rows:
        if produto_id is None:
            continue
        cursor.execute(
            "UPDATE produtos_finais SET estoque_atual=?, updated_at=? WHERE id=?",
            (normalizar_decimal(saldo), agora_brasilia_iso(), produto_id),
        )
        atualizados += cursor.rowcount or 0
    return atualizados


def registrar_alertas_estoque(cursor: sqlite3.Cursor) -> int:
    if not tabela_existe(cursor, "produtos_finais"):
        return 0
    agora = agora_brasilia_iso()
    criados = 0
    rows = cursor.execute(
        """
        SELECT id, nome, estoque_atual, estoque_minimo
        FROM produtos_finais
        WHERE COALESCE(estoque_minimo, 0) > 0
          AND COALESCE(estoque_atual, 0) <= COALESCE(estoque_minimo, 0)
        """
    ).fetchall()
    for produto_id, nome, atual, minimo in rows:
        desc = f"Produto acabado abaixo do mínimo: {nome} ({atual or 0} / mínimo {minimo or 0})"
        existe = cursor.execute(
            """
            SELECT id FROM erp_motor_alertas
            WHERE modulo='Estoque' AND referencia=? AND resolvido=0 AND descricao=?
            """,
            (f"produto:{produto_id}", desc),
        ).fetchone()
        if not existe:
            cursor.execute(
                "INSERT INTO erp_motor_alertas (modulo, referencia, descricao, nivel, created_at) VALUES (?, ?, ?, ?, ?)",
                ("Estoque", f"produto:{produto_id}", desc, "ATENCAO", agora),
            )
            criados += 1
    return criados




def sincronizar_compras_financeiro(cursor: sqlite3.Cursor) -> int:
    """Gera contas a pagar a partir dos pedidos de compra, de forma idempotente.

    Não marca a conta como paga. Apenas garante que cada pedido tenha um lançamento
    financeiro único em financeiro_lancamentos com origem='Compra' e origem_id igual
    ao id do pedido.
    """
    if not (tabela_existe(cursor, "compras_pedidos") and tabela_existe(cursor, "financeiro_lancamentos")):
        return 0

    agora = agora_brasilia_iso()
    pedidos = cursor.execute(
        """
        SELECT id, fornecedor, fornecedor_nome, ingrediente_nome, valor_total,
               previsao_entrega, created_at, status, financeiro_id
        FROM compras_pedidos
        WHERE COALESCE(valor_total, 0) > 0
        """
    ).fetchall()
    criados = 0
    for p in pedidos:
        pedido_id = int(p["id"])
        existente = cursor.execute(
            "SELECT id FROM financeiro_lancamentos WHERE origem='Compra' AND origem_id=?",
            (pedido_id,),
        ).fetchone()
        descricao = f"Compra - {p['ingrediente_nome'] or 'Pedido'}"
        fornecedor_nome = p["fornecedor_nome"] or p["fornecedor"] or ""
        if fornecedor_nome:
            descricao = f"{descricao} - {fornecedor_nome}"
        valor = normalizar_decimal(p["valor_total"])
        emissao = p["created_at"] or agora
        vencimento = p["previsao_entrega"] or emissao
        status_fin = "Pago" if str(p["status"] or "").strip().lower() in ("pago", "quitado") else "Pendente"

        if existente:
            financeiro_id = int(existente["id"])
            cursor.execute(
                """
                UPDATE financeiro_lancamentos
                SET descricao=?, categoria='Compras', valor=?, data_emissao=?,
                    data_vencimento=?, status=?, updated_at=?
                WHERE id=?
                """,
                (descricao, valor, emissao, vencimento, status_fin, agora, financeiro_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO financeiro_lancamentos (
                    tipo, descricao, categoria, origem, origem_id, valor,
                    data_emissao, data_vencimento, status, observacao, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "despesa",
                    descricao,
                    "Compras",
                    "Compra",
                    pedido_id,
                    valor,
                    emissao,
                    vencimento,
                    status_fin,
                    "Gerado automaticamente pelo motor central do ERP",
                    agora,
                    agora,
                ),
            )
            financeiro_id = int(cursor.lastrowid)
            criados += 1

        if coluna_existe_local(cursor, "compras_pedidos", "financeiro_id"):
            cursor.execute(
                "UPDATE compras_pedidos SET financeiro_id=?, updated_at=? WHERE id=?",
                (financeiro_id, agora, pedido_id),
            )
    return criados


def sincronizar_vendas_financeiro(cursor: sqlite3.Cursor) -> int:
    """Gera contas a receber a partir de vendas, sem duplicar lançamentos."""
    if not (tabela_existe(cursor, "vendas_erp") and tabela_existe(cursor, "financeiro_lancamentos")):
        return 0

    agora = agora_brasilia_iso()
    vendas = cursor.execute(
        """
        SELECT id, produto_final_nome, cliente_id, cliente_nome, valor_total, created_at, origem
        FROM vendas_erp
        WHERE COALESCE(valor_total, 0) > 0
        """
    ).fetchall()
    criados = 0
    for v in vendas:
        venda_id = int(v["id"])
        existente = cursor.execute(
            "SELECT id FROM financeiro_lancamentos WHERE origem='Venda' AND origem_id=?",
            (venda_id,),
        ).fetchone()
        descricao = f"Venda - {v['produto_final_nome'] or 'Produto'}"
        if v["cliente_nome"]:
            descricao = f"{descricao} - {v['cliente_nome']}"
        valor = normalizar_decimal(v["valor_total"])
        emissao = v["created_at"] or agora

        if existente:
            cursor.execute(
                """
                UPDATE financeiro_lancamentos
                SET descricao=?, categoria='Vendas', valor=?, data_emissao=?,
                    data_vencimento=?, cliente_id=?, cliente_nome=?, updated_at=?
                WHERE id=?
                """,
                (descricao, valor, emissao, emissao, v["cliente_id"], v["cliente_nome"], agora, int(existente["id"])),
            )
        else:
            cursor.execute(
                """
                INSERT INTO financeiro_lancamentos (
                    tipo, descricao, categoria, origem, origem_id, canal, valor,
                    data_emissao, data_vencimento, status, observacao, created_at,
                    updated_at, cliente_id, cliente_nome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "receita",
                    descricao,
                    "Vendas",
                    "Venda",
                    venda_id,
                    v["origem"] or "ERP",
                    valor,
                    emissao,
                    emissao,
                    "Pendente",
                    "Gerado automaticamente pelo motor central do ERP",
                    agora,
                    agora,
                    v["cliente_id"],
                    v["cliente_nome"],
                ),
            )
            criados += 1
    return criados


def sincronizar_estoque_ingredientes(cursor: sqlite3.Cursor) -> int:
    """Recalcula saldo dos ingredientes com base nas movimentações de estoque.

    Entrada soma, saída/consumo/perda subtrai. Mantém preço e dados nutricionais.
    """
    if not (tabela_existe(cursor, "ingredientes") and tabela_existe(cursor, "movimentacoes_estoque")):
        return 0
    agora = agora_brasilia_iso()
    rows = cursor.execute(
        """
        SELECT ingrediente_id,
               SUM(CASE
                   WHEN LOWER(COALESCE(tipo,'')) IN ('entrada','compra','ajuste entrada','ajuste_entrada') THEN COALESCE(quantidade,0)
                   WHEN LOWER(COALESCE(tipo,'')) IN ('saida','saída','consumo','producao','produção','perda','ajuste saida','ajuste_saída','ajuste_saida') THEN -COALESCE(quantidade,0)
                   ELSE 0
               END) AS saldo
        FROM movimentacoes_estoque
        GROUP BY ingrediente_id
        """
    ).fetchall()
    atualizados = 0
    for row in rows:
        if row["ingrediente_id"] is None:
            continue
        cursor.execute(
            "UPDATE ingredientes SET estoque_atual=?, updated_at=? WHERE id=?",
            (normalizar_decimal(row["saldo"]), agora, int(row["ingrediente_id"])),
        )
        atualizados += cursor.rowcount or 0
    return atualizados



def sincronizar_producao_lotes_qualidade(cursor: sqlite3.Cursor) -> Dict[str, int]:
    """Integra produções já registradas com produto acabado, lote e qualidade.

    A rotina é idempotente: se o lote/qualidade já existir para a produção, não duplica.
    Ela complementa produções antigas que foram salvas antes do motor central.
    """
    resultado = {"lotes_criados": 0, "lotes_atualizados": 0, "qualidade_criada": 0, "alertas": 0}
    if not tabela_existe(cursor, "producoes"):
        return resultado

    agora = agora_brasilia_iso()

    producoes = cursor.execute(
        """
        SELECT id, receita_id, receita_nome, quantidade, custo_total, custo_unitario,
               produto_final_id, produto_final_nome, lote, data_fabricacao, data_validade,
               observacao, created_at
        FROM producoes
        ORDER BY id
        """
    ).fetchall()

    for prod in producoes:
        producao_id = int(prod["id"])
        produto_id = prod["produto_final_id"]
        produto_nome = prod["produto_final_nome"]
        lote = (prod["lote"] or f"PROD-{producao_id:06d}").strip()
        quantidade = normalizar_decimal(prod["quantidade"])

        if produto_id and tabela_existe(cursor, "estoque_produto_acabado"):
            estoque = cursor.execute(
                "SELECT id, quantidade_produzida, saldo_atual FROM estoque_produto_acabado WHERE producao_id=? LIMIT 1",
                (producao_id,),
            ).fetchone()
            if estoque:
                # Corrige dados cadastrais do lote sem sobrescrever baixas já realizadas.
                saldo_atual = normalizar_decimal(estoque["saldo_atual"])
                qtd_antiga = normalizar_decimal(estoque["quantidade_produzida"])
                novo_saldo = saldo_atual
                if qtd_antiga <= 0 and quantidade > 0:
                    novo_saldo = quantidade
                cursor.execute(
                    """
                    UPDATE estoque_produto_acabado
                    SET produto_final_id=?, produto_final_nome=?, receita_id=?, receita_nome=?, lote=?,
                        quantidade_produzida=?, saldo_atual=?, data_fabricacao=?, data_validade=?,
                        observacao=COALESCE(observacao, ?)
                    WHERE id=?
                    """,
                    (
                        int(produto_id), produto_nome or "Produto acabado", prod["receita_id"], prod["receita_nome"], lote,
                        quantidade, novo_saldo, prod["data_fabricacao"], prod["data_validade"],
                        prod["observacao"] or f"Lote vinculado automaticamente à produção #{producao_id}",
                        int(estoque["id"]),
                    ),
                )
                resultado["lotes_atualizados"] += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO estoque_produto_acabado (
                        produto_final_id, produto_final_nome, receita_id, receita_nome, producao_id, lote,
                        quantidade_produzida, saldo_atual, data_fabricacao, data_validade, observacao, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(produto_id), produto_nome or "Produto acabado", prod["receita_id"], prod["receita_nome"],
                        producao_id, lote, quantidade, quantidade, prod["data_fabricacao"], prod["data_validade"],
                        prod["observacao"] or f"Lote criado automaticamente pelo motor central para produção #{producao_id}",
                        prod["created_at"] or agora,
                    ),
                )
                resultado["lotes_criados"] += 1
        elif quantidade > 0:
            if tabela_existe(cursor, "erp_motor_alertas"):
                existe = cursor.execute(
                    "SELECT id FROM erp_motor_alertas WHERE modulo='Produção' AND referencia=? AND resolvido=0 LIMIT 1",
                    (f"producao:{producao_id}",),
                ).fetchone()
                if not existe:
                    cursor.execute(
                        "INSERT INTO erp_motor_alertas (modulo, referencia, descricao, nivel, created_at) VALUES (?, ?, ?, ?, ?)",
                        (
                            "Produção",
                            f"producao:{producao_id}",
                            f"Produção #{producao_id} sem produto acabado vinculado; não foi possível gerar estoque de produto acabado.",
                            "ATENCAO",
                            agora,
                        ),
                    )
                    resultado["alertas"] += 1

        # Qualidade automática para produções antigas sem checklist/auditoria.
        if tabela_existe(cursor, "qualidade_checklists") and tabela_existe(cursor, "qualidade_temperaturas"):
            existe_qualidade = cursor.execute(
                "SELECT id FROM qualidade_checklists WHERE lote=? AND area='Produção' LIMIT 1",
                (lote,),
            ).fetchone()
            if not existe_qualidade:
                data_registro = (prod["data_fabricacao"] or str(prod["created_at"] or agora)[:10])
                itens = ("Touca", "Luva", "Uniforme", "Bancada limpa")
                for item in itens:
                    cursor.execute(
                        """
                        INSERT INTO qualidade_checklists (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            data_registro, "Produção", "Sistema", item, "Conforme",
                            f"Checklist padrão criado pelo motor central para produção #{producao_id}.", lote, "", agora,
                        ),
                    )
                cursor.execute(
                    """
                    INSERT INTO qualidade_temperaturas (data_registro, area, equipamento, temperatura, limite_min, limite_max, status, responsavel, observacao, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (data_registro, "Fabricação", "Processo térmico", 60.0, 60.0, None, "Conforme", "Sistema", f"Processo padrão 60 °C vinculado à produção #{producao_id}.", agora),
                )
                cursor.execute(
                    """
                    INSERT INTO qualidade_temperaturas (data_registro, area, equipamento, temperatura, limite_min, limite_max, status, responsavel, observacao, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (data_registro, "Ultracongelamento", "Túnel / ultracongelador", -18.0, None, -18.0, "Conforme", "Sistema", f"Processo padrão -18 °C em até 1 hora vinculado à produção #{producao_id}.", agora),
                )
                if tabela_existe(cursor, "qualidade_auditoria"):
                    cursor.execute(
                        """
                        INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            "Qualidade automática",
                            f"Produção #{producao_id} - {prod['receita_nome']}: registros padrão criados pelo motor central.",
                            lote,
                            "Conforme",
                            agora,
                        ),
                    )
                resultado["qualidade_criada"] += 1

    return resultado

def executar_motor_central(caminho_banco: str) -> Dict[str, Any]:
    conn = sqlite3.connect(caminho_banco)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    garantir_tabelas_motor(cursor)
    detalhes: Dict[str, Any] = {}
    try:
        detalhes["ingredientes_com_saldo_atualizado"] = sincronizar_estoque_ingredientes(cursor)
        detalhes["producao_lotes_qualidade"] = sincronizar_producao_lotes_qualidade(cursor)
        detalhes["produtos_com_saldo_atualizado"] = sincronizar_saldo_produtos(cursor)
        detalhes["contas_a_pagar_geradas"] = sincronizar_compras_financeiro(cursor)
        detalhes["contas_a_receber_geradas"] = sincronizar_vendas_financeiro(cursor)
        detalhes["receitas_cmv_recalculado"] = sincronizar_cmv_receitas(cursor)
        detalhes["produtos_cmv_recalculado"] = sincronizar_cmv_produtos(cursor)
        detalhes["alertas_estoque_criados"] = registrar_alertas_estoque(cursor)
        status = "OK"
        resumo = "Motor central executado com sucesso."
        conn.commit()
    except Exception as exc:
        conn.rollback()
        status = "ERRO"
        resumo = str(exc)
        detalhes["erro"] = str(exc)
    finally:
        import json

        cursor.execute(
            "INSERT INTO erp_motor_execucoes (tipo, status, resumo, detalhes_json, created_at) VALUES (?, ?, ?, ?, ?)",
            ("sincronizacao", status, resumo, json.dumps(detalhes, ensure_ascii=False), agora_brasilia_iso()),
        )
        conn.commit()
        conn.close()
    return {"status": status, "resumo": resumo, "detalhes": detalhes}
