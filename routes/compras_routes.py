"""Rotas do módulo Compras do Wevvo ERP/PDV.

Sprint R02: migração real das rotas de compras para Blueprint.
As regras auxiliares ainda são injetadas a partir do app.py para preservar
compatibilidade durante a refatoração gradual.
"""

from flask import Blueprint, jsonify, request
from services.core import agora_brasilia, normalizar_float


# Mantido para compatibilidade com imports antigos em routes.__init__.
compras_bp = Blueprint("compras_placeholder", __name__)


def create_compras_blueprint(deps):
    """Cria o Blueprint de Compras com dependências injetadas pelo app.py."""
    compras_bp = Blueprint("compras", __name__)

    criar_tabelas = deps["criar_tabelas"]
    conectar_banco = deps["conectar_banco"]
    erp_garantir_conta_pagar_compra = deps["erp_garantir_conta_pagar_compra"]
    erp_sincronizar_compras_estoque_financeiro = deps["erp_sincronizar_compras_estoque_financeiro"]
    recalcular_receitas_salvas = deps["recalcular_receitas_salvas"]
    resumo_financeiro = deps["resumo_financeiro"]
    _erp_garantir_tabelas_base = deps["_erp_garantir_tabelas_base"]
    _erp_recalcular_cmv_todos = deps["_erp_recalcular_cmv_todos"]
    _erp_registrar_evento = deps["_erp_registrar_evento"]
    _erp_auditoria_geral = deps["_erp_auditoria_geral"]

    @compras_bp.route('/compras_status')
    def compras_status():
        """Etapa 9: compras profissionais com sugestão por estoque mínimo, cotações, pedido e entrada no estoque."""
        criar_tabelas()
        conn = conectar_banco()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, nome, unidade, estoque_atual, estoque_minimo, preco_compra, quantidade_compra, unidade_compra, preco_kg
            FROM ingredientes
            ORDER BY nome COLLATE NOCASE
        """)
        ingredientes = [dict(row) for row in cursor.fetchall()]
        sugestoes = []
        for item in ingredientes:
            atual = float(item.get('estoque_atual') or 0)
            minimo = float(item.get('estoque_minimo') or 0)
            if minimo > 0 and atual <= minimo:
                qtd_padrao = float(item.get('quantidade_compra') or 0)
                qtd_sugerida = max(minimo * 2 - atual, qtd_padrao, minimo - atual)
                sugestoes.append({
                    'ingrediente_id': item['id'],
                    'ingrediente_nome': item['nome'],
                    'unidade': item.get('unidade') or item.get('unidade_compra') or 'KG',
                    'estoque_atual': atual,
                    'estoque_minimo': minimo,
                    'quantidade_sugerida': round(qtd_sugerida, 3),
                    'preco_estimado': round(float(item.get('preco_compra') or item.get('preco_kg') or 0), 2),
                    'prioridade': 'Alta' if atual <= 0 else 'Normal'
                })

        cursor.execute("SELECT * FROM compras_solicitacoes ORDER BY id DESC LIMIT 80")
        solicitacoes = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM compras_cotacoes ORDER BY id DESC LIMIT 80")
        cotacoes = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM compras_pedidos ORDER BY id DESC LIMIT 80")
        pedidos = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT id, nome, documento, whatsapp, prazo_padrao_dias, avaliacao, status FROM fornecedores WHERE status='Ativo' ORDER BY nome COLLATE NOCASE")
        fornecedores = [dict(row) for row in cursor.fetchall()]

        cursor.execute("SELECT status, COUNT(*) total, COALESCE(SUM(quantidade_solicitada),0) quantidade FROM compras_solicitacoes GROUP BY status")
        status_solicitacoes = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT status, COUNT(*) total, COALESCE(SUM(valor_total),0) valor FROM compras_pedidos GROUP BY status")
        status_pedidos = [dict(row) for row in cursor.fetchall()]
        resumo = {
            'sugestoes': len(sugestoes),
            'solicitacoes_abertas': sum(1 for x in solicitacoes if x.get('status') in ('Aberta','Cotando')),
            'pedidos_abertos': sum(1 for x in pedidos if x.get('status') in ('Aberto','Enviado')),
            'valor_pedidos_abertos': round(sum(float(x.get('valor_total') or 0) for x in pedidos if x.get('status') in ('Aberto','Enviado')), 2)
        }
        conn.close()
        return jsonify({'status':'sucesso','resumo':resumo,'ingredientes':ingredientes,'fornecedores':fornecedores,'sugestoes':sugestoes,'solicitacoes':solicitacoes,'cotacoes':cotacoes,'pedidos':pedidos,'status_solicitacoes':status_solicitacoes,'status_pedidos':status_pedidos})

    @compras_bp.route('/compras_criar_solicitacao', methods=['POST'])
    def compras_criar_solicitacao():
        criar_tabelas()
        dados = request.get_json(silent=True) or {}
        ingrediente_id = int(dados.get('ingrediente_id') or 0)
        quantidade = normalizar_float(dados.get('quantidade'), 0)
        observacao = str(dados.get('observacao') or '').strip()
        prioridade = str(dados.get('prioridade') or 'Normal').strip() or 'Normal'
        if not ingrediente_id or quantidade <= 0:
            return jsonify({'status':'erro','mensagem':'Informe ingrediente e quantidade.'}), 400
        conn = conectar_banco(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM ingredientes WHERE id=?", (ingrediente_id,))
        ing = cursor.fetchone()
        if not ing:
            conn.close(); return jsonify({'status':'erro','mensagem':'Ingrediente não encontrado.'}), 404
        agora = agora_brasilia()
        atual = float(ing['estoque_atual'] or 0) if 'estoque_atual' in ing.keys() else 0
        minimo = float(ing['estoque_minimo'] or 0) if 'estoque_minimo' in ing.keys() else 0
        cursor.execute("""
            INSERT INTO compras_solicitacoes (ingrediente_id, ingrediente_nome, unidade, estoque_atual, estoque_minimo,
                quantidade_sugerida, quantidade_solicitada, prioridade, status, observacao, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Aberta', ?, ?, ?)
        """, (ingrediente_id, ing['nome'], ing['unidade'] if 'unidade' in ing.keys() else 'KG', atual, minimo, quantidade, quantidade, prioridade, observacao, agora, agora))
        sid = cursor.lastrowid
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':'Solicitação de compra criada.','id':sid})

    @compras_bp.route('/compras_registrar_cotacao', methods=['POST'])
    def compras_registrar_cotacao():
        criar_tabelas()
        dados = request.get_json(silent=True) or {}
        solicitacao_id = int(dados.get('solicitacao_id') or 0)
        fornecedor_id = int(dados.get('fornecedor_id') or 0)
        fornecedor = str(dados.get('fornecedor') or '').strip()
        preco_total = normalizar_float(dados.get('preco_total'), 0)
        prazo_dias = int(normalizar_float(dados.get('prazo_dias'), 0))
        observacao = str(dados.get('observacao') or '').strip()
        conn = conectar_banco(); cursor = conn.cursor()
        fornecedor_nome = fornecedor
        if fornecedor_id:
            cursor.execute("SELECT nome FROM fornecedores WHERE id=?", (fornecedor_id,))
            forn = cursor.fetchone()
            if forn:
                fornecedor_nome = forn['nome']
        if not solicitacao_id or not fornecedor_nome or preco_total <= 0:
            conn.close(); return jsonify({'status':'erro','mensagem':'Solicitação, fornecedor e preço são obrigatórios.'}), 400
        cursor.execute("SELECT id FROM compras_solicitacoes WHERE id=?", (solicitacao_id,))
        if not cursor.fetchone():
            conn.close(); return jsonify({'status':'erro','mensagem':'Solicitação não encontrada.'}), 404
        agora = agora_brasilia()
        cursor.execute("INSERT INTO compras_cotacoes (solicitacao_id, fornecedor, fornecedor_id, fornecedor_nome, preco_total, prazo_dias, observacao, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (solicitacao_id, fornecedor_nome, fornecedor_id or None, fornecedor_nome, preco_total, prazo_dias, observacao, agora))
        cursor.execute("SELECT ingrediente_id, ingrediente_nome, unidade, quantidade_solicitada FROM compras_solicitacoes WHERE id=?", (solicitacao_id,))
        sol_cot = cursor.fetchone()
        if fornecedor_id and sol_cot:
            qtd_ref = float(sol_cot['quantidade_solicitada'] or 0)
            preco_unit = round(preco_total / qtd_ref, 6) if qtd_ref > 0 else preco_total
            cursor.execute("""
                INSERT INTO fornecedores_ingredientes (fornecedor_id, ingrediente_id, ingrediente_nome, unidade, preco_unitario, prazo_dias, observacao, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (fornecedor_id, sol_cot['ingrediente_id'], sol_cot['ingrediente_nome'], sol_cot['unidade'], preco_unit, prazo_dias, 'Preço registrado a partir de cotação de compra.', agora, agora))
        cursor.execute("UPDATE compras_solicitacoes SET status='Cotando', updated_at=? WHERE id=?", (agora, solicitacao_id))
        cid = cursor.lastrowid
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':'Cotação registrada.','id':cid})

    @compras_bp.route('/compras_gerar_pedido', methods=['POST'])
    def compras_gerar_pedido():
        criar_tabelas()
        dados = request.get_json(silent=True) or {}
        solicitacao_id = int(dados.get('solicitacao_id') or 0)
        cotacao_id = int(dados.get('cotacao_id') or 0)
        fornecedor_id = int(dados.get('fornecedor_id') or 0)
        fornecedor_manual = str(dados.get('fornecedor') or '').strip()
        valor_manual = normalizar_float(dados.get('valor_total'), 0)
        previsao = str(dados.get('previsao_entrega') or '').strip()
        if not solicitacao_id:
            return jsonify({'status':'erro','mensagem':'Informe a solicitação.'}), 400
        conn = conectar_banco(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM compras_solicitacoes WHERE id=?", (solicitacao_id,))
        sol = cursor.fetchone()
        if not sol:
            conn.close(); return jsonify({'status':'erro','mensagem':'Solicitação não encontrada.'}), 404
        cot = None
        if cotacao_id:
            cursor.execute("SELECT * FROM compras_cotacoes WHERE id=?", (cotacao_id,))
            cot = cursor.fetchone()
        fornecedor = fornecedor_manual or (cot['fornecedor_nome'] if cot and 'fornecedor_nome' in cot.keys() and cot['fornecedor_nome'] else (cot['fornecedor'] if cot else 'Fornecedor a definir'))
        if not fornecedor_manual and fornecedor_id:
            cursor.execute("SELECT nome FROM fornecedores WHERE id=?", (fornecedor_id,))
            forn = cursor.fetchone()
            if forn:
                fornecedor = forn['nome']
        fornecedor_id_final = fornecedor_id or (cot['fornecedor_id'] if cot and 'fornecedor_id' in cot.keys() and cot['fornecedor_id'] else None)
        valor_total = valor_manual or (float(cot['preco_total'] or 0) if cot else 0)
        agora = agora_brasilia()
        cursor.execute("""
            INSERT INTO compras_pedidos (solicitacao_id, cotacao_id, fornecedor, fornecedor_id, fornecedor_nome, ingrediente_id, ingrediente_nome, quantidade,
                unidade, valor_total, status, previsao_entrega, observacao, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aberto', ?, ?, ?, ?)
        """, (solicitacao_id, cotacao_id or None, fornecedor, fornecedor_id_final, fornecedor, sol['ingrediente_id'], sol['ingrediente_nome'], sol['quantidade_solicitada'], sol['unidade'], valor_total, previsao, 'Pedido gerado pelo módulo Compras', agora, agora))
        pid = cursor.lastrowid
        cursor.execute("UPDATE compras_solicitacoes SET status='Pedido gerado', updated_at=? WHERE id=?", (agora, solicitacao_id))
        if cotacao_id:
            cursor.execute("UPDATE compras_cotacoes SET selecionada=CASE WHEN id=? THEN 1 ELSE 0 END WHERE solicitacao_id=?", (cotacao_id, solicitacao_id))
        # Conta a pagar nasce automaticamente junto com o pedido de compra.
        # Não marca como pago ao receber a mercadoria; baixa financeira é uma etapa separada.
        financeiro_id = erp_garantir_conta_pagar_compra(cursor, pid)
        if financeiro_id:
            cursor.execute("UPDATE compras_pedidos SET financeiro_id=?, updated_at=? WHERE id=?", (financeiro_id, agora, pid))
        _erp_registrar_evento(cursor, 'Compras', 'pedido_gerado', 'compras_pedidos', pid, 'sucesso', 'Pedido criado e conta a pagar vinculada automaticamente.')
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':'Pedido de compra gerado e lançado no financeiro.','id':pid})

    @compras_bp.route('/compras_receber_pedido', methods=['POST'])
    def compras_receber_pedido():
        criar_tabelas()
        dados = request.get_json(silent=True) or {}
        pedido_id = int(dados.get('pedido_id') or 0)
        if not pedido_id:
            return jsonify({'status':'erro','mensagem':'Informe o pedido.'}), 400
        conn = conectar_banco(); cursor = conn.cursor()
        cursor.execute("SELECT * FROM compras_pedidos WHERE id=?", (pedido_id,))
        ped = cursor.fetchone()
        if not ped:
            conn.close(); return jsonify({'status':'erro','mensagem':'Pedido não encontrado.'}), 404
        if ped['status'] == 'Recebido':
            conn.close(); return jsonify({'status':'erro','mensagem':'Pedido já foi recebido.'}), 400
        agora = agora_brasilia()
        quantidade = float(ped['quantidade'] or 0); valor_total = float(ped['valor_total'] or 0)
        ingrediente_id = ped['ingrediente_id']
        cursor.execute("SELECT * FROM ingredientes WHERE id=?", (ingrediente_id,))
        ing = cursor.fetchone()
        if not ing:
            conn.close(); return jsonify({'status':'erro','mensagem':'Ingrediente do pedido não encontrado.'}), 404
        novo_estoque = float(ing['estoque_atual'] or 0) + quantidade
        preco_unitario = round(valor_total / quantidade, 6) if valor_total > 0 and quantidade > 0 else float(ing['preco_kg'] or 0)
        cursor.execute("""
            UPDATE ingredientes
            SET estoque_atual=?, preco_compra=CASE WHEN ? > 0 THEN ? ELSE preco_compra END,
                quantidade_compra=CASE WHEN ? > 0 THEN ? ELSE quantidade_compra END,
                unidade_compra=?, preco_kg=CASE WHEN ? > 0 THEN ? ELSE preco_kg END,
                updated_at=?
            WHERE id=?
        """, (novo_estoque, valor_total, valor_total, quantidade, quantidade, ped['unidade'], preco_unitario, preco_unitario, agora, ingrediente_id))
        cursor.execute("INSERT INTO movimentacoes_estoque (ingrediente_id, ingrediente_nome, tipo, quantidade, unidade, observacao, created_at) VALUES (?, ?, 'ENTRADA', ?, ?, ?, ?)", (ingrediente_id, ped['ingrediente_nome'], quantidade, ped['unidade'], f"Entrada automática do pedido de compra #{pedido_id}", agora))
        cursor.execute("""
            UPDATE compras_pedidos
            SET status='Recebido', data_recebimento=?, custo_unitario_recebido=?, estoque_integrado=1, cmv_recalculado=1, updated_at=?
            WHERE id=?
        """, (agora[:10], preco_unitario, agora, pedido_id))
        cursor.execute("UPDATE compras_solicitacoes SET status='Concluída', updated_at=? WHERE id=?", (agora, ped['solicitacao_id']))
        financeiro_id = erp_garantir_conta_pagar_compra(cursor, pedido_id)
        # Mantém como Pendente: receber mercadoria não significa pagar fornecedor.
        if financeiro_id:
            cursor.execute("""
                UPDATE financeiro_lancamentos
                SET status=CASE WHEN status IN ('Pago','Recebido','Cancelado') THEN status ELSE 'Pendente' END,
                    observacao=COALESCE(NULLIF(observacao,''),'Conta gerada automaticamente pelo pedido de compra') || ' | Mercadoria recebida em ' || ?,
                    updated_at=?
                WHERE id=?
            """, (agora[:10], agora, financeiro_id))
            cursor.execute("UPDATE compras_pedidos SET financeiro_id=? WHERE id=?", (financeiro_id, pedido_id))
        try:
            recalcular_receitas_salvas(cursor, int(ingrediente_id))
            _erp_recalcular_cmv_todos(cursor)
        except Exception as exc:
            _erp_registrar_evento(cursor, 'CMV', 'recalculo_pos_compra', 'ingredientes', ingrediente_id, 'erro', str(exc))
        _erp_registrar_evento(cursor, 'Compras', 'pedido_recebido', 'compras_pedidos', pedido_id, 'sucesso', 'Estoque, custo do ingrediente, CMV e conta a pagar sincronizados.')
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':'Pedido recebido: estoque, custo, CMV e conta a pagar sincronizados.'})

    @compras_bp.route('/compras_sincronizar_erp', methods=['POST'])
    def compras_sincronizar_erp():
        criar_tabelas()
        conn = conectar_banco(); cursor = conn.cursor()
        _erp_garantir_tabelas_base(cursor)
        resultado = erp_sincronizar_compras_estoque_financeiro(cursor)
        financeiro = resumo_financeiro(cursor)
        auditoria = _erp_auditoria_geral(cursor)
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':'Compras, contas a pagar, CMV e auditoria sincronizados.', 'resultado': resultado, 'financeiro': financeiro, 'auditoria': auditoria})

    return compras_bp


__all__ = ["compras_bp", "create_compras_blueprint"]
