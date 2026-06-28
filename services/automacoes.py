# Motor de Automação do Wevvo ERP/PDV - Etapa 7
# Funções desacopladas para registrar eventos e manter rastreabilidade entre módulos.


def registrar_evento_automacao(cursor, origem, acao, entidade='', entidade_id=None, status='sucesso', mensagem='', detalhes=''):
    cursor.execute("""
        INSERT INTO automacoes_eventos (
            origem, acao, entidade, entidade_id, status, mensagem, detalhes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (origem, acao, entidade, entidade_id, status, mensagem, detalhes, __import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M:%S')))


def avaliar_nivel_estoque(estoque_atual, estoque_minimo):
    try:
        atual = float(estoque_atual or 0)
        minimo = float(estoque_minimo or 0)
    except (TypeError, ValueError):
        return 'ok'
    if minimo <= 0:
        return 'ok'
    if atual <= minimo:
        return 'critico'
    if atual <= minimo * 1.25:
        return 'atencao'
    return 'ok'
