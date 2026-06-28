from flask import Flask, render_template, request, jsonify, render_template_string, Response
import csv
import json
import re
import sqlite3
import os
from io import StringIO
import pandas as pd
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from config import (
    BANCO,
    ARQUIVO_TACO,
    ARQUIVO_INGREDIENTES_PRECIFICACAO,
    ARQUIVO_RECEITAS_CATEGORIAS,
    UNIDADES_VALIDAS,
)
from services.core import (
    agora_brasilia,
    data_brasilia_obj,
    data_brasil,
    normalizar_busca,
    normalizar_float,
    coluna_existe,
)
from services.automacoes import registrar_evento_automacao, avaliar_nivel_estoque
from services.erp_motor import executar_motor_central
from services.dashboard_service import gerar_dashboard_executivo
from services.financeiro_service import gerar_financeiro_integrado

app = Flask(__name__)


def hoje_brasilia():
    """Retorna a data atual no fuso do Brasil em formato ISO para filtros do banco."""
    try:
        return data_brasilia_obj().date().isoformat()
    except Exception:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()


def normalizar_decimal_cmv(valor, padrao=0.0):
    """Converte valores monetários/percentuais do CMV sem transformar 3.00 em 300.
    Aceita 3,00, 3.00, 1.234,56 e 1234.56.
    """
    if valor is None:
        return float(padrao)
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace('R$', '').replace('%', '').replace(' ', '')
    if not texto:
        return float(padrao)
    if ',' in texto and '.' in texto:
        # Formato brasileiro: 1.234,56
        texto = texto.replace('.', '').replace(',', '.')
    elif ',' in texto:
        # Formato brasileiro simples: 3,00
        texto = texto.replace(',', '.')
    # Se tiver apenas ponto, mantém como decimal: 3.00 = 3
    try:
        return float(texto)
    except Exception:
        return float(padrao)


def parse_float_br(valor, padrao=0.0):
    """Compatibilidade para campos monetários brasileiros.
    Mantém ponto decimal como decimal e converte vírgula brasileira corretamente.
    """
    return normalizar_decimal_cmv(valor, padrao)


def calcular_precificacao_cmv(custo_base, embalagem=0, fixo=0, variavel=0, margem=30, preco=0):
    """Cálculo único e oficial do CMV/Precificação.

    Usado pela tela, pelo salvamento e pelos relatórios para evitar divergência entre módulos.
    Fórmulas:
    - CMV real = custo base + embalagem + custo fixo unitário
    - Preço mínimo = CMV real / (1 - taxas variáveis %)
    - Preço sugerido = CMV real / (1 - taxas variáveis % - margem desejada %)
    - Lucro real = preço praticado - CMV real - taxas variáveis sobre venda
    - Margem real = lucro real / preço praticado
    - Markup = preço praticado / CMV real
    """
    custo_base = normalizar_decimal_cmv(custo_base, 0)
    embalagem = normalizar_decimal_cmv(embalagem, 0)
    fixo = normalizar_decimal_cmv(fixo, 0)
    variavel = normalizar_decimal_cmv(variavel, 0)
    margem = normalizar_decimal_cmv(margem, 30)
    preco = normalizar_decimal_cmv(preco, 0)

    # Proteções para evitar cálculos impossíveis e valores absurdos
    variavel = max(0.0, min(99.0, variavel))
    margem = max(0.0, min(99.0, margem))

    cmv_real = round(custo_base + embalagem + fixo, 6)
    divisor_minimo = 1 - (variavel / 100.0)
    preco_minimo = round(cmv_real / divisor_minimo, 2) if cmv_real > 0 and divisor_minimo > 0 else 0

    divisor_sugerido = 1 - ((variavel + margem) / 100.0)
    preco_sugerido = round(cmv_real / divisor_sugerido, 2) if cmv_real > 0 and divisor_sugerido > 0 else 0

    taxas_valor = round(preco * variavel / 100.0, 6)
    lucro_real = round(preco - cmv_real - taxas_valor, 2)
    margem_real = round((lucro_real / preco * 100), 2) if preco > 0 else 0
    markup = round((preco / cmv_real), 4) if cmv_real > 0 else 0

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


def obter_custo_base_entidade(cursor, entidade_tipo, entidade_id):
    entidade_tipo = str(entidade_tipo or '').strip().lower()
    entidade_id = int(entidade_id)
    if entidade_tipo == 'produto':
        cursor.execute("""
            SELECT pf.preco_venda, r.custo_porcao, r.custo_total, r.rendimento
            FROM produtos_finais pf
            LEFT JOIN receitas r ON r.id = pf.receita_id
            WHERE pf.id = ?
        """, (entidade_id,))
    else:
        cursor.execute("""
            SELECT preco_venda, custo_porcao, custo_total, rendimento
            FROM receitas
            WHERE id = ?
        """, (entidade_id,))
    row = cursor.fetchone()
    if not row:
        return None
    rendimento = normalizar_decimal_cmv(row['rendimento'] if 'rendimento' in row.keys() else 1, 1) or 1
    custo = normalizar_decimal_cmv(row['custo_porcao'] if 'custo_porcao' in row.keys() else 0, 0)
    if custo <= 0:
        custo = normalizar_decimal_cmv(row['custo_total'] if 'custo_total' in row.keys() else 0, 0) / rendimento
    preco_atual = normalizar_decimal_cmv(row['preco_venda'] if 'preco_venda' in row.keys() else 0, 0)
    return {"custo_base": round(custo, 6), "preco_atual": preco_atual, "rendimento": rendimento}


def salvar_calculo_cmv(cursor, entidade_tipo, entidade_id, calculo, observacao=None):
    agora = agora_brasilia()
    cursor.execute("""
        INSERT INTO cmv_precificacao (
            entidade_tipo, entidade_id, embalagem_unitaria, custo_fixo_unitario,
            custo_variavel_percentual, margem_desejada_percentual, preco_praticado,
            custo_base, cmv_real, preco_minimo, preco_sugerido, lucro_real,
            margem_real_percentual, markup, status, observacao, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entidade_tipo, entidade_id) DO UPDATE SET
            embalagem_unitaria = excluded.embalagem_unitaria,
            custo_fixo_unitario = excluded.custo_fixo_unitario,
            custo_variavel_percentual = excluded.custo_variavel_percentual,
            margem_desejada_percentual = excluded.margem_desejada_percentual,
            preco_praticado = excluded.preco_praticado,
            custo_base = excluded.custo_base,
            cmv_real = excluded.cmv_real,
            preco_minimo = excluded.preco_minimo,
            preco_sugerido = excluded.preco_sugerido,
            lucro_real = excluded.lucro_real,
            margem_real_percentual = excluded.margem_real_percentual,
            markup = excluded.markup,
            status = excluded.status,
            observacao = COALESCE(excluded.observacao, cmv_precificacao.observacao),
            updated_at = excluded.updated_at
    """, (
        entidade_tipo, int(entidade_id), calculo['embalagem_unitaria'], calculo['custo_fixo_unitario'],
        calculo['custo_variavel_percentual'], calculo['margem_desejada_percentual'], calculo['preco_praticado'],
        calculo['custo_base'], calculo['cmv_real'], calculo['preco_minimo'], calculo['preco_sugerido'],
        calculo['lucro_real'], calculo['margem_real_percentual'], calculo['markup'], calculo['status'], observacao, agora
    ))
    return agora


PALAVRAS_IGNORADAS_NUTRI = {
    "a", "ao", "aos", "as", "com", "de", "da", "das", "do", "dos", "e", "em",
    "kg", "g", "ml", "l", "un", "und", "unid", "unidade", "unidades", "pct",
    "pacote", "barra", "acompanha", "acompanhado", "acompanhada", "recheado",
    "recheada", "molho", "creme", "puro", "pura", "integral", "fit", "cru",
    "crua", "cozido", "cozida", "quero", "cx", "n", "tipo"
}

PALAVRAS_NAO_ALIMENTO = {
    "adesivo", "adesivos", "bandeja", "bandejas", "bobina", "caixa", "cx",
    "embalagem", "embalagens", "etiqueta", "etiquetas", "filme", "forma",
    "formas", "forminha", "forminhas", "galvanotek", "lacres", "lacre",
    "marmita", "marmitas", "papel", "pote", "potes", "prato", "pratos",
    "saco", "sacos", "sacola", "sacolas", "tampa", "tampas", "talher", "talheres"
}


def tokens_nutricionais(nome):
    texto = normalizar_busca(nome)
    texto = re.sub(r"\b\d+(?:g|kg|ml|l|un|und)?\b", " ", texto)
    tokens = []
    for token in texto.split():
        if token in PALAVRAS_IGNORADAS_NUTRI:
            continue
        if len(token) <= 1:
            continue
        tokens.append(token)
    return tokens


def eh_item_sem_impacto_nutricional(nome):
    tokens = set(normalizar_busca(nome).split())
    return bool(tokens & PALAVRAS_NAO_ALIMENTO)


def parece_prato_montado(nome):
    texto = " " + normalizar_busca(nome) + " "
    excecoes = [
        " com osso ", " sem osso ", " sem pele ", " com pele ", " barra ",
        " em po ", " em pó ", " de coco ", " de arroz "
    ]
    if any(exc in texto for exc in excecoes):
        return False

    padroes = [
        " combo ", " marmita ", " marmitas ", " acompanhado ", " acompanhada ",
        " acompanha ", " ao molho ", " com molho ", " recheado ", " recheada ",
        " caldo de ", " sopa de ", " escondidinho ", " strogonoff ",
        " feijoada ", " galinhada ", " ragu ", " risoto ", " lasanha ",
        " panqueca ", " pure de ", " purê de ", " salteados ",
        " vegano ", " vegana ", " vegetariano ", " vegetariana ", " muito saborosa "
    ]
    if any(padrao in texto for padrao in padroes):
        return True

    if " com " in texto:
        return True

    return False


def similaridade_nutricional(nome_alvo, nome_base):
    alvo_limpo = " ".join(tokens_nutricionais(nome_alvo))
    base_limpa = " ".join(tokens_nutricionais(str(nome_base or "").replace("(TACO)", "")))
    if not alvo_limpo or not base_limpa:
        return 0.0

    seq = SequenceMatcher(None, alvo_limpo, base_limpa).ratio()
    alvo_tokens = set(alvo_limpo.split())
    base_tokens = set(base_limpa.split())
    intersecao = len(alvo_tokens & base_tokens)
    uniao = len(alvo_tokens | base_tokens) or 1
    cobertura = intersecao / max(len(alvo_tokens), 1)
    jaccard = intersecao / uniao

    if alvo_limpo == base_limpa:
        return 1.0
    if alvo_limpo in base_limpa or base_limpa in alvo_limpo:
        return max(0.86, seq)

    return round((seq * 0.45) + (cobertura * 0.40) + (jaccard * 0.15), 4)


def nutrientes_complementares(nome):
    texto = normalizar_busca(nome)

    def tem(*palavras):
        return any(palavra in texto for palavra in palavras)

    def item(base, kcal, carb, acucar, prot, gord, sat, trans, fibra, sodio):
        return {
            "base": base,
            "valores": [kcal, carb, acucar, 0, prot, gord, sat, trans, fibra, sodio]
        }

    if tem("agua filtrada", "agua mineral", "cha ", "camomila", "capim cidreira", "hibisco", "mulungu", "espinheira", "dente de leao", "cavalinha"):
        return item("Tabela complementar: agua/cha sem acucar", 0, 0, 0, 0, 0, 0, 0, 0, 0)
    if tem("eritritol", "stevia", "stevia refinada"):
        return item("Tabela complementar: adocante sem caloria", 0, 100, 0, 0, 0, 0, 0, 0, 0)
    if tem("xilitol"):
        return item("Tabela complementar: xilitol", 240, 100, 0, 0, 0, 0, 0, 0, 0)
    if tem("bicarbonato"):
        return item("Tabela complementar: bicarbonato de sodio", 0, 0, 0, 0, 0, 0, 0, 0, 27360)
    if tem("sal", "tempero a gosto"):
        return item("Tabela complementar: tempero/sal", 0, 0, 0, 0, 0, 0, 0, 0, 30000)
    if tem("vinagre"):
        return item("Tabela complementar: vinagre", 18, 0.9, 0.4, 0, 0, 0, 0, 0, 5)
    if tem("vinho"):
        return item("Tabela complementar: vinho", 85, 2.6, 1.0, 0.1, 0, 0, 0, 0, 5)
    if tem("ketchup", "catchup"):
        return item("Tabela complementar: ketchup", 112, 26, 22, 1.3, 0.2, 0, 0, 0.3, 900)
    if tem("baunilha"):
        return item("Tabela complementar: extrato de baunilha", 288, 12.7, 12.7, 0.1, 0.1, 0, 0, 0, 9)
    if tem("emulsificante"):
        return item("Tabela complementar: emulsificante alimentar", 420, 65, 0, 1, 12, 4, 0, 0, 200)
    if tem("agar", "goma guar", "goma xantana", "psyllium"):
        return item("Tabela complementar: fibra/goma alimentar", 200, 85, 0, 2, 0, 0, 0, 75, 50)
    if tem("whey", "proteina em po", "levedura nutricional"):
        return item("Tabela complementar: proteina em po/levedura", 380, 20, 6, 55, 6, 2, 0, 8, 300)
    if tem("banha", "panceta", "barriga de porco"):
        return item("Tabela complementar: gordura suina", 898, 0, 0, 0, 99.5, 39, 0, 0, 0)
    if tem("costelinha", "lombo suino", "mignon suino", "pernil", "picanha suina", "sobre coxa", "coxa solteira"):
        return item("Tabela complementar: carne suina/frango", 240, 0, 0, 26, 15, 5, 0, 0, 80)
    if tem("musculo", "patinho"):
        return item("Tabela complementar: carne bovina magra", 220, 0, 0, 32, 8, 3, 0, 0, 60)
    if tem("merlusa", "tilapia", "atum"):
        return item("Tabela complementar: peixe", 120, 0, 0, 24, 3, 1, 0, 0, 55)
    if tem("creme de ricota", "cream cheese", "requeijao"):
        return item("Tabela complementar: creme de queijo", 250, 4, 3, 8, 22, 13, 0, 0, 350)
    if tem("coco ralado", "coco", "côco", "raspa de coco", "raspa de côco"):
        return item("Tabela complementar: coco seco sem acucar", 660, 24, 7, 7, 65, 57, 0, 16, 35)
    if tem("chia"):
        return item("Tabela complementar: chia", 486, 42, 0, 17, 31, 3.3, 0, 34, 16)
    if tem("nozes"):
        return item("Tabela complementar: nozes", 654, 14, 2.6, 15, 65, 6, 0, 6.7, 2)
    if tem("amendoas", "amendoas cruas"):
        return item("Tabela complementar: amendoas", 579, 22, 4.4, 21, 50, 3.8, 0, 12.5, 1)
    if tem("amaranto"):
        return item("Tabela complementar: amaranto em graos/flocos", 371, 65, 1.7, 14, 7, 1.5, 0, 6.7, 4)
    if tem("aveia"):
        return item("Tabela complementar: aveia", 389, 66, 1, 17, 7, 1.2, 0, 10.6, 2)
    if tem("arroz", "fuba", "fubá", "arauta", "araruta", "trigo para kibe", "macarrao", "macarrão", "painco", "painço"):
        return item("Tabela complementar: cereal/farinha/massa", 350, 73, 1, 8, 2, 0.4, 0, 4, 10)
    if tem("lentlha", "lentilha", "fejao", "feijao"):
        return item("Tabela complementar: leguminosa", 116, 20, 1.8, 9, 0.4, 0.1, 0, 8, 2)
    if tem("mandioquinha"):
        return item("Tabela complementar: mandioquinha", 80, 19, 1.8, 1, 0.2, 0, 0, 1.8, 3)
    if tem("manga", "maca", "maça", "amora", "frutas vermelhas", "limao", "limão", "raspa de casca"):
        return item("Tabela complementar: fruta", 55, 14, 10, 0.8, 0.3, 0, 0, 2, 2)
    if tem("aspargo", "espinafre", "pimentao", "pimentão", "pimenta biquinho", "pimenta de cheiro", "pimenta dedo", "pasta de pimenta", "abobora", "abóbora", "gengibre"):
        return item("Tabela complementar: verdura/legume", 30, 6, 2.5, 2, 0.3, 0, 0, 2, 20)
    if tem("champignon", "cogumelo", "shimeji"):
        return item("Tabela complementar: cogumelo", 28, 4.3, 1.7, 2.5, 0.3, 0, 0, 2.5, 5)
    if tem("alecrim", "hortela", "hortelã", "louro", "ervas finas", "oregano", "orégano"):
        return item("Tabela complementar: erva aromatica", 250, 50, 2, 8, 6, 2, 0, 30, 50)
    if tem("acafrao", "açafrao", "açafrão", "curcuma", "cúrcuma", "canela", "cominho", "cravo", "colorau", "curry", "paprica", "páprica", "pimenta branca", "pimenta preta", "pimenta calabreza", "chimichurri", "lemon pepper", "noz moscada"):
        return item("Tabela complementar: especiaria/tempero seco", 300, 55, 3, 10, 8, 2, 0, 25, 80)
    if tem("suco detox", "suco"):
        return item("Tabela complementar: suco de frutas/vegetais", 45, 11, 8, 0.5, 0.1, 0, 0, 0.5, 5)

    return None


def gerar_lote(cursor, nome_base):
    hoje = data_brasilia_obj()
    prefixo = "".join([c for c in str(nome_base or "SN").upper() if c.isalnum()])[:3]
    if len(prefixo) < 3:
        prefixo = (prefixo + "SNX")[:3]
    base = f"{prefixo}{hoje.strftime('%y%m%d')}"
    cursor.execute("SELECT COUNT(*) AS total FROM producoes WHERE lote LIKE ?", (base + "%",))
    sequencia = int(cursor.fetchone()["total"] or 0) + 1
    return f"{base}{sequencia:03d}"


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
    Mantém o módulo Produto Acabado coerente com o estoque cadastrado no Produto Final.
    O Produto Acabado é controlado por lote; quando o usuário informa estoque inicial
    diretamente no cadastro do Produto Final, criamos/ajustamos um lote interno para que
    esse saldo apareça no módulo de Produto Acabado.
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

    # Se o estoque do Produto Final foi reduzido manualmente, baixa a diferença dos lotes mais recentes.
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


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def converter_numero(valor):
    if pd.isna(valor):
        return 0.0

    if isinstance(valor, str):
        valor = valor.strip()

        if valor in ["", "NA", "Na", "na", "Tr", "tr", "*"]:
            return 0.0

        valor = valor.replace(",", ".")

    try:
        return float(valor)
    except Exception:
        return 0.0



def calcular_custo_por_quantidade(preco, unidade, quantidade_base):
    """
    Calcula custo conforme a unidade cadastrada do preço.
    A quantidade informada na ficha técnica continua sendo digitada no campo Peso usado.
    KG e L: preço por unidade maior; G e ML: preço por unidade menor.
    UN: considera a quantidade informada como quantidade de unidades.
    """
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


def calcular_preco_unitario_compra(preco_compra, quantidade_compra, unidade):
    preco_compra = normalizar_float(preco_compra)
    quantidade_compra = normalizar_float(quantidade_compra, 1)
    unidade = str(unidade or "KG").upper().strip()

    if quantidade_compra <= 0:
        quantidade_compra = 1

    if unidade not in UNIDADES_VALIDAS:
        unidade = "KG"

    return round(preco_compra / quantidade_compra, 6), unidade


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





def converter_quantidade_para_estoque(unidade, quantidade_base):
    """
    Converte a quantidade usada na ficha técnica para a unidade de estoque do ingrediente.
    Na ficha técnica o campo usado continua como gramas/ml base.
    KG e L são baixados dividindo por 1000. G e ML são baixados direto.
    UN considera a quantidade informada como quantidade de unidades.
    """
    quantidade_base = float(quantidade_base or 0)
    unidade = str(unidade or "KG").upper().strip()

    if unidade in ["KG", "L"]:
        return quantidade_base / 1000
    if unidade in ["G", "ML", "UN"]:
        return quantidade_base

    return quantidade_base / 1000

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
        "sodio": 0
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
            gramas
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
        "sodio": round((nutrientes["sodio"] / total_gramas_receita) * 100, 1)
    }




def recalcular_todas_receitas(cursor):
    """Compatibilidade do motor de automação.

    Algumas rotas antigas chamavam `recalcular_todas_receitas`, enquanto
    a função oficial do projeto passou a ser `recalcular_receitas_salvas`.
    Mantemos este alias para não quebrar vínculos existentes.
    """
    return recalcular_receitas_salvas(cursor)

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
            int(receita_id)
        ))
        total_recalculadas += 1

    return total_recalculadas



QUALIDADE_ITENS_PADRAO = ["Touca", "Luva", "Uniforme", "Bancada limpa"]
QUALIDADE_TEMPERATURAS_PADRAO = [
    {
        "area": "Fabricação",
        "equipamento": "Processo térmico",
        "temperatura": 60.0,
        "limite_min": 60.0,
        "limite_max": None,
        "observacao": "Fabricada a 60º.",
    },
    {
        "area": "Ultracongelamento",
        "equipamento": "Túnel / ultracongelador",
        "temperatura": -18.0,
        "limite_min": None,
        "limite_max": -18.0,
        "observacao": "Ultracongelada a -18º em até 1 hora.",
    },
]

def registrar_qualidade_padrao_producao(cursor, producao_id, lote, receita_nome, op_codigo='', responsavel='Sistema'):
    """Cria registros automáticos de qualidade para produção concluída."""
    agora = agora_brasilia()
    data_registro = hoje_brasilia()
    referencia = op_codigo or lote or f"Produção #{producao_id}"

    for item in QUALIDADE_ITENS_PADRAO:
        cursor.execute("""
            INSERT INTO qualidade_checklists (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data_registro, "Produção", responsavel, item, "Conforme",
            f"Checklist padrão automático da produção {referencia}.", lote or '', op_codigo or '', agora
        ))

    for padrao in QUALIDADE_TEMPERATURAS_PADRAO:
        cursor.execute("""
            INSERT INTO qualidade_temperaturas (data_registro, area, equipamento, temperatura, limite_min, limite_max, status, responsavel, observacao, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data_registro, padrao["area"], padrao["equipamento"], padrao["temperatura"],
            padrao["limite_min"], padrao["limite_max"], "Conforme", responsavel,
            f"{padrao['observacao']} Registro automático da produção {referencia}.", agora
        ))

    cursor.execute("""
        INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        "Qualidade automática",
        f"Produção #{producao_id} - {receita_nome}: checklist padrão e temperaturas registradas automaticamente.",
        referencia,
        "Conforme",
        agora
    ))


def consolidar_banco_erp(cursor):
    """Consolidação incremental do banco do Wevvo ERP/PDV.

    Esta função não apaga tabelas nem dados existentes. Ela cria apenas estruturas
    de compatibilidade e auditoria para que os submódulos usem uma fonte única
    e para que nomes antigos/novos do projeto continuem funcionando.
    """
    agora = agora_brasilia()

    def objeto_existe(nome):
        row = cursor.execute(
            "SELECT name FROM sqlite_master WHERE name=? AND type IN ('table','view')",
            (nome,)
        ).fetchone()
        return row is not None

    def criar_view_se_nao_existir(nome, sql):
        if not objeto_existe(nome):
            cursor.execute(sql)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_schema_mapa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo TEXT NOT NULL,
            nome_logico TEXT NOT NULL,
            tabela_origem TEXT NOT NULL,
            observacao TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(modulo, nome_logico)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_auditoria_banco (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            tabela_nome TEXT,
            descricao TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OK',
            created_at TEXT NOT NULL
        )
    """)

    # Views de compatibilidade: evitam quebra entre nomes usados nas telas,
    # endpoints novos e tabelas reais já existentes.
    criar_view_se_nao_existir('receita_ingredientes', """
        CREATE VIEW receita_ingredientes AS
        SELECT id, receita_id, ingrediente_id, ingrediente_nome, gramas
        FROM receita_itens
    """)

    criar_view_se_nao_existir('produtos_acabados', """
        CREATE VIEW produtos_acabados AS
        SELECT id, receita_id, codigo_interno, nome, categoria, peso_liquido, porcao,
               preco_venda, estoque_atual, estoque_minimo, validade_dias, conservacao,
               modo_preparo, ingredientes_rotulo, receitas_combo, alergicos, gluten,
               lactose, fabricante, empresa, responsavel, endereco, ativo, created_at, updated_at
        FROM produtos_finais
    """)

    criar_view_se_nao_existir('producao', """
        CREATE VIEW producao AS
        SELECT id, receita_id, receita_nome, produto_final_id, produto_final_nome,
               quantidade, rendimento_base, custo_total, custo_unitario, lote,
               data_fabricacao, data_validade, validade_dias, observacao, created_at
        FROM producoes
    """)

    criar_view_se_nao_existir('lotes', """
        CREATE VIEW lotes AS
        SELECT id, produto_final_id, produto_final_nome, receita_id, receita_nome,
               producao_id, lote, quantidade_produzida, saldo_atual, data_fabricacao,
               data_validade, observacao, created_at
        FROM estoque_produto_acabado
    """)

    criar_view_se_nao_existir('compras', """
        CREATE VIEW compras AS
        SELECT id, solicitacao_id, cotacao_id, fornecedor, fornecedor_id, fornecedor_nome,
               ingrediente_id, ingrediente_nome, quantidade, unidade, valor_total, status,
               previsao_entrega, observacao, created_at, updated_at, financeiro_id,
               data_recebimento, custo_unitario_recebido, estoque_integrado, cmv_recalculado
        FROM compras_pedidos
    """)

    criar_view_se_nao_existir('contas_pagar', """
        CREATE VIEW contas_pagar AS
        SELECT * FROM financeiro_lancamentos
        WHERE LOWER(COALESCE(tipo,'')) IN ('despesa','pagar','conta a pagar')
    """)

    criar_view_se_nao_existir('contas_receber', """
        CREATE VIEW contas_receber AS
        SELECT * FROM financeiro_lancamentos
        WHERE LOWER(COALESCE(tipo,'')) IN ('receita','receber','conta a receber')
    """)

    criar_view_se_nao_existir('qualidade_registros', """
        CREATE VIEW qualidade_registros AS
        SELECT id, data_registro, area, responsavel, item AS descricao, status,
               observacao, lote, op_codigo, created_at, 'checklist' AS origem
        FROM qualidade_checklists
        UNION ALL
        SELECT id, data_registro, area, responsavel, equipamento AS descricao, status,
               observacao, NULL AS lote, NULL AS op_codigo, created_at, 'temperatura' AS origem
        FROM qualidade_temperaturas
    """)

    mapas = [
        ('Cadastros', 'Ingredientes', 'ingredientes', 'Cadastro técnico e nutricional de insumos.'),
        ('Cadastros', 'Receitas', 'receitas', 'Receitas e fichas técnicas principais.'),
        ('Cadastros', 'Produtos acabados', 'produtos_finais', 'Produtos finais vinculados a receitas.'),
        ('Estoque', 'Movimentações de ingredientes', 'movimentacoes_estoque', 'Entradas e saídas de insumos.'),
        ('Estoque', 'Lotes de produto acabado', 'estoque_produto_acabado', 'Saldos por lote e rastreabilidade.'),
        ('Produção', 'Ordens PCP', 'pcp_ordens_producao', 'Planejamento e acompanhamento de produção.'),
        ('Produção', 'Produções concluídas', 'producoes', 'Baixa de ingredientes e geração de produto acabado.'),
        ('Compras', 'Pedidos de compra', 'compras_pedidos', 'Pedidos, recebimento e integração financeira.'),
        ('Vendas', 'Vendas ERP', 'vendas_erp', 'Pedidos/vendas e baixa de estoque.'),
        ('Financeiro', 'Lançamentos', 'financeiro_lancamentos', 'Fonte única para pagar e receber.'),
        ('Qualidade', 'Checklists', 'qualidade_checklists', 'Controle de qualidade operacional.'),
        ('Configurações', 'Usuários', 'usuarios_sistema', 'Login, perfis e permissões.'),
    ]
    for modulo, nome_logico, tabela_origem, observacao in mapas:
        cursor.execute("""
            INSERT INTO erp_schema_mapa (modulo, nome_logico, tabela_origem, observacao, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(modulo, nome_logico) DO UPDATE SET
                tabela_origem=excluded.tabela_origem,
                observacao=excluded.observacao,
                updated_at=excluded.updated_at
        """, (modulo, nome_logico, tabela_origem, observacao, agora))

    # Índices de apoio para consultas integradas, seguros para rodar várias vezes.
    indices = [
        "CREATE INDEX IF NOT EXISTS idx_receita_itens_receita ON receita_itens(receita_id)",
        "CREATE INDEX IF NOT EXISTS idx_receita_itens_ingrediente ON receita_itens(ingrediente_id)",
        "CREATE INDEX IF NOT EXISTS idx_mov_estoque_ing_data ON movimentacoes_estoque(ingrediente_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_prod_finais_receita ON produtos_finais(receita_id)",
        "CREATE INDEX IF NOT EXISTS idx_estoque_pa_prod_lote ON estoque_produto_acabado(produto_final_id, lote)",
        "CREATE INDEX IF NOT EXISTS idx_mov_pa_estoque_data ON movimentacoes_produto_acabado(estoque_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_producoes_receita_data ON producoes(receita_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_vendas_prod_data ON vendas_erp(produto_final_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_compras_ing_status ON compras_pedidos(ingrediente_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_fin_venc_status ON financeiro_lancamentos(data_vencimento, status)",
        "CREATE INDEX IF NOT EXISTS idx_cmv_entidade ON cmv_precificacao(entidade_tipo, entidade_id)"
    ]
    for sql in indices:
        try:
            cursor.execute(sql)
        except Exception as exc:
            cursor.execute("""
                INSERT INTO erp_auditoria_banco (tipo, tabela_nome, descricao, status, created_at)
                VALUES ('indice', NULL, ?, 'ATENCAO', ?)
            """, (str(exc), agora))

    cursor.execute("""
        INSERT INTO erp_auditoria_banco (tipo, tabela_nome, descricao, status, created_at)
        VALUES ('consolidacao', 'banco', 'Consolidação incremental de schema executada sem apagar dados.', 'OK', ?)
    """, (agora,))

def criar_tabelas():
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ingredientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_taco TEXT,
        nome TEXT NOT NULL,
        preco_kg REAL NOT NULL DEFAULT 0,
        calorias_100g REAL NOT NULL DEFAULT 0,
        carboidratos_100g REAL NOT NULL DEFAULT 0,
        proteinas_100g REAL NOT NULL DEFAULT 0,
        gorduras_100g REAL NOT NULL DEFAULT 0,
        sodio_100g REAL NOT NULL DEFAULT 0
    )
    """)

    colunas_ingredientes = {
        "unidade": "TEXT NOT NULL DEFAULT 'KG'",
        "acucares_totais_100g": "REAL NOT NULL DEFAULT 0",
        "acucares_adicionados_100g": "REAL NOT NULL DEFAULT 0",
        "gorduras_saturadas_100g": "REAL NOT NULL DEFAULT 0",
        "gorduras_trans_100g": "REAL NOT NULL DEFAULT 0",
        "fibra_alimentar_100g": "REAL NOT NULL DEFAULT 0",
        "estoque_atual": "REAL NOT NULL DEFAULT 0",
        "estoque_minimo": "REAL NOT NULL DEFAULT 0",
        "ultimo_reajuste_tipo": "TEXT",
        "ultimo_reajuste_valor": "REAL NOT NULL DEFAULT 0",
        "updated_at": "TEXT",
        "fonte_nutricional": "TEXT",
        "taco_match_nome": "TEXT",
        "taco_match_codigo": "TEXT",
        "taco_match_similaridade": "REAL NOT NULL DEFAULT 0",
        "preco_compra": "REAL NOT NULL DEFAULT 0",
        "quantidade_compra": "REAL NOT NULL DEFAULT 1",
        "unidade_compra": "TEXT NOT NULL DEFAULT 'KG'"
    }

    for coluna, definicao in colunas_ingredientes.items():
        if not coluna_existe(cursor, "ingredientes", coluna):
            cursor.execute(f"ALTER TABLE ingredientes ADD COLUMN {coluna} {definicao}")

    cursor.execute("""
        UPDATE ingredientes
        SET preco_compra = CASE WHEN COALESCE(preco_compra, 0) = 0 THEN COALESCE(preco_kg, 0) ELSE preco_compra END,
            quantidade_compra = CASE WHEN COALESCE(quantidade_compra, 0) <= 0 THEN 1 ELSE quantidade_compra END,
            unidade_compra = CASE WHEN COALESCE(unidade_compra, '') = '' THEN COALESCE(unidade, 'KG') ELSE unidade_compra END
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS receitas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        custo_total REAL NOT NULL DEFAULT 0,
        preco_venda REAL NOT NULL DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        descricao TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """)

    colunas_receitas = {
        "rendimento": "REAL NOT NULL DEFAULT 1",
        "margem_lucro": "REAL NOT NULL DEFAULT 0",
        "custo_porcao": "REAL NOT NULL DEFAULT 0",
        "preco_venda_porcao": "REAL NOT NULL DEFAULT 0",
        "calorias": "REAL NOT NULL DEFAULT 0",
        "carboidratos": "REAL NOT NULL DEFAULT 0",
        "proteinas": "REAL NOT NULL DEFAULT 0",
        "gorduras": "REAL NOT NULL DEFAULT 0",
        "acucares_totais": "REAL NOT NULL DEFAULT 0",
        "acucares_adicionados": "REAL NOT NULL DEFAULT 0",
        "gorduras_saturadas": "REAL NOT NULL DEFAULT 0",
        "gorduras_trans": "REAL NOT NULL DEFAULT 0",
        "fibra": "REAL NOT NULL DEFAULT 0",
        "sodio": "REAL NOT NULL DEFAULT 0",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT",
        "categoria_id": "INTEGER",
        "categoria_nome": "TEXT",
        "categoria_produto": "TEXT",
        "rotulo_peso_liquido": "TEXT",
        "rotulo_porcao": "TEXT",
        "rotulo_fabricante": "TEXT",
        "rotulo_data_fabricacao": "TEXT",
        "rotulo_data_validade": "TEXT",
        "rotulo_lote": "TEXT",
        "rotulo_ingredientes": "TEXT",
        "rotulo_alergicos": "TEXT",
        "rotulo_gluten": "TEXT DEFAULT 'CONTÉM GLÚTEN'",
        "rotulo_lactose": "TEXT DEFAULT 'CONTÉM LACTOSE'",
        "rotulo_conservacao": "TEXT",
        "rotulo_modo_preparo": "TEXT",
        "rotulo_empresa": "TEXT",
        "rotulo_responsavel": "TEXT",
        "rotulo_endereco": "TEXT"
    }

    for coluna, definicao in colunas_receitas.items():
        if not coluna_existe(cursor, "receitas", coluna):
            cursor.execute(f"ALTER TABLE receitas ADD COLUMN {coluna} {definicao}")

    sincronizar_categorias_existentes(cursor)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS receita_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receita_id INTEGER NOT NULL,
        ingrediente_id INTEGER NOT NULL,
        ingrediente_nome TEXT NOT NULL,
        gramas REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (receita_id) REFERENCES receitas(id) ON DELETE CASCADE,
        FOREIGN KEY (ingrediente_id) REFERENCES ingredientes(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimentacoes_estoque (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ingrediente_id INTEGER NOT NULL,
        ingrediente_nome TEXT NOT NULL,
        tipo TEXT NOT NULL,
        quantidade REAL NOT NULL DEFAULT 0,
        unidade TEXT NOT NULL DEFAULT 'KG',
        observacao TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (ingrediente_id) REFERENCES ingredientes(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS producoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receita_id INTEGER NOT NULL,
        receita_nome TEXT NOT NULL,
        quantidade REAL NOT NULL DEFAULT 0,
        rendimento_base REAL NOT NULL DEFAULT 1,
        custo_total REAL NOT NULL DEFAULT 0,
        custo_unitario REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (receita_id) REFERENCES receitas(id)
    )
    """)


    colunas_producoes = {
        "produto_final_id": "INTEGER",
        "produto_final_nome": "TEXT",
        "lote": "TEXT",
        "data_fabricacao": "TEXT",
        "data_validade": "TEXT",
        "validade_dias": "INTEGER NOT NULL DEFAULT 0"
    }

    for coluna, definicao in colunas_producoes.items():
        if not coluna_existe(cursor, "producoes", coluna):
            cursor.execute(f"ALTER TABLE producoes ADD COLUMN {coluna} {definicao}")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pcp_ordens_producao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT,
        receita_id INTEGER,
        receita_nome TEXT NOT NULL,
        produto_final_id INTEGER,
        produto_final_nome TEXT,
        quantidade_planejada REAL NOT NULL DEFAULT 0,
        quantidade_produzida REAL NOT NULL DEFAULT 0,
        data_planejada TEXT,
        turno TEXT,
        responsavel TEXT,
        capacidade_hora REAL NOT NULL DEFAULT 0,
        tempo_previsto_horas REAL NOT NULL DEFAULT 0,
        prioridade TEXT DEFAULT 'Normal',
        status TEXT DEFAULT 'Planejada',
        observacao TEXT,
        producao_id INTEGER,
        lote TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )
    """)




    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qualidade_checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_registro TEXT NOT NULL,
        area TEXT NOT NULL,
        responsavel TEXT,
        item TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Conforme',
        observacao TEXT,
        lote TEXT,
        op_codigo TEXT,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_qualidade_checklists_data ON qualidade_checklists(data_registro)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_qualidade_checklists_status ON qualidade_checklists(status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qualidade_temperaturas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_registro TEXT NOT NULL,
        area TEXT NOT NULL,
        equipamento TEXT,
        temperatura REAL NOT NULL DEFAULT 0,
        limite_min REAL,
        limite_max REAL,
        status TEXT NOT NULL DEFAULT 'Conforme',
        responsavel TEXT,
        observacao TEXT,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_qualidade_temperaturas_data ON qualidade_temperaturas(data_registro)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qualidade_nao_conformidades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data_registro TEXT NOT NULL,
        origem TEXT NOT NULL DEFAULT 'Manual',
        lote TEXT,
        op_codigo TEXT,
        descricao TEXT NOT NULL,
        gravidade TEXT NOT NULL DEFAULT 'Média',
        acao_corretiva TEXT,
        responsavel TEXT,
        status TEXT NOT NULL DEFAULT 'Aberta',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_qualidade_nc_status ON qualidade_nao_conformidades(status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qualidade_auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        descricao TEXT NOT NULL,
        referencia TEXT,
        status TEXT,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS perfil_empresa (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        tipo_pessoa TEXT DEFAULT 'juridica',
        razao_social TEXT,
        nome_fantasia TEXT,
        documento TEXT,
        inscricao_estadual TEXT,
        responsavel TEXT DEFAULT 'Indústria Brasileira',
        endereco TEXT,
        cidade TEXT,
        uf TEXT,
        cep TEXT,
        telefone TEXT,
        email TEXT,
        site TEXT,
        texto_produzido_por TEXT,
        texto_endereco_completo TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO perfil_empresa (
            id, tipo_pessoa, razao_social, nome_fantasia, documento, responsavel,
            endereco, cidade, uf, cep, telefone, email, texto_produzido_por, texto_endereco_completo
        ) VALUES (
            1, 'juridica', 'WEVVO', 'Wevvo', '09.541.530/0001-11', 'Indústria Brasileira',
            'Rua Maria Dorizotto Frasson, 143 Santa Fé 3', 'Piracicaba', 'SP', '13401-857', '1930362036',
            'fernandesnutri@saudeenutri.com.br', 'PRODUZIDO POR: WEVVO',
            'Rua Maria Dorizotto Frasson, 143 Santa Fé 3 Piracicaba - SP · CEP 13401-857 · CNPJ: 09.541.530/0001-11 · fernandesnutri@saudeenutri.com.br · Telefone: 1930362036'
        )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos_finais (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receita_id INTEGER,
        codigo_interno TEXT,
        nome TEXT NOT NULL,
        categoria TEXT,
        peso_liquido TEXT,
        porcao TEXT DEFAULT '100 g (1 embalagem)',
        preco_venda REAL NOT NULL DEFAULT 0,
        estoque_atual REAL NOT NULL DEFAULT 0,
        estoque_minimo REAL NOT NULL DEFAULT 0,
        validade_dias INTEGER NOT NULL DEFAULT 0,
        conservacao TEXT,
        modo_preparo TEXT,
        ingredientes_rotulo TEXT,
        receitas_combo TEXT,
        alergicos TEXT,
        gluten TEXT DEFAULT 'CONTÉM GLÚTEN',
        lactose TEXT DEFAULT 'CONTÉM LACTOSE',
        fabricante TEXT DEFAULT 'Wevvo',
        empresa TEXT DEFAULT 'PRODUZIDO POR: WEVVO',
        responsavel TEXT DEFAULT 'Indústria Brasileira',
        endereco TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,
        FOREIGN KEY (receita_id) REFERENCES receitas(id)
    )
    """)

    colunas_produtos_finais = {
        "receitas_combo": "TEXT"
    }

    for coluna, definicao in colunas_produtos_finais.items():
        if not coluna_existe(cursor, "produtos_finais", coluna):
            cursor.execute(f"ALTER TABLE produtos_finais ADD COLUMN {coluna} {definicao}")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cmv_precificacao (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entidade_tipo TEXT NOT NULL,
        entidade_id INTEGER NOT NULL,
        embalagem_unitaria REAL NOT NULL DEFAULT 0,
        custo_fixo_unitario REAL NOT NULL DEFAULT 0,
        custo_variavel_percentual REAL NOT NULL DEFAULT 0,
        margem_desejada_percentual REAL NOT NULL DEFAULT 30,
        preco_praticado REAL NOT NULL DEFAULT 0,
        custo_base REAL NOT NULL DEFAULT 0,
        cmv_real REAL NOT NULL DEFAULT 0,
        preco_minimo REAL NOT NULL DEFAULT 0,
        preco_sugerido REAL NOT NULL DEFAULT 0,
        lucro_real REAL NOT NULL DEFAULT 0,
        margem_real_percentual REAL NOT NULL DEFAULT 0,
        markup REAL NOT NULL DEFAULT 0,
        status TEXT,
        observacao TEXT,
        updated_at TEXT,
        UNIQUE(entidade_tipo, entidade_id)
    )
    """)

    colunas_cmv_precificacao = {
        "custo_base": "REAL NOT NULL DEFAULT 0",
        "cmv_real": "REAL NOT NULL DEFAULT 0",
        "preco_minimo": "REAL NOT NULL DEFAULT 0",
        "preco_sugerido": "REAL NOT NULL DEFAULT 0",
        "lucro_real": "REAL NOT NULL DEFAULT 0",
        "margem_real_percentual": "REAL NOT NULL DEFAULT 0",
        "markup": "REAL NOT NULL DEFAULT 0",
        "status": "TEXT"
    }
    for coluna, definicao in colunas_cmv_precificacao.items():
        if not coluna_existe(cursor, "cmv_precificacao", coluna):
            cursor.execute(f"ALTER TABLE cmv_precificacao ADD COLUMN {coluna} {definicao}")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS estoque_produto_acabado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        produto_final_id INTEGER,
        produto_final_nome TEXT NOT NULL,
        receita_id INTEGER,
        receita_nome TEXT,
        producao_id INTEGER,
        lote TEXT NOT NULL,
        quantidade_produzida REAL NOT NULL DEFAULT 0,
        saldo_atual REAL NOT NULL DEFAULT 0,
        data_fabricacao TEXT,
        data_validade TEXT,
        observacao TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (produto_final_id) REFERENCES produtos_finais(id),
        FOREIGN KEY (receita_id) REFERENCES receitas(id),
        FOREIGN KEY (producao_id) REFERENCES producoes(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimentacoes_produto_acabado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        estoque_id INTEGER NOT NULL,
        produto_final_id INTEGER,
        produto_final_nome TEXT NOT NULL,
        lote TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'SAIDA',
        quantidade REAL NOT NULL DEFAULT 0,
        saldo_anterior REAL NOT NULL DEFAULT 0,
        saldo_atual REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (estoque_id) REFERENCES estoque_produto_acabado(id),
        FOREIGN KEY (produto_final_id) REFERENCES produtos_finais(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS producao_itens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producao_id INTEGER NOT NULL,
        ingrediente_id INTEGER NOT NULL,
        ingrediente_nome TEXT NOT NULL,
        quantidade_baixada REAL NOT NULL DEFAULT 0,
        unidade TEXT NOT NULL DEFAULT 'KG',
        FOREIGN KEY (producao_id) REFERENCES producoes(id) ON DELETE CASCADE,
        FOREIGN KEY (ingrediente_id) REFERENCES ingredientes(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS integracoes_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE,
        tipo TEXT,
        ambiente TEXT DEFAULT 'sandbox',
        loja TEXT,
        usuario TEXT,
        api_key TEXT,
        token TEXT,
        chave_pix TEXT,
        endpoint TEXT,
        ativo INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Pendente',
        ultimo_teste TEXT,
        ultima_sincronizacao TEXT,
        observacao TEXT,
        updated_at TEXT
    )
    """)


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS integracoes_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integracao TEXT NOT NULL,
        acao TEXT NOT NULL,
        status TEXT NOT NULL,
        mensagem TEXT,
        detalhes TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS integracoes_operacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        integracao TEXT NOT NULL,
        acao TEXT NOT NULL,
        tipo_impacto TEXT NOT NULL,
        entidade TEXT,
        entidade_id INTEGER,
        quantidade REAL DEFAULT 0,
        valor REAL DEFAULT 0,
        mensagem TEXT,
        created_at TEXT NOT NULL
    )
    """)


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS automacoes_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origem TEXT NOT NULL,
        acao TEXT NOT NULL,
        entidade TEXT,
        entidade_id INTEGER,
        status TEXT NOT NULL DEFAULT 'sucesso',
        mensagem TEXT,
        detalhes TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vendas_erp (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        origem TEXT NOT NULL DEFAULT 'ERP',
        produto_final_id INTEGER,
        produto_final_nome TEXT,
        estoque_id INTEGER,
        lote TEXT,
        quantidade REAL NOT NULL DEFAULT 0,
        valor_unitario REAL NOT NULL DEFAULT 0,
        valor_total REAL NOT NULL DEFAULT 0,
        custo_unitario REAL NOT NULL DEFAULT 0,
        cmv_total REAL NOT NULL DEFAULT 0,
        lucro_estimado REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL
    )
    """)


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS financeiro_lancamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL DEFAULT 'receita',
        descricao TEXT NOT NULL,
        categoria TEXT,
        origem TEXT NOT NULL DEFAULT 'Manual',
        origem_id INTEGER,
        canal TEXT,
        valor REAL NOT NULL DEFAULT 0,
        data_emissao TEXT NOT NULL,
        data_vencimento TEXT,
        data_pagamento TEXT,
        status TEXT NOT NULL DEFAULT 'Pendente',
        forma_pagamento TEXT,
        observacao TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_financeiro_tipo_status ON financeiro_lancamentos(tipo, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_financeiro_origem ON financeiro_lancamentos(origem, origem_id)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo_pessoa TEXT NOT NULL DEFAULT 'PF',
        nome TEXT NOT NULL,
        documento TEXT,
        telefone TEXT,
        whatsapp TEXT,
        email TEXT,
        cep TEXT,
        endereco TEXT,
        numero TEXT,
        complemento TEXT,
        bairro TEXT,
        cidade TEXT,
        uf TEXT,
        condicao_pagamento TEXT,
        limite_credito REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        status TEXT NOT NULL DEFAULT 'Ativo',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clientes_nome ON clientes(nome)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clientes_status ON clientes(status)")

    for coluna, definicao in {
        'cep': 'TEXT',
        'numero': 'TEXT',
        'complemento': 'TEXT',
        'bairro': 'TEXT'
    }.items():
        if coluna_existe(cursor, 'clientes', coluna) == False:
            cursor.execute(f"ALTER TABLE clientes ADD COLUMN {coluna} {definicao}")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clientes_interacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cliente_id INTEGER NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'observacao',
        descricao TEXT NOT NULL,
        origem TEXT NOT NULL DEFAULT 'ERP',
        created_at TEXT NOT NULL,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id)
    )
    """)

    for coluna, definicao in {
        'cliente_id': 'INTEGER',
        'cliente_nome': 'TEXT'
    }.items():
        if coluna_existe(cursor, 'vendas_erp', coluna) == False:
            cursor.execute(f"ALTER TABLE vendas_erp ADD COLUMN {coluna} {definicao}")
        if coluna_existe(cursor, 'financeiro_lancamentos', coluna) == False:
            cursor.execute(f"ALTER TABLE financeiro_lancamentos ADD COLUMN {coluna} {definicao}")


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fornecedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo_pessoa TEXT NOT NULL DEFAULT 'PJ',
        nome TEXT NOT NULL,
        documento TEXT,
        contato TEXT,
        telefone TEXT,
        whatsapp TEXT,
        email TEXT,
        cep TEXT,
        endereco TEXT,
        numero TEXT,
        complemento TEXT,
        bairro TEXT,
        cidade TEXT,
        uf TEXT,
        condicao_pagamento TEXT,
        prazo_padrao_dias INTEGER NOT NULL DEFAULT 0,
        avaliacao INTEGER NOT NULL DEFAULT 5,
        produtos_fornecidos TEXT,
        observacao TEXT,
        status TEXT NOT NULL DEFAULT 'Ativo',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fornecedores_nome ON fornecedores(nome)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fornecedores_status ON fornecedores(status)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fornecedores_ingredientes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fornecedor_id INTEGER NOT NULL,
        ingrediente_id INTEGER NOT NULL,
        ingrediente_nome TEXT NOT NULL,
        unidade TEXT NOT NULL DEFAULT 'KG',
        preco_unitario REAL NOT NULL DEFAULT 0,
        prazo_dias INTEGER NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (fornecedor_id) REFERENCES fornecedores(id),
        FOREIGN KEY (ingrediente_id) REFERENCES ingredientes(id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_forn_ing_fornecedor ON fornecedores_ingredientes(fornecedor_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_forn_ing_ingrediente ON fornecedores_ingredientes(ingrediente_id)")



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_solicitacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ingrediente_id INTEGER,
        ingrediente_nome TEXT NOT NULL,
        unidade TEXT NOT NULL DEFAULT 'KG',
        estoque_atual REAL NOT NULL DEFAULT 0,
        estoque_minimo REAL NOT NULL DEFAULT 0,
        quantidade_sugerida REAL NOT NULL DEFAULT 0,
        quantidade_solicitada REAL NOT NULL DEFAULT 0,
        prioridade TEXT NOT NULL DEFAULT 'Normal',
        status TEXT NOT NULL DEFAULT 'Aberta',
        observacao TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_cotacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        solicitacao_id INTEGER,
        fornecedor TEXT NOT NULL,
        preco_total REAL NOT NULL DEFAULT 0,
        prazo_dias INTEGER NOT NULL DEFAULT 0,
        observacao TEXT,
        selecionada INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (solicitacao_id) REFERENCES compras_solicitacoes(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS compras_pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        solicitacao_id INTEGER,
        cotacao_id INTEGER,
        fornecedor TEXT NOT NULL,
        ingrediente_id INTEGER,
        ingrediente_nome TEXT NOT NULL,
        quantidade REAL NOT NULL DEFAULT 0,
        unidade TEXT NOT NULL DEFAULT 'KG',
        valor_total REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'Aberto',
        previsao_entrega TEXT,
        observacao TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        FOREIGN KEY (solicitacao_id) REFERENCES compras_solicitacoes(id),
        FOREIGN KEY (cotacao_id) REFERENCES compras_cotacoes(id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_compras_status ON compras_solicitacoes(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pedidos_compras_status ON compras_pedidos(status)")
    for coluna, definicao in {
        'fornecedor_id': 'INTEGER',
        'fornecedor_nome': 'TEXT'
    }.items():
        if coluna_existe(cursor, 'compras_cotacoes', coluna) == False:
            cursor.execute(f"ALTER TABLE compras_cotacoes ADD COLUMN {coluna} {definicao}")
        if coluna_existe(cursor, 'compras_pedidos', coluna) == False:
            cursor.execute(f"ALTER TABLE compras_pedidos ADD COLUMN {coluna} {definicao}")

    # Campos de integração automática Compras -> Estoque -> Financeiro -> CMV.
    # Incremental: apenas adiciona colunas quando não existem.
    for coluna, definicao in {
        'financeiro_id': 'INTEGER',
        'data_recebimento': 'TEXT',
        'custo_unitario_recebido': 'REAL NOT NULL DEFAULT 0',
        'estoque_integrado': 'INTEGER NOT NULL DEFAULT 0',
        'cmv_recalculado': 'INTEGER NOT NULL DEFAULT 0'
    }.items():
        if coluna_existe(cursor, 'compras_pedidos', coluna) == False:
            cursor.execute(f"ALTER TABLE compras_pedidos ADD COLUMN {coluna} {definicao}")


    # Cadastros auxiliares profissionais (base para submenus do tipo Bling).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cadastros_auxiliares (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        modulo TEXT NOT NULL,
        nome TEXT NOT NULL,
        descricao TEXT,
        status TEXT NOT NULL DEFAULT 'Ativo',
        valor_padrao REAL NOT NULL DEFAULT 0,
        observacao TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cad_aux_modulo ON cadastros_auxiliares(modulo, status)")

    cadastros_aux_padrao = [
        ('vendedores', 'Vendedor padrão', 'Equipe comercial'),
        ('funcionarios', 'Responsável operacional', 'Usuário interno para produção/qualidade'),
        ('contas-financeiras', 'Caixa principal', 'Conta padrão para movimentações internas'),
        ('categorias-financeiras', 'Compras de insumos', 'Categoria de contas a pagar'),
        ('categorias-financeiras', 'Vendas de produtos', 'Categoria de contas a receber'),
        ('formas-pagamento', 'PIX', 'Forma de pagamento'),
        ('formas-pagamento', 'Boleto', 'Forma de pagamento'),
        ('formas-pagamento', 'Cartão', 'Forma de pagamento'),
        ('listas-precos', 'Lista padrão', 'Lista de preços principal'),
        ('depositos', 'Estoque principal', 'Depósito padrão'),
        ('multiempresa', 'Empresa principal', 'Unidade principal do ERP')
    ]
    for modulo, nome, descricao in cadastros_aux_padrao:
        cursor.execute("""
            INSERT INTO cadastros_auxiliares (modulo, nome, descricao, status, created_at, updated_at)
            SELECT ?, ?, ?, 'Ativo', ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM cadastros_auxiliares WHERE modulo = ? AND nome = ?
            )
        """, (modulo, nome, descricao, agora_brasilia(), agora_brasilia(), modulo, nome))


    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios_sistema (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL UNIQUE,
        email TEXT,
        senha_hash TEXT,
        perfil TEXT NOT NULL DEFAULT 'Administrador',
        status TEXT NOT NULL DEFAULT 'Ativo',
        ultimo_acesso TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )
    """)
    # Permissões e controle de acesso por usuário.
    try:
        if not coluna_existe(cursor, 'usuarios_sistema', 'permissoes'):
            cursor.execute("ALTER TABLE usuarios_sistema ADD COLUMN permissoes TEXT DEFAULT 'todos'")
        if not coluna_existe(cursor, 'usuarios_sistema', 'observacao'):
            cursor.execute("ALTER TABLE usuarios_sistema ADD COLUMN observacao TEXT")
    except Exception:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sistema_configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT,
        tipo TEXT DEFAULT 'texto',
        updated_at TEXT
    )
    """)
    cursor.execute("""
        INSERT INTO usuarios_sistema (usuario, email, perfil, status, created_at, updated_at)
        SELECT 'administrador', '', 'Administrador', 'Ativo', ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM usuarios_sistema WHERE usuario='administrador')
    """, (agora_brasilia(), agora_brasilia()))



    integracoes_padrao = [
        ('Bling', 'ERP / pedidos / estoque'),
        ('Mercado Livre', 'Marketplace'),
        ('Shopee', 'Marketplace'),
        ('iFood', 'Delivery'),
        ('WhatsApp', 'Atendimento / pedidos'),
        ('PIX', 'Pagamento'),
        ('NFC-e / SAT', 'Fiscal'),
        ('Impressoras térmicas', 'Impressão')
    ]
    for nome, tipo in integracoes_padrao:
        cursor.execute("""
            INSERT OR IGNORE INTO integracoes_config (nome, tipo, status, observacao, updated_at)
            VALUES (?, ?, 'Pendente', 'Aguardando configuração.', ?)
        """, (nome, tipo, agora_brasilia()))

    conn.commit()
    conn.close()


def importar_ingredientes_precificacao():
    if not os.path.exists(ARQUIVO_INGREDIENTES_PRECIFICACAO):
        return False

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM ingredientes
        WHERE fonte_nutricional = 'PRECIFICACAO_TACO'
           OR codigo_taco LIKE 'PREC-%'
    """)
    if int(cursor.fetchone()["total"] or 0) > 0:
        conn.close()
        print("-> Ingredientes da precificação já importados no banco.")
        return True

    cursor.execute("SELECT COUNT(*) AS total FROM ingredientes")
    total = int(cursor.fetchone()["total"] or 0)

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM ingredientes
        WHERE nome LIKE '%(TACO)%'
    """)
    total_taco = int(cursor.fetchone()["total"] or 0)

    if total > 0 and total_taco == 0:
        conn.close()
        print("-> Banco já possui ingredientes próprios; importação da precificação ignorada.")
        return True

    print("-> Importando ingredientes da planilha de precificação com equivalência TACO...")

    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute("DELETE FROM ingredientes")
    cursor.execute("DELETE FROM sqlite_sequence WHERE name = 'ingredientes'")

    importados = 0
    with open(ARQUIVO_INGREDIENTES_PRECIFICACAO, newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)
        for linha in leitor:
            nome = str(linha.get("nome") or "").strip()
            if not nome:
                continue

            preco = normalizar_float(linha.get("preco"), 0)
            unidade = str(linha.get("unidade") or "KG").upper().strip()
            if unidade not in UNIDADES_VALIDAS:
                unidade = "KG"

            codigo_taco = str(linha.get("taco_codigo") or "").strip()
            taco_nome = str(linha.get("taco_nome") or "").strip()
            similaridade = normalizar_float(linha.get("similaridade"), 0)

            cursor.execute("""
                INSERT INTO ingredientes (
                    codigo_taco,
                    nome,
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
                    sodio_100g,
                    fonte_nutricional,
                    taco_match_nome,
                    taco_match_codigo,
                    taco_match_similaridade,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "PREC-" + (codigo_taco or str(importados + 1)),
                nome,
                preco,
                unidade,
                normalizar_float(linha.get("calorias"), 0),
                normalizar_float(linha.get("carboidratos"), 0),
                0.0,
                0.0,
                normalizar_float(linha.get("proteinas"), 0),
                normalizar_float(linha.get("gorduras"), 0),
                normalizar_float(linha.get("gorduras_saturadas"), 0),
                normalizar_float(linha.get("gorduras_trans"), 0),
                normalizar_float(linha.get("fibra"), 0),
                normalizar_float(linha.get("sodio"), 0),
                "PRECIFICACAO_TACO",
                taco_nome,
                codigo_taco,
                similaridade,
                agora_brasilia()
            ))
            importados += 1

    conn.commit()
    conn.close()

    print(f"-> Ingredientes da precificação importados: {importados}.")
    return True


def importar_taco():
    if importar_ingredientes_precificacao():
        return

    if not os.path.exists(ARQUIVO_TACO):
        print(f"-> Arquivo {ARQUIVO_TACO} não encontrado.")
        return

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM ingredientes")
    total = cursor.fetchone()["total"]

    if total > 0:
        conn.close()
        print("-> Ingredientes já importados no banco.")
        return

    print("-> Importando Tabela TACO completa...")

    try:
        df = pd.read_excel(ARQUIVO_TACO, sheet_name="CMVCol taco3", header=None)
    except Exception:
        df = pd.read_excel(ARQUIVO_TACO, sheet_name=0, header=None)

    alimentos_importados = 0

    for _, linha in df.iterrows():
        codigo = linha[0] if len(linha) > 0 else None
        nome = linha[1] if len(linha) > 1 else None

        if pd.isna(codigo) or pd.isna(nome):
            continue

        codigo_texto = str(codigo).strip()
        nome_texto = str(nome).strip()

        if not codigo_texto.replace(".0", "").isdigit():
            continue

        if nome_texto == "":
            continue

        calorias = converter_numero(linha[3]) if len(linha) > 3 else 0
        proteinas = converter_numero(linha[5]) if len(linha) > 5 else 0
        gorduras = converter_numero(linha[6]) if len(linha) > 6 else 0
        carboidratos = converter_numero(linha[8]) if len(linha) > 8 else 0
        sodio = converter_numero(linha[17]) if len(linha) > 17 else 0

        cursor.execute("""
            INSERT INTO ingredientes (
                codigo_taco,
                nome,
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            codigo_texto.replace(".0", ""),
            nome_texto + " (TACO)",
            0.0,
            "KG",
            calorias,
            carboidratos,
            0.0,
            0.0,
            proteinas,
            gorduras,
            0.0,
            0.0,
            0.0,
            sodio
        ))

        alimentos_importados += 1

    conn.commit()
    conn.close()

    print(f"-> Tabela TACO importada com sucesso: {alimentos_importados} alimentos.")


def obter_ou_criar_ingrediente_receita(cursor, nome, mapa_ingredientes):
    nome = str(nome or "").strip()
    chave = normalizar_busca(nome)
    if chave in mapa_ingredientes:
        return mapa_ingredientes[chave]

    for chave_existente, item in mapa_ingredientes.items():
        if chave and (chave in chave_existente or chave_existente in chave):
            return item

    cursor.execute("""
        INSERT INTO ingredientes (
            codigo_taco,
            nome,
            preco_kg,
            unidade,
            preco_compra,
            quantidade_compra,
            unidade_compra,
            fonte_nutricional,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        None,
        nome,
        0,
        "KG",
        0,
        1,
        "KG",
        "IMPORTADO_RECEITA",
        agora_brasilia()
    ))
    item = {"id": cursor.lastrowid, "nome": nome}
    mapa_ingredientes[chave] = item
    return item


def atualizar_ingrediente_tecnico_da_receita(cursor, receita_id, receita_nome, mapa_ingredientes):
    totais = calcular_totais_receita_por_id(cursor, receita_id)
    if not totais:
        return

    cursor.execute("""
        SELECT COALESCE(SUM(gramas), 0) AS total_gramas
        FROM receita_itens
        WHERE receita_id = ?
    """, (receita_id,))
    total_gramas = float(cursor.fetchone()["total_gramas"] or 0)
    preco_kg = 0
    if total_gramas > 0:
        preco_kg = round(float(totais["custo_total"] or 0) / (total_gramas / 1000), 6)

    chave = normalizar_busca(receita_nome)
    cursor.execute("""
        SELECT id
        FROM ingredientes
        WHERE LOWER(nome) = LOWER(?)
        LIMIT 1
    """, (receita_nome,))
    ingrediente = cursor.fetchone()

    valores = (
        receita_nome,
        preco_kg,
        "KG",
        preco_kg,
        1,
        "KG",
        totais["calorias"],
        totais["carboidratos"],
        totais["acucares_totais"],
        totais["acucares_adicionados"],
        totais["proteinas"],
        totais["gorduras"],
        totais["gorduras_saturadas"],
        totais["gorduras_trans"],
        totais["fibra"],
        totais["sodio"],
        "RECEITA_TECNICA",
        agora_brasilia()
    )

    if ingrediente:
        cursor.execute("""
            UPDATE ingredientes
            SET nome = ?,
                preco_kg = ?,
                unidade = ?,
                preco_compra = ?,
                quantidade_compra = ?,
                unidade_compra = ?,
                calorias_100g = ?,
                carboidratos_100g = ?,
                acucares_totais_100g = ?,
                acucares_adicionados_100g = ?,
                proteinas_100g = ?,
                gorduras_100g = ?,
                gorduras_saturadas_100g = ?,
                gorduras_trans_100g = ?,
                fibra_alimentar_100g = ?,
                sodio_100g = ?,
                fonte_nutricional = ?,
                updated_at = ?
            WHERE id = ?
        """, valores + (ingrediente["id"],))
        ingrediente_id = ingrediente["id"]
    else:
        cursor.execute("""
            INSERT INTO ingredientes (
                nome,
                preco_kg,
                unidade,
                preco_compra,
                quantidade_compra,
                unidade_compra,
                calorias_100g,
                carboidratos_100g,
                acucares_totais_100g,
                acucares_adicionados_100g,
                proteinas_100g,
                gorduras_100g,
                gorduras_saturadas_100g,
                gorduras_trans_100g,
                fibra_alimentar_100g,
                sodio_100g,
                fonte_nutricional,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, valores)
        ingrediente_id = cursor.lastrowid

    mapa_ingredientes[chave] = {"id": ingrediente_id, "nome": receita_nome}


def importar_receitas_categorias_precificacao():
    if not os.path.exists(ARQUIVO_RECEITAS_CATEGORIAS):
        return

    receitas_csv = {}
    with open(ARQUIVO_RECEITAS_CATEGORIAS, newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)
        for linha in leitor:
            categoria = str(linha.get("categoria") or "").strip()
            receita = str(linha.get("receita") or "").strip()
            ingrediente = str(linha.get("ingrediente") or "").strip()
            quantidade = normalizar_float(linha.get("quantidade"), 0)
            origem = str(linha.get("origem") or "").strip()

            if not categoria or not receita:
                continue

            chave = (categoria, receita)
            receitas_csv.setdefault(chave, {"origem": origem, "itens": []})
            if ingrediente and quantidade > 0:
                receitas_csv[chave]["itens"].append({
                    "ingrediente": ingrediente,
                    "quantidade": quantidade
                })

    total_esperado = len(receitas_csv)
    if total_esperado <= 0:
        return

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute("SELECT COUNT(*) AS total FROM receitas")
    total_atual = int(cursor.fetchone()["total"] or 0)

    combo_antigo_com_gramas_erradas = False
    sem_ingredientes_tecnicos = False
    combo_com_valor_zerado = False
    if total_atual == total_esperado:
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM receitas r
            JOIN receita_itens ri ON ri.receita_id = r.id
            WHERE r.categoria_nome = 'Combos'
              AND r.nome LIKE '%14unds/250g%'
              AND ri.gramas > 0
              AND ri.gramas < 100
        """)
        combo_antigo_com_gramas_erradas = int(cursor.fetchone()["total"] or 0) > 0
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM ingredientes
            WHERE fonte_nutricional = 'RECEITA_TECNICA'
        """)
        sem_ingredientes_tecnicos = int(cursor.fetchone()["total"] or 0) == 0
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM receitas
            WHERE categoria_nome = 'Combos'
              AND nome LIKE '%14unds/250g%'
              AND COALESCE(custo_total, 0) = 0
        """)
        combo_com_valor_zerado = int(cursor.fetchone()["total"] or 0) > 0

    if total_atual > 0 and (
        combo_antigo_com_gramas_erradas
        or sem_ingredientes_tecnicos
        or combo_com_valor_zerado
        or (total_atual != total_esperado and total_atual in [458, 460, 491])
    ):
        cursor.execute("DELETE FROM receita_itens")
        cursor.execute("DELETE FROM receitas")
        cursor.execute("DELETE FROM categorias")
        conn.commit()
        total_atual = 0

    if total_atual > 0:
        conn.close()
        print("-> Receitas já existem no banco; importação de categorias ignorada.")
        return

    cursor.execute("SELECT id, nome FROM ingredientes")
    mapa_ingredientes = {
        normalizar_busca(item["nome"]): {"id": item["id"], "nome": item["nome"]}
        for item in cursor.fetchall()
    }

    importadas = 0
    itens_importados = 0

    for (categoria_nome, receita_nome), dados_receita in receitas_csv.items():
        itens = dados_receita["itens"]
        origem = dados_receita["origem"]
        categoria_id = garantir_categoria(cursor, categoria_nome)

        cursor.execute("""
            INSERT INTO receitas (
                nome,
                categoria_id,
                categoria_nome,
                categoria_produto,
                rendimento,
                margem_lucro,
                custo_total,
                custo_porcao,
                preco_venda,
                preco_venda_porcao,
                calorias,
                carboidratos,
                proteinas,
                gorduras,
                acucares_totais,
                acucares_adicionados,
                gorduras_saturadas,
                gorduras_trans,
                fibra,
                sodio,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            receita_nome,
            categoria_id,
            categoria_nome,
            categoria_nome,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            agora_brasilia(),
            agora_brasilia()
        ))
        receita_id = cursor.lastrowid
        importadas += 1

        for item in itens:
            ingrediente = obter_ou_criar_ingrediente_receita(
                cursor,
                item["ingrediente"],
                mapa_ingredientes
            )
            cursor.execute("""
                INSERT INTO receita_itens (
                    receita_id,
                    ingrediente_id,
                    ingrediente_nome,
                    gramas
                )
                VALUES (?, ?, ?, ?)
            """, (
                receita_id,
                ingrediente["id"],
                ingrediente["nome"],
                item["quantidade"]
            ))
            itens_importados += 1

        recalcular_receitas_salvas(cursor, receita_id)
        if not origem.startswith("MontagemdosCombos") and categoria_nome != "Combos":
            atualizar_ingrediente_tecnico_da_receita(cursor, receita_id, receita_nome, mapa_ingredientes)

    recalcular_receitas_salvas(cursor)

    conn.commit()
    conn.close()
    print(f"-> Categorias e receitas importadas: {importadas} receitas, {itens_importados} itens.")


def iniciar_sistema():
    criar_tabelas()
    importar_taco()
    importar_receitas_categorias_precificacao()
    print("-> SISTEMA PRONTO!")


iniciar_sistema()



def montar_perfil_empresa_dict(row):
    if not row:
        return {
            "tipo_pessoa": "juridica",
            "razao_social": "WEVVO",
            "nome_fantasia": "Wevvo",
            "documento": "09.541.530/0001-11",
            "inscricao_estadual": "",
            "responsavel": "Indústria Brasileira",
            "endereco": "Rua Maria Dorizotto Frasson, 143 Santa Fé 3",
            "cidade": "Piracicaba",
            "uf": "SP",
            "cep": "13401-857",
            "telefone": "1930362036",
            "email": "fernandesnutri@saudeenutri.com.br",
            "site": "",
            "texto_produzido_por": "PRODUZIDO POR: WEVVO",
            "texto_endereco_completo": "Rua Maria Dorizotto Frasson, 143 Santa Fé 3 Piracicaba - SP · CEP 13401-857 · CNPJ: 09.541.530/0001-11 · fernandesnutri@saudeenutri.com.br · Telefone: 1930362036"
        }
    dados = dict(row)
    documento = (dados.get("documento") or "").strip()
    tipo = (dados.get("tipo_pessoa") or "juridica").strip()
    rotulo_doc = "CPF" if tipo == "fisica" else "CNPJ"
    nome = (dados.get("nome_fantasia") or dados.get("razao_social") or "Wevvo").strip()
    produzido = (dados.get("texto_produzido_por") or f"PRODUZIDO POR: {nome.upper()}").strip()
    endereco_base = (dados.get("texto_endereco_completo") or "").strip()
    if not endereco_base:
        partes = []
        endereco = (dados.get("endereco") or "").strip()
        cidade = (dados.get("cidade") or "").strip()
        uf = (dados.get("uf") or "").strip()
        cep = (dados.get("cep") or "").strip()
        email = (dados.get("email") or "").strip()
        telefone = (dados.get("telefone") or "").strip()
        if endereco:
            partes.append(endereco)
        if cidade or uf:
            partes.append((cidade + (" - " + uf if uf else "")).strip())
        if cep:
            partes.append("CEP " + cep)
        if documento:
            partes.append(f"{rotulo_doc}: {documento}")
        if email:
            partes.append(email)
        if telefone:
            partes.append("Telefone: " + telefone)
        endereco_base = " · ".join(partes)
    dados["texto_produzido_por"] = produzido
    dados["texto_endereco_completo"] = endereco_base
    dados["fabricante"] = nome
    return dados


@app.route("/api/perfil_empresa", methods=["GET"])
def api_obter_perfil_empresa():
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM perfil_empresa WHERE id = 1")
    perfil = montar_perfil_empresa_dict(cursor.fetchone())
    conn.close()
    return jsonify({"ok": True, "perfil": perfil})


@app.route("/api/perfil_empresa", methods=["POST"])
def api_salvar_perfil_empresa():
    dados = request.get_json(silent=True) or {}
    tipo_pessoa = str(dados.get("tipo_pessoa", "juridica")).strip() or "juridica"
    razao_social = str(dados.get("razao_social", "")).strip()
    nome_fantasia = str(dados.get("nome_fantasia", "")).strip()
    documento = str(dados.get("documento", "")).strip()
    inscricao_estadual = str(dados.get("inscricao_estadual", "")).strip()
    responsavel = str(dados.get("responsavel", "Indústria Brasileira")).strip()
    endereco = str(dados.get("endereco", "")).strip()
    cidade = str(dados.get("cidade", "")).strip()
    uf = str(dados.get("uf", "")).strip().upper()
    cep = str(dados.get("cep", "")).strip()
    telefone = str(dados.get("telefone", "")).strip()
    email = str(dados.get("email", "")).strip()
    site = str(dados.get("site", "")).strip()
    nome_rotulo = nome_fantasia or razao_social or "Wevvo"
    texto_produzido_por = str(dados.get("texto_produzido_por", "")).strip() or f"PRODUZIDO POR: {nome_rotulo.upper()}"

    rotulo_doc = "CPF" if tipo_pessoa == "fisica" else "CNPJ"
    partes = []
    if endereco:
        partes.append(endereco)
    if cidade or uf:
        partes.append((cidade + (" - " + uf if uf else "")).strip())
    if cep:
        partes.append("CEP " + cep)
    if documento:
        partes.append(f"{rotulo_doc}: {documento}")
    if email:
        partes.append(email)
    if telefone:
        partes.append("Telefone: " + telefone)
    texto_endereco_completo = str(dados.get("texto_endereco_completo", "")).strip() or " · ".join(partes)

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO perfil_empresa (
            id, tipo_pessoa, razao_social, nome_fantasia, documento, inscricao_estadual,
            responsavel, endereco, cidade, uf, cep, telefone, email, site,
            texto_produzido_por, texto_endereco_completo, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            tipo_pessoa=excluded.tipo_pessoa,
            razao_social=excluded.razao_social,
            nome_fantasia=excluded.nome_fantasia,
            documento=excluded.documento,
            inscricao_estadual=excluded.inscricao_estadual,
            responsavel=excluded.responsavel,
            endereco=excluded.endereco,
            cidade=excluded.cidade,
            uf=excluded.uf,
            cep=excluded.cep,
            telefone=excluded.telefone,
            email=excluded.email,
            site=excluded.site,
            texto_produzido_por=excluded.texto_produzido_por,
            texto_endereco_completo=excluded.texto_endereco_completo,
            updated_at=CURRENT_TIMESTAMP
    """, (
        tipo_pessoa, razao_social, nome_fantasia, documento, inscricao_estadual,
        responsavel, endereco, cidade, uf, cep, telefone, email, site,
        texto_produzido_por, texto_endereco_completo
    ))
    conn.commit()
    cursor.execute("SELECT * FROM perfil_empresa WHERE id = 1")
    perfil = montar_perfil_empresa_dict(cursor.fetchone())
    conn.close()
    return jsonify({"ok": True, "perfil": perfil})


@app.route("/")
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


@app.route("/receitas_salvas")
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
            "categoria_nome": item["categoria_nome"] or "Sem categoria"
        }
        for item in receitas
    ])


def montar_detalhamento_combo_cozinha(cursor, receita_id, itens_combo):
    """Abre os pratos de um combo e calcula ingredientes internos para impressão da cozinha."""
    detalhamento = []

    for item_combo in itens_combo:
        nome_prato = item_combo["ingrediente_nome"]
        quantidade_prato = float(item_combo["gramas"] or 0)

        cursor.execute("""
            SELECT id, nome, rendimento, custo_total
            FROM receitas
            WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))
              AND id <> ?
            ORDER BY id DESC
            LIMIT 1
        """, (nome_prato, int(receita_id)))
        prato = cursor.fetchone()

        if not prato:
            continue

        cursor.execute("""
            SELECT ri.ingrediente_id, ri.ingrediente_nome, ri.gramas,
                   i.preco_kg, i.unidade
            FROM receita_itens ri
            LEFT JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receita_id = ?
            ORDER BY ri.id ASC
        """, (int(prato["id"]),))
        itens_prato = cursor.fetchall()

        total_gramas_prato = sum(float(subitem["gramas"] or 0) for subitem in itens_prato)
        if total_gramas_prato <= 0:
            total_gramas_prato = quantidade_prato or 1

        fator = quantidade_prato / total_gramas_prato if total_gramas_prato else 1
        ingredientes = []
        custo_prato = 0.0

        for subitem in itens_prato:
            gramas_calculadas = round(float(subitem["gramas"] or 0) * fator, 2)
            custo = round(calcular_custo_por_quantidade(
                subitem["preco_kg"] or 0,
                subitem["unidade"] or "KG",
                gramas_calculadas
            ), 2)
            custo_prato += custo
            ingredientes.append({
                "id": subitem["ingrediente_id"],
                "nome": subitem["ingrediente_nome"],
                "gramas": gramas_calculadas,
                "custo": custo
            })

        detalhamento.append({
            "receita_id": prato["id"],
            "prato": prato["nome"],
            "quantidade_combo": round(quantidade_prato, 2),
            "total_gramas_prato_base": round(total_gramas_prato, 2),
            "fator": round(fator, 6),
            "custo": round(custo_prato, 2),
            "ingredientes": ingredientes
        })

    return detalhamento


def sincronizar_ingredientes_tecnicos_usados_na_receita(cursor, receita_id):
    cursor.execute("""
        SELECT DISTINCT r.id, r.nome
        FROM receita_itens ri
        JOIN ingredientes i ON i.id = ri.ingrediente_id
        JOIN receitas r ON LOWER(TRIM(r.nome)) = LOWER(TRIM(i.nome))
        WHERE ri.receita_id = ?
          AND r.id <> ?
          AND COALESCE(i.fonte_nutricional, '') = 'RECEITA_TECNICA'
    """, (int(receita_id), int(receita_id)))

    receitas_base = cursor.fetchall()
    for receita_base in receitas_base:
        recalcular_receitas_salvas(cursor, int(receita_base["id"]))
        atualizar_ingrediente_tecnico_da_receita(cursor, int(receita_base["id"]), receita_base["nome"], {})


@app.route("/categorias_admin")
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


@app.route("/categorias_select")
def categorias_select():
    conn = conectar_banco()
    cursor = conn.cursor()
    sincronizar_categorias_existentes(cursor)
    conn.commit()
    cursor.execute("SELECT id, nome FROM categorias ORDER BY nome ASC")
    categorias = cursor.fetchall()
    conn.close()
    return jsonify([{"id": item["id"], "nome": item["nome"]} for item in categorias])


@app.route("/salvar_categoria", methods=["POST"])
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


@app.route("/excluir_categoria/<int:categoria_id>", methods=["DELETE"])
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


@app.route("/vincular_receita_categoria", methods=["POST"])
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


@app.route("/imprimir_categoria/<int:categoria_id>")
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


@app.route("/buscar")
def buscar():
    termo = request.args.get("q", "").strip()

    if len(termo) < 1:
        return jsonify([])

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id,
            nome,
            preco_kg,
            unidade,
            calorias_100g,
            carboidratos_100g,
            proteinas_100g,
            gorduras_100g,
            sodio_100g,
            fonte_nutricional,
            taco_match_nome,
            taco_match_similaridade
        FROM ingredientes
        WHERE COALESCE(fonte_nutricional, '') <> 'RECEITA_TECNICA'
        ORDER BY nome ASC
    """)

    termo_normalizado = normalizar_busca(termo)

    def prioridade_busca(item):
        nome_normalizado = normalizar_busca(item["nome"])
        palavras = nome_normalizado.split()
        if nome_normalizado.startswith(termo_normalizado):
            prioridade = 0
        elif any(palavra.startswith(termo_normalizado) for palavra in palavras):
            prioridade = 1
        else:
            prioridade = 2
        return (prioridade, nome_normalizado)

    linhas_filtradas = [
        item for item in cursor.fetchall()
        if termo_normalizado in normalizar_busca(item["nome"])
        and not parece_prato_montado(item["nome"])
    ]
    linhas = sorted(linhas_filtradas, key=prioridade_busca)[:50]
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "nome": item["nome"],
            "preco_kg": item["preco_kg"],
            "preco": item["preco_kg"],
            "unidade": item["unidade"],
            "calorias_100g": item["calorias_100g"],
            "carboidratos_100g": item["carboidratos_100g"],
            "proteinas_100g": item["proteinas_100g"],
            "gorduras_100g": item["gorduras_100g"],
            "sodio_100g": item["sodio_100g"],
            "fonte_nutricional": item["fonte_nutricional"],
            "taco_match_nome": item["taco_match_nome"],
            "taco_match_similaridade": item["taco_match_similaridade"]
        }
        for item in linhas
    ])


@app.route("/sugerir_nutriente_ingrediente")
def sugerir_nutriente_ingrediente():
    nome = request.args.get("q", "").strip()
    if not nome:
        return jsonify({"status": "erro", "mensagem": "Informe o nome do ingrediente."}), 400

    campos_nutri = [
        "calorias_100g",
        "carboidratos_100g",
        "acucares_totais_100g",
        "acucares_adicionados_100g",
        "proteinas_100g",
        "gorduras_100g",
        "gorduras_saturadas_100g",
        "gorduras_trans_100g",
        "fibra_alimentar_100g",
        "sodio_100g"
    ]

    complemento = nutrientes_complementares(nome)
    if complemento:
        return jsonify({
            "status": "sucesso",
            "fonte": complemento["base"],
            "similaridade": 100,
            "nutrientes": dict(zip(campos_nutri, complemento["valores"]))
        })

    conn = conectar_banco()
    cursor = conn.cursor()
    condicao_com_nutri = " OR ".join([f"COALESCE({campo}, 0) > 0" for campo in campos_nutri])
    cursor.execute(f"""
        SELECT id, nome, fonte_nutricional, taco_match_nome, {", ".join(campos_nutri)}
        FROM ingredientes
        WHERE ({condicao_com_nutri})
        ORDER BY
            CASE
                WHEN COALESCE(fonte_nutricional, '') LIKE '%TACO%' THEN 0
                WHEN COALESCE(taco_match_nome, '') <> '' THEN 1
                WHEN COALESCE(fonte_nutricional, '') = 'RECEITA_TECNICA' THEN 2
                ELSE 3
            END,
            nome ASC
    """)
    bases = [dict(linha) for linha in cursor.fetchall()]
    conn.close()

    melhor = None
    melhor_score = 0.0
    for base in bases:
        score = similaridade_nutricional(nome, base["nome"])
        if score > melhor_score:
            melhor_score = score
            melhor = base

    if not melhor or melhor_score < 0.42:
        return jsonify({
            "status": "erro",
            "mensagem": "Não encontrei uma base nutricional parecida com segurança."
        }), 404

    return jsonify({
        "status": "sucesso",
        "fonte": melhor["taco_match_nome"] or melhor["nome"],
        "similaridade": round(melhor_score * 100, 1),
        "nutrientes": {campo: melhor[campo] for campo in campos_nutri}
    })


@app.route("/ingredientes_precos")
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


@app.route("/ingredientes_admin")
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


@app.route("/ingrediente_admin/<int:ingrediente_id>")
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


@app.route("/salvar_ingrediente_admin", methods=["POST"])
def salvar_ingrediente_admin():
    dados = request.json or {}

    ingrediente_id = dados.get("id")
    nome = str(dados.get("nome", "")).strip()
    preco_compra = normalizar_float(dados.get("preco_compra", 0))
    quantidade_compra = normalizar_float(dados.get("quantidade_compra", 1), 1)
    unidade_compra = str(dados.get("unidade_compra", "KG")).upper().strip()

    if unidade_compra not in UNIDADES_VALIDAS:
        return jsonify({"status": "erro", "mensagem": "Unidade invalida."}), 400

    if not nome:
        return jsonify({"status": "erro", "mensagem": "Nome do ingrediente e obrigatorio."}), 400

    if preco_compra < 0:
        return jsonify({"status": "erro", "mensagem": "Preco nao pode ser negativo."}), 400

    if quantidade_compra <= 0:
        return jsonify({"status": "erro", "mensagem": "Quantidade deve ser maior que zero."}), 400

    preco_unitario, unidade = calcular_preco_unitario_compra(
        preco_compra,
        quantidade_compra,
        unidade_compra
    )

    conn = conectar_banco()
    cursor = conn.cursor()

    ingrediente_atual = None
    if ingrediente_id:
        cursor.execute("SELECT * FROM ingredientes WHERE id = ?", (int(ingrediente_id),))
        ingrediente_atual = cursor.fetchone()
        if not ingrediente_atual:
            conn.close()
            return jsonify({"status": "erro", "mensagem": "Ingrediente nao encontrado."}), 404

    def valor_nutricional(campo):
        if campo in dados:
            return normalizar_float(dados.get(campo, 0))
        if ingrediente_atual:
            return normalizar_float(ingrediente_atual[campo], 0)
        return 0

    campos_nutri = {
        "calorias_100g": valor_nutricional("calorias_100g"),
        "carboidratos_100g": valor_nutricional("carboidratos_100g"),
        "acucares_totais_100g": valor_nutricional("acucares_totais_100g"),
        "acucares_adicionados_100g": valor_nutricional("acucares_adicionados_100g"),
        "proteinas_100g": valor_nutricional("proteinas_100g"),
        "gorduras_100g": valor_nutricional("gorduras_100g"),
        "gorduras_saturadas_100g": valor_nutricional("gorduras_saturadas_100g"),
        "gorduras_trans_100g": valor_nutricional("gorduras_trans_100g"),
        "fibra_alimentar_100g": valor_nutricional("fibra_alimentar_100g"),
        "sodio_100g": valor_nutricional("sodio_100g")
    }

    if ingrediente_id:
        ingrediente_id = int(ingrediente_id)
        cursor.execute("""
            UPDATE ingredientes
            SET nome = ?,
                preco_kg = ?,
                unidade = ?,
                preco_compra = ?,
                quantidade_compra = ?,
                unidade_compra = ?,
                calorias_100g = ?,
                carboidratos_100g = ?,
                acucares_totais_100g = ?,
                acucares_adicionados_100g = ?,
                proteinas_100g = ?,
                gorduras_100g = ?,
                gorduras_saturadas_100g = ?,
                gorduras_trans_100g = ?,
                fibra_alimentar_100g = ?,
                sodio_100g = ?,
                ultimo_reajuste_tipo = 'COMPRA',
                ultimo_reajuste_valor = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            nome,
            preco_unitario,
            unidade,
            preco_compra,
            quantidade_compra,
            unidade_compra,
            campos_nutri["calorias_100g"],
            campos_nutri["carboidratos_100g"],
            campos_nutri["acucares_totais_100g"],
            campos_nutri["acucares_adicionados_100g"],
            campos_nutri["proteinas_100g"],
            campos_nutri["gorduras_100g"],
            campos_nutri["gorduras_saturadas_100g"],
            campos_nutri["gorduras_trans_100g"],
            campos_nutri["fibra_alimentar_100g"],
            campos_nutri["sodio_100g"],
            preco_compra,
            agora_brasilia(),
            ingrediente_id
        ))
    else:
        cursor.execute("""
            INSERT INTO ingredientes (
                codigo_taco,
                nome,
                preco_kg,
                unidade,
                preco_compra,
                quantidade_compra,
                unidade_compra,
                calorias_100g,
                carboidratos_100g,
                acucares_totais_100g,
                acucares_adicionados_100g,
                proteinas_100g,
                gorduras_100g,
                gorduras_saturadas_100g,
                gorduras_trans_100g,
                fibra_alimentar_100g,
                sodio_100g,
                fonte_nutricional,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            None,
            nome,
            preco_unitario,
            unidade,
            preco_compra,
            quantidade_compra,
            unidade_compra,
            campos_nutri["calorias_100g"],
            campos_nutri["carboidratos_100g"],
            campos_nutri["acucares_totais_100g"],
            campos_nutri["acucares_adicionados_100g"],
            campos_nutri["proteinas_100g"],
            campos_nutri["gorduras_100g"],
            campos_nutri["gorduras_saturadas_100g"],
            campos_nutri["gorduras_trans_100g"],
            campos_nutri["fibra_alimentar_100g"],
            campos_nutri["sodio_100g"],
            "MANUAL",
            agora_brasilia()
        ))
        ingrediente_id = cursor.lastrowid

    receitas_recalculadas = recalcular_receitas_salvas(cursor, ingrediente_id)

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "id": ingrediente_id,
        "preco_unitario": preco_unitario,
        "unidade": unidade,
        "receitas_recalculadas": receitas_recalculadas
    })


@app.route("/excluir_ingrediente/<int:ingrediente_id>", methods=["DELETE"])
def excluir_ingrediente_admin(ingrediente_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(DISTINCT receita_id) AS total
        FROM receita_itens
        WHERE ingrediente_id = ?
    """, (ingrediente_id,))
    receitas_usando = int(cursor.fetchone()["total"] or 0)

    if receitas_usando > 0:
        conn.close()
        return jsonify({
            "status": "erro",
            "mensagem": f"Este ingrediente esta em {receitas_usando} receita(s). Remova ou troque nas receitas antes de excluir."
        }), 400

    cursor.execute("DELETE FROM ingredientes WHERE id = ?", (ingrediente_id,))
    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso"})


@app.route("/atualizar_preco_ingrediente", methods=["POST"])
def atualizar_preco_ingrediente():
    dados = request.json or {}

    ingrediente_id = dados.get("id")
    preco = float(dados.get("preco", 0))
    unidade = str(dados.get("unidade", "KG")).upper().strip()

    if unidade not in UNIDADES_VALIDAS:
        return jsonify({"status": "erro", "mensagem": "Unidade inválida"}), 400

    if not ingrediente_id:
        return jsonify({"status": "erro", "mensagem": "Ingrediente inválido"}), 400

    if preco < 0:
        return jsonify({"status": "erro", "mensagem": "Preço não pode ser negativo"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE ingredientes
        SET preco_kg = ?,
            unidade = ?,
            ultimo_reajuste_tipo = 'VALOR_UNITARIO',
            ultimo_reajuste_valor = ?,
            updated_at = ?
        WHERE id = ?
    """, (preco, unidade, preco, agora_brasilia(), int(ingrediente_id)))

    receitas_recalculadas = recalcular_receitas_salvas(cursor, int(ingrediente_id))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "receitas_recalculadas": receitas_recalculadas})


@app.route("/reajustar_preco_ingrediente", methods=["POST"])
def reajustar_preco_ingrediente():
    dados = request.json or {}

    ingrediente_id = dados.get("id")
    tipo = dados.get("tipo")
    valor = float(dados.get("valor", 0))

    if not ingrediente_id:
        return jsonify({"status": "erro", "mensagem": "Ingrediente inválido"}), 400

    if tipo not in ["PERCENTUAL", "VALOR"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de reajuste inválido"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    if tipo == "PERCENTUAL":
        cursor.execute("""
            UPDATE ingredientes
            SET preco_kg = ROUND(preco_kg * (1 + (? / 100.0)), 4),
                ultimo_reajuste_tipo = 'PERCENTUAL_UNITARIO',
                ultimo_reajuste_valor = ?,
                updated_at = ?
            WHERE id = ?
        """, (valor, valor, agora_brasilia(), int(ingrediente_id)))
    else:
        cursor.execute("""
            UPDATE ingredientes
            SET preco_kg = CASE
                    WHEN preco_kg + ? < 0 THEN 0
                    ELSE ROUND(preco_kg + ?, 4)
                END,
                ultimo_reajuste_tipo = 'VALOR_UNITARIO',
                ultimo_reajuste_valor = ?,
                updated_at = ?
            WHERE id = ?
        """, (valor, valor, valor, agora_brasilia(), int(ingrediente_id)))

    receitas_recalculadas = recalcular_receitas_salvas(cursor, int(ingrediente_id))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "receitas_recalculadas": receitas_recalculadas})


@app.route("/reajustar_preco_receita", methods=["POST"])
def reajustar_preco_receita():
    dados = request.json or {}
    receita_id = dados.get("id")
    tipo = dados.get("tipo")
    valor = float(dados.get("valor", 0))

    if not receita_id:
        return jsonify({"status": "erro", "mensagem": "Receita inválida"}), 400

    if tipo not in ["PERCENTUAL", "VALOR"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de reajuste inválido"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, custo_total, rendimento, preco_venda
        FROM receitas
        WHERE id = ?
    """, (int(receita_id),))
    receita = cursor.fetchone()

    if not receita:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Receita não encontrada"}), 404

    preco_atual = float(receita["preco_venda"] or 0)
    custo_total = float(receita["custo_total"] or 0)
    rendimento = max(float(receita["rendimento"] or 1), 1)

    if tipo == "PERCENTUAL":
        novo_preco = preco_atual * (1 + (valor / 100.0))
    else:
        novo_preco = preco_atual + valor

    novo_preco = round(max(novo_preco, 0), 2)
    nova_margem = round((novo_preco / custo_total) - 1, 4) if custo_total > 0 else 0

    cursor.execute("""
        UPDATE receitas
        SET preco_venda = ?,
            preco_venda_porcao = ?,
            margem_lucro = ?,
            updated_at = ?
        WHERE id = ?
    """, (novo_preco, round(novo_preco / rendimento, 2), nova_margem, agora_brasilia(), int(receita_id)))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "preco_venda": novo_preco})


@app.route("/reajustar_preco_combo", methods=["POST"])
def reajustar_preco_combo():
    dados = request.json or {}
    combo_id = dados.get("id")
    tipo = dados.get("tipo")
    valor = float(dados.get("valor", 0))

    if not combo_id:
        return jsonify({"status": "erro", "mensagem": "Combo inválido"}), 400

    if tipo not in ["PERCENTUAL", "VALOR"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de reajuste inválido"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT id, preco_venda FROM produtos_finais WHERE id = ?", (int(combo_id),))
    combo = cursor.fetchone()

    if not combo:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Combo não encontrado"}), 404

    preco_atual = float(combo["preco_venda"] or 0)
    if tipo == "PERCENTUAL":
        novo_preco = preco_atual * (1 + (valor / 100.0))
    else:
        novo_preco = preco_atual + valor

    novo_preco = round(max(novo_preco, 0), 2)

    cursor.execute("""
        UPDATE produtos_finais
        SET preco_venda = ?,
            updated_at = ?
        WHERE id = ?
    """, (novo_preco, agora_brasilia(), int(combo_id)))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "preco_venda": novo_preco})


@app.route("/reajustar_precos_todos", methods=["POST"])
def reajustar_precos_todos():
    dados = request.json or {}

    tipo = dados.get("tipo")
    valor = float(dados.get("valor", 0))

    if tipo not in ["PERCENTUAL", "VALOR"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de reajuste inválido"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    if tipo == "PERCENTUAL":
        cursor.execute("""
            UPDATE ingredientes
            SET preco_kg = ROUND(preco_kg * (1 + (? / 100.0)), 4),
                ultimo_reajuste_tipo = 'PERCENTUAL_GERAL',
                ultimo_reajuste_valor = ?,
                updated_at = ?
        """, (valor, valor, agora_brasilia()))
    else:
        cursor.execute("""
            UPDATE ingredientes
            SET preco_kg = CASE
                    WHEN preco_kg + ? < 0 THEN 0
                    ELSE ROUND(preco_kg + ?, 4)
                END,
                ultimo_reajuste_tipo = 'VALOR_GERAL',
                ultimo_reajuste_valor = ?,
                updated_at = ?
        """, (valor, valor, valor, agora_brasilia()))

    receitas_recalculadas = recalcular_receitas_salvas(cursor)

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "receitas_recalculadas": receitas_recalculadas})


@app.route("/recalcular_receitas", methods=["POST"])
def recalcular_receitas():
    conn = conectar_banco()
    cursor = conn.cursor()

    receitas_recalculadas = recalcular_receitas_salvas(cursor)

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "receitas_recalculadas": receitas_recalculadas})


@app.route("/cadastrar_ingrediente", methods=["POST"])
def cadastrar_ingrediente():
    dados = request.json or {}

    nome = dados.get("nome", "").strip()
    unidade = str(dados.get("unidade", "KG")).upper().strip()

    if unidade not in UNIDADES_VALIDAS:
        return jsonify({"status": "erro", "mensagem": "Unidade inválida"}), 400

    if not nome:
        return jsonify({"status": "erro", "mensagem": "Nome do ingrediente é obrigatório"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO ingredientes (
            codigo_taco,
            nome,
            preco_kg,
            unidade,
            preco_compra,
            quantidade_compra,
            unidade_compra,
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
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        None,
        nome,
        float(dados.get("preco_kg", 0)),
        unidade,
        float(dados.get("preco_kg", 0)),
        1,
        unidade,
        normalizar_float(dados.get("calorias", 0)),
        normalizar_float(dados.get("carbos", 0)),
        normalizar_float(dados.get("acucares_totais", 0)),
        normalizar_float(dados.get("acucares_adicionados", 0)),
        normalizar_float(dados.get("proteinas", 0)),
        normalizar_float(dados.get("gorduras", 0)),
        normalizar_float(dados.get("gorduras_saturadas", 0)),
        normalizar_float(dados.get("gorduras_trans", 0)),
        normalizar_float(dados.get("fibra", 0)),
        normalizar_float(dados.get("sodio", 0))
    ))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso"})


@app.route("/calcular", methods=["POST"])
def calcular():
    dados = request.json or {}

    itens_receita = dados.get("itens", [])
    rendimento_porcoes = float(dados.get("rendimento_porcoes", 1))
    margem_lucro = float(dados.get("margem_lucro", 100)) / 100

    if rendimento_porcoes <= 0:
        rendimento_porcoes = 1

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
        "sodio": 0
    }

    conn = conectar_banco()
    cursor = conn.cursor()

    for item in itens_receita:
        ingrediente_id = item.get("id")
        gramas = float(item.get("gramas", 0))

        if not ingrediente_id or gramas <= 0:
            continue

        total_gramas_receita += gramas

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
        """, (ingrediente_id,))

        ingrediente = cursor.fetchone()

        if ingrediente:
            custo_total += calcular_custo_por_quantidade(
                ingrediente["preco_kg"],
                ingrediente["unidade"],
                gramas
            )

            proporcao = gramas / 100

            nutrientes["calorias"] += ingrediente["calorias_100g"] * proporcao
            nutrientes["carbos"] += float(ingrediente["carboidratos_100g"] or 0) * proporcao
            nutrientes["acucares_totais"] += float(ingrediente["acucares_totais_100g"] or 0) * proporcao
            nutrientes["acucares_adicionados"] += float(ingrediente["acucares_adicionados_100g"] or 0) * proporcao
            nutrientes["proteinas"] += float(ingrediente["proteinas_100g"] or 0) * proporcao
            nutrientes["gorduras"] += float(ingrediente["gorduras_100g"] or 0) * proporcao
            nutrientes["gorduras_saturadas"] += float(ingrediente["gorduras_saturadas_100g"] or 0) * proporcao
            nutrientes["gorduras_trans"] += float(ingrediente["gorduras_trans_100g"] or 0) * proporcao
            nutrientes["fibra"] += float(ingrediente["fibra_alimentar_100g"] or 0) * proporcao
            nutrientes["sodio"] += float(ingrediente["sodio_100g"] or 0) * proporcao

    conn.close()

    preco_venda_total = custo_total * (1 + margem_lucro)

    if total_gramas_receita <= 0:
        total_gramas_receita = 1

    nutrientes_100g = {
        "calorias": round((nutrientes["calorias"] / total_gramas_receita) * 100, 1),
        "carbos": round((nutrientes["carbos"] / total_gramas_receita) * 100, 1),
        "acucares_totais": round((nutrientes["acucares_totais"] / total_gramas_receita) * 100, 1),
        "acucares_adicionados": round((nutrientes["acucares_adicionados"] / total_gramas_receita) * 100, 1),
        "proteinas": round((nutrientes["proteinas"] / total_gramas_receita) * 100, 1),
        "gorduras": round((nutrientes["gorduras"] / total_gramas_receita) * 100, 1),
        "gorduras_saturadas": round((nutrientes["gorduras_saturadas"] / total_gramas_receita) * 100, 1),
        "gorduras_trans": round((nutrientes["gorduras_trans"] / total_gramas_receita) * 100, 1),
        "fibra": round((nutrientes["fibra"] / total_gramas_receita) * 100, 1),
        "sodio": round((nutrientes["sodio"] / total_gramas_receita) * 100, 1)
    }

    return jsonify({
        "custo_total_receita": round(custo_total, 2),
        "custo_por_porcao": round(custo_total / rendimento_porcoes, 2),
        "preco_venda_total": round(preco_venda_total, 2),
        "preco_venda_porcao": round(preco_venda_total / rendimento_porcoes, 2),
        "total_gramas_receita": round(total_gramas_receita, 1),
        "porcao_nutrientes": nutrientes_100g,
        "rotulo_100g": nutrientes_100g
    })


@app.route("/salvar_receita", methods=["POST"])
def salvar_receita():
    dados = request.json or {}

    receita_id = dados.get("id")
    nome = dados.get("nome", "").strip()
    itens = dados.get("itens", [])
    categoria_id = dados.get("categoria_id")
    categoria_nome = ""
    rotulo = dados.get("rotulo") or {}

    if not nome:
        return jsonify({"status": "erro", "mensagem": "Nome da receita é obrigatório"}), 400

    if not itens:
        return jsonify({"status": "erro", "mensagem": "Adicione pelo menos um ingrediente"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    if categoria_id:
        cursor.execute("SELECT id, nome FROM categorias WHERE id = ?", (int(categoria_id),))
        categoria = cursor.fetchone()
        if categoria:
            categoria_id = int(categoria["id"])
            categoria_nome = categoria["nome"]
        else:
            categoria_id = None

    if receita_id:
        cursor.execute("""
            UPDATE receitas
            SET
                nome = ?,
                categoria_id = ?,
                categoria_nome = ?,
                categoria_produto = ?,
                rendimento = ?,
                margem_lucro = ?,
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
                rotulo_peso_liquido = ?,
                rotulo_porcao = ?,
                rotulo_fabricante = ?,
                rotulo_data_fabricacao = ?,
                rotulo_data_validade = ?,
                rotulo_lote = ?,
                rotulo_ingredientes = ?,
                rotulo_alergicos = ?,
                rotulo_gluten = ?,
                rotulo_lactose = ?,
                rotulo_conservacao = ?,
                rotulo_modo_preparo = ?,
                rotulo_empresa = ?,
                rotulo_responsavel = ?,
                rotulo_endereco = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            nome,
            categoria_id,
            categoria_nome,
            categoria_nome,
            float(dados.get("rendimento", 1)),
            float(dados.get("margem_lucro", 0)),
            float(dados.get("custo_total", 0)),
            float(dados.get("custo_porcao", 0)),
            float(dados.get("preco_venda", 0)),
            float(dados.get("preco_venda_porcao", 0)),
            float(dados.get("calorias", 0)),
            float(dados.get("carboidratos", 0)),
            normalizar_float(dados.get("proteinas", 0)),
            normalizar_float(dados.get("gorduras", 0)),
            normalizar_float(dados.get("acucares_totais", 0)),
            normalizar_float(dados.get("acucares_adicionados", 0)),
            normalizar_float(dados.get("gorduras_saturadas", 0)),
            normalizar_float(dados.get("gorduras_trans", 0)),
            normalizar_float(dados.get("fibra", 0)),
            normalizar_float(dados.get("sodio", 0)),
            str(rotulo.get("peso_liquido", "")).strip(),
            str(rotulo.get("porcao", "")).strip(),
            str(rotulo.get("fabricante", "")).strip(),
            str(rotulo.get("data_fabricacao", "")).strip(),
            str(rotulo.get("data_validade", "")).strip(),
            str(rotulo.get("lote", "")).strip(),
            str(rotulo.get("ingredientes", "")).strip(),
            str(rotulo.get("alergicos", "")).strip(),
            str(rotulo.get("gluten", "CONTÉM GLÚTEN")).strip(),
            str(rotulo.get("lactose", "CONTÉM LACTOSE")).strip(),
            str(rotulo.get("conservacao", "")).strip(),
            str(rotulo.get("modo_preparo", "")).strip(),
            str(rotulo.get("empresa", "")).strip(),
            str(rotulo.get("responsavel", "")).strip(),
            str(rotulo.get("endereco", "")).strip(),
            agora_brasilia(),
            int(receita_id)
        ))

        cursor.execute("DELETE FROM receita_itens WHERE receita_id = ?", (int(receita_id),))
        receita_id_final = int(receita_id)

    else:
        cursor.execute("""
            INSERT INTO receitas (
                nome,
                categoria_id,
                categoria_nome,
                categoria_produto,
                rendimento,
                margem_lucro,
                custo_total,
                custo_porcao,
                preco_venda,
                preco_venda_porcao,
                calorias,
                carboidratos,
                proteinas,
                gorduras,
                acucares_totais,
                acucares_adicionados,
                gorduras_saturadas,
                gorduras_trans,
                fibra,
                sodio,
                rotulo_peso_liquido,
                rotulo_porcao,
                rotulo_fabricante,
                rotulo_data_fabricacao,
                rotulo_data_validade,
                rotulo_lote,
                rotulo_ingredientes,
                rotulo_alergicos,
                rotulo_gluten,
                rotulo_lactose,
                rotulo_conservacao,
                rotulo_modo_preparo,
                rotulo_empresa,
                rotulo_responsavel,
                rotulo_endereco,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nome,
            categoria_id,
            categoria_nome,
            categoria_nome,
            float(dados.get("rendimento", 1)),
            float(dados.get("margem_lucro", 0)),
            float(dados.get("custo_total", 0)),
            float(dados.get("custo_porcao", 0)),
            float(dados.get("preco_venda", 0)),
            float(dados.get("preco_venda_porcao", 0)),
            normalizar_float(dados.get("calorias", 0)),
            normalizar_float(dados.get("carboidratos", 0)),
            normalizar_float(dados.get("proteinas", 0)),
            normalizar_float(dados.get("gorduras", 0)),
            normalizar_float(dados.get("acucares_totais", 0)),
            normalizar_float(dados.get("acucares_adicionados", 0)),
            normalizar_float(dados.get("gorduras_saturadas", 0)),
            normalizar_float(dados.get("gorduras_trans", 0)),
            normalizar_float(dados.get("fibra", 0)),
            normalizar_float(dados.get("sodio", 0)),
            str(rotulo.get("peso_liquido", "")).strip(),
            str(rotulo.get("porcao", "")).strip(),
            str(rotulo.get("fabricante", "")).strip(),
            str(rotulo.get("data_fabricacao", "")).strip(),
            str(rotulo.get("data_validade", "")).strip(),
            str(rotulo.get("lote", "")).strip(),
            str(rotulo.get("ingredientes", "")).strip(),
            str(rotulo.get("alergicos", "")).strip(),
            str(rotulo.get("gluten", "CONTÉM GLÚTEN")).strip(),
            str(rotulo.get("lactose", "CONTÉM LACTOSE")).strip(),
            str(rotulo.get("conservacao", "")).strip(),
            str(rotulo.get("modo_preparo", "")).strip(),
            str(rotulo.get("empresa", "")).strip(),
            str(rotulo.get("responsavel", "")).strip(),
            str(rotulo.get("endereco", "")).strip(),
            agora_brasilia()
        ))

        receita_id_final = cursor.lastrowid

    for item in itens:
        ingrediente_id = int(item.get("id"))
        ingrediente_nome = item.get("nome", "")
        gramas = float(item.get("gramas", 0))

        if ingrediente_id and ingrediente_nome and gramas > 0:
            cursor.execute("""
                INSERT INTO receita_itens (
                    receita_id,
                    ingrediente_id,
                    ingrediente_nome,
                    gramas
                )
                VALUES (?, ?, ?, ?)
            """, (
                receita_id_final,
                ingrediente_id,
                ingrediente_nome,
                gramas
            ))

    recalcular_receitas_salvas(cursor, receita_id_final)
    cursor.execute("SELECT nome FROM receitas WHERE id = ?", (int(receita_id_final),))
    receita_salva = cursor.fetchone()
    if receita_salva:
        atualizar_ingrediente_tecnico_da_receita(cursor, int(receita_id_final), receita_salva["nome"], {})

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "id": receita_id_final
    })


@app.route("/receita/<int:receita_id>")
def abrir_receita(receita_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    # Ao abrir uma ficha salva, recalcula e grava os valores atuais
    # antes de enviar para a tela. Assim não depende do botão Calcular.
    sincronizar_ingredientes_tecnicos_usados_na_receita(cursor, receita_id)
    recalcular_receitas_salvas(cursor, receita_id)
    conn.commit()

    cursor.execute("SELECT * FROM receitas WHERE id = ?", (receita_id,))
    receita = cursor.fetchone()

    if not receita:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Receita não encontrada"}), 404

    cursor.execute("""
        SELECT ri.ingrediente_id, ri.ingrediente_nome, ri.gramas,
               i.preco_kg, i.unidade
        FROM receita_itens ri
        LEFT JOIN ingredientes i ON i.id = ri.ingrediente_id
        WHERE ri.receita_id = ?
        ORDER BY ri.id ASC
    """, (receita_id,))

    itens = cursor.fetchall()
    combo_detalhado = montar_detalhamento_combo_cozinha(cursor, receita_id, itens)

    cursor.execute("""
        SELECT id, nome
        FROM ingredientes
        WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))
          AND COALESCE(fonte_nutricional, '') = 'RECEITA_TECNICA'
        ORDER BY id DESC
        LIMIT 1
    """, (receita["nome"],))
    ingrediente_tecnico = cursor.fetchone()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "receita": {
            "id": receita["id"],
            "nome": receita["nome"],
            "categoria_id": receita["categoria_id"],
            "categoria_nome": receita["categoria_nome"] or receita["categoria_produto"] or "",
            "rendimento": receita["rendimento"],
            "margem_lucro": receita["margem_lucro"],
            "custo_total": receita["custo_total"],
            "custo_porcao": receita["custo_porcao"],
            "preco_venda": receita["preco_venda"],
            "preco_venda_porcao": receita["preco_venda_porcao"],
            "calorias": receita["calorias"],
            "carboidratos": receita["carboidratos"],
            "proteinas": receita["proteinas"],
            "gorduras": receita["gorduras"],
            "acucares_totais": receita["acucares_totais"],
            "acucares_adicionados": receita["acucares_adicionados"],
            "gorduras_saturadas": receita["gorduras_saturadas"],
            "gorduras_trans": receita["gorduras_trans"],
            "fibra": receita["fibra"],
            "sodio": receita["sodio"],
            "rotulo": {
                "peso_liquido": receita["rotulo_peso_liquido"] or "",
                "porcao": receita["rotulo_porcao"] or "",
                "fabricante": receita["rotulo_fabricante"] or "",
                "data_fabricacao": receita["rotulo_data_fabricacao"] or "",
                "data_validade": receita["rotulo_data_validade"] or "",
                "lote": receita["rotulo_lote"] or "",
                "ingredientes": receita["rotulo_ingredientes"] or "",
                "alergicos": receita["rotulo_alergicos"] or "",
                "gluten": receita["rotulo_gluten"] or "CONTÉM GLÚTEN",
                "lactose": receita["rotulo_lactose"] or "CONTÉM LACTOSE",
                "conservacao": receita["rotulo_conservacao"] or "",
                "modo_preparo": receita["rotulo_modo_preparo"] or "",
                "empresa": receita["rotulo_empresa"] or "",
                "responsavel": receita["rotulo_responsavel"] or "",
                "endereco": receita["rotulo_endereco"] or ""
            },
            "itens": [
                {
                    "id": item["ingrediente_id"],
                    "nome": item["ingrediente_nome"],
                    "gramas": item["gramas"],
                    "custo": round(calcular_custo_por_quantidade(item["preco_kg"] or 0, item["unidade"] or "KG", item["gramas"] or 0), 2)
                }
                for item in itens
            ],
            "ingrediente_tecnico": dict(ingrediente_tecnico) if ingrediente_tecnico else None,
            "combo_detalhado": combo_detalhado
        }
    })


@app.route("/excluir_receita/<int:receita_id>", methods=["DELETE"])
def excluir_receita(receita_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM receita_itens WHERE receita_id = ?", (receita_id,))
    cursor.execute("DELETE FROM receitas WHERE id = ?", (receita_id,))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso"})


@app.route("/estoque_ingredientes")
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


@app.route("/movimentar_estoque", methods=["POST"])
def movimentar_estoque():
    dados = request.json or {}

    ingrediente_id = dados.get("ingrediente_id")
    tipo = str(dados.get("tipo", "")).upper().strip()
    quantidade = float(dados.get("quantidade", 0))
    observacao = dados.get("observacao", "").strip()

    if not ingrediente_id:
        return jsonify({"status": "erro", "mensagem": "Ingrediente inválido"}), 400

    if tipo not in ["ENTRADA", "SAIDA", "AJUSTE"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de movimentação inválido"}), 400

    if quantidade < 0:
        return jsonify({"status": "erro", "mensagem": "Quantidade não pode ser negativa"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nome, unidade, estoque_atual
        FROM ingredientes
        WHERE id = ?
    """, (int(ingrediente_id),))

    ingrediente = cursor.fetchone()

    if not ingrediente:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Ingrediente não encontrado"}), 404

    estoque_atual = float(ingrediente["estoque_atual"])

    if tipo == "ENTRADA":
        novo_estoque = estoque_atual + quantidade
    elif tipo == "SAIDA":
        if quantidade > estoque_atual:
            conn.close()
            return jsonify({"status": "erro", "mensagem": "Saldo insuficiente em estoque"}), 400
        novo_estoque = estoque_atual - quantidade
    else:
        novo_estoque = quantidade

    cursor.execute("""
        UPDATE ingredientes
        SET estoque_atual = ?,
            updated_at = ?
        WHERE id = ?
    """, (novo_estoque, agora_brasilia(), int(ingrediente_id)))

    cursor.execute("""
        INSERT INTO movimentacoes_estoque (
            ingrediente_id,
            ingrediente_nome,
            tipo,
            quantidade,
            unidade,
            observacao,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        int(ingrediente_id),
        ingrediente["nome"],
        tipo,
        quantidade,
        ingrediente["unidade"],
        observacao,
        agora_brasilia()
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "estoque_atual": novo_estoque
    })


@app.route("/atualizar_estoque_minimo", methods=["POST"])
def atualizar_estoque_minimo():
    dados = request.json or {}

    ingrediente_id = dados.get("id")
    estoque_minimo = normalizar_float(dados.get("estoque_minimo", 0), 0)

    if not ingrediente_id:
        return jsonify({"status": "erro", "mensagem": "Ingrediente inválido"}), 400

    if estoque_minimo < 0:
        return jsonify({"status": "erro", "mensagem": "Estoque mínimo não pode ser negativo"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome, unidade
        FROM ingredientes
        WHERE id = ?
    """, (int(ingrediente_id),))

    ingrediente = cursor.fetchone()

    if not ingrediente:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Ingrediente não encontrado"}), 404

    cursor.execute("""
        UPDATE ingredientes
        SET estoque_minimo = ?,
            updated_at = ?
        WHERE id = ?
    """, (estoque_minimo, agora_brasilia(), int(ingrediente_id)))

    cursor.execute("""
        INSERT INTO movimentacoes_estoque (
            ingrediente_id,
            ingrediente_nome,
            tipo,
            quantidade,
            unidade,
            observacao,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        int(ingrediente_id),
        ingrediente["nome"],
        "MINIMO",
        estoque_minimo,
        ingrediente["unidade"],
        "Estoque mínimo definido",
        agora_brasilia()
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "estoque_minimo": estoque_minimo,
        "updated_at": agora_brasilia()
    })


@app.route("/movimentar_produto_final_estoque", methods=["POST"])
def movimentar_produto_final_estoque():
    dados = request.json or {}

    produto_id = dados.get("id")
    tipo = str(dados.get("tipo", "")).upper().strip()
    quantidade = normalizar_float(dados.get("quantidade", 0), 0)
    observacao = str(dados.get("observacao", "")).strip()

    if not produto_id:
        return jsonify({"status": "erro", "mensagem": "Combo inválido"}), 400

    if tipo not in ["ENTRADA", "SAIDA", "AJUSTE"]:
        return jsonify({"status": "erro", "mensagem": "Tipo de movimentação inválido"}), 400

    if quantidade < 0:
        return jsonify({"status": "erro", "mensagem": "Quantidade não pode ser negativa"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nome, estoque_atual
        FROM produtos_finais
        WHERE id = ?
    """, (int(produto_id),))
    produto = cursor.fetchone()

    if not produto:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Combo não encontrado"}), 404

    estoque_atual = float(produto["estoque_atual"] or 0)

    if tipo == "ENTRADA":
        novo_estoque = estoque_atual + quantidade
    elif tipo == "SAIDA":
        if quantidade > estoque_atual:
            conn.close()
            return jsonify({"status": "erro", "mensagem": "Saldo insuficiente em estoque"}), 400
        novo_estoque = estoque_atual - quantidade
    else:
        novo_estoque = quantidade

    cursor.execute("""
        UPDATE produtos_finais
        SET estoque_atual = ?,
            updated_at = ?
        WHERE id = ?
    """, (novo_estoque, agora_brasilia(), int(produto_id)))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "estoque_atual": novo_estoque,
        "observacao": observacao
    })


@app.route("/atualizar_produto_final_estoque_minimo", methods=["POST"])
def atualizar_produto_final_estoque_minimo():
    dados = request.json or {}

    produto_id = dados.get("id")
    estoque_minimo = normalizar_float(dados.get("estoque_minimo", 0), 0)

    if not produto_id:
        return jsonify({"status": "erro", "mensagem": "Combo inválido"}), 400

    if estoque_minimo < 0:
        return jsonify({"status": "erro", "mensagem": "Estoque mínimo não pode ser negativo"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM produtos_finais WHERE id = ?", (int(produto_id),))
    produto = cursor.fetchone()

    if not produto:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Combo não encontrado"}), 404

    cursor.execute("""
        UPDATE produtos_finais
        SET estoque_minimo = ?,
            updated_at = ?
        WHERE id = ?
    """, (estoque_minimo, agora_brasilia(), int(produto_id)))

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "estoque_minimo": estoque_minimo,
        "updated_at": agora_brasilia()
    })


@app.route("/movimentacoes_estoque")
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


@app.route("/receitas_producao")
def receitas_producao():
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
        SELECT id, nome, rendimento, custo_total, custo_porcao, preco_venda, preco_venda_porcao, categoria_nome
        FROM receitas
        {where}
        ORDER BY nome ASC
    """)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "nome": item["nome"],
            "rendimento": item["rendimento"],
            "custo_total": item["custo_total"],
            "custo_porcao": item["custo_porcao"],
            "preco_venda": item["preco_venda"],
            "preco_venda_porcao": item["preco_venda_porcao"],
            "categoria_nome": item["categoria_nome"] or "Sem categoria"
        }
        for item in linhas
    ])


def montar_previsao_producao(cursor, receita_id, quantidade_produzir):
    cursor.execute("""
        SELECT id, nome, rendimento, custo_total
        FROM receitas
        WHERE id = ?
    """, (int(receita_id),))
    receita = cursor.fetchone()

    if not receita:
        return None, "Receita não encontrada"

    rendimento = float(receita["rendimento"] or 1)
    if rendimento <= 0:
        rendimento = 1

    quantidade_produzir = float(quantidade_produzir or 0)
    if quantidade_produzir <= 0:
        return None, "Informe uma quantidade a produzir maior que zero"

    fator = quantidade_produzir / rendimento

    cursor.execute("""
        SELECT
            ri.ingrediente_id,
            ri.ingrediente_nome,
            ri.gramas,
            i.unidade,
            i.estoque_atual,
            i.preco_kg
        FROM receita_itens ri
        INNER JOIN ingredientes i ON i.id = ri.ingrediente_id
        WHERE ri.receita_id = ?
        ORDER BY ri.id ASC
    """, (int(receita_id),))

    itens_receita = cursor.fetchall()

    if not itens_receita:
        return None, "A receita não possui ingredientes cadastrados"

    itens = []
    bloqueado = False
    custo_total = 0

    for item in itens_receita:
        quantidade_base = float(item["gramas"] or 0) * fator
        quantidade_estoque = converter_quantidade_para_estoque(item["unidade"], quantidade_base)
        estoque_atual = float(item["estoque_atual"] or 0)
        suficiente = estoque_atual >= quantidade_estoque

        if not suficiente:
            bloqueado = True

        custo_total += calcular_custo_por_quantidade(item["preco_kg"], item["unidade"], quantidade_base)

        itens.append({
            "ingrediente_id": item["ingrediente_id"],
            "ingrediente_nome": item["ingrediente_nome"],
            "quantidade_base": round(quantidade_base, 3),
            "quantidade_baixar": round(quantidade_estoque, 3),
            "unidade": item["unidade"],
            "estoque_atual": round(estoque_atual, 3),
            "estoque_depois": round(estoque_atual - quantidade_estoque, 3),
            "suficiente": suficiente
        })

    return {
        "receita_id": receita["id"],
        "receita_nome": receita["nome"],
        "rendimento_base": rendimento,
        "quantidade": quantidade_produzir,
        "fator": round(fator, 4),
        "custo_total": round(custo_total, 2),
        "custo_unitario": round(custo_total / quantidade_produzir, 2),
        "bloqueado": bloqueado,
        "itens": itens
    }, None


@app.route("/simular_producao", methods=["POST"])
def simular_producao():
    dados = request.json or {}
    receita_id = dados.get("receita_id")
    quantidade = normalizar_float(dados.get("quantidade", 0), 0)

    if not receita_id:
        return jsonify({"status": "erro", "mensagem": "Selecione uma receita"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()

    previsao, erro = montar_previsao_producao(cursor, receita_id, quantidade)
    conn.close()

    if erro:
        return jsonify({"status": "erro", "mensagem": erro}), 400

    return jsonify({"status": "sucesso", "previsao": previsao})


@app.route("/registrar_producao", methods=["POST"])
def registrar_producao():
    dados = request.json or {}
    receita_id = dados.get("receita_id")
    quantidade = normalizar_float(dados.get("quantidade", 0), 0)
    observacao = dados.get("observacao", "").strip()
    produto_final_id = dados.get("produto_final_id") or None
    produto_final_nome = None
    op_codigo_qualidade = (dados.get('op_codigo') or '').strip()
    responsavel_qualidade = (dados.get('responsavel') or 'Sistema').strip()

    if produto_final_id in ["", 0, "0"]:
        produto_final_id = None

    if not receita_id:
        return jsonify({"status": "erro", "mensagem": "Selecione uma receita"}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    previsao, erro = montar_previsao_producao(cursor, receita_id, quantidade)

    if erro:
        conn.close()
        return jsonify({"status": "erro", "mensagem": erro}), 400

    if previsao["bloqueado"]:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Estoque insuficiente para registrar esta produção"}), 400

    produto_final = None
    validade_dias = 0
    if produto_final_id:
        cursor.execute("SELECT nome, validade_dias FROM produtos_finais WHERE id = ?", (int(produto_final_id),))
        produto_final = cursor.fetchone()
        if produto_final:
            produto_final_nome = produto_final["nome"]
            validade_dias = int(produto_final["validade_dias"] or 0)

    nome_para_lote = produto_final_nome or previsao["receita_nome"]
    lote = gerar_lote(cursor, nome_para_lote)
    fabricacao_data = data_brasilia_obj()
    validade_data = fabricacao_data + timedelta(days=validade_dias) if validade_dias > 0 else fabricacao_data
    data_fabricacao = data_brasil(fabricacao_data)
    data_validade = data_brasil(validade_data)

    cursor.execute("""
        INSERT INTO producoes (
            receita_id,
            receita_nome,
            quantidade,
            rendimento_base,
            custo_total,
            custo_unitario,
            observacao,
            produto_final_id,
            produto_final_nome,
            lote,
            data_fabricacao,
            data_validade,
            validade_dias,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(previsao["receita_id"]),
        previsao["receita_nome"],
        float(previsao["quantidade"]),
        float(previsao["rendimento_base"]),
        float(previsao["custo_total"]),
        float(previsao["custo_unitario"]),
        observacao,
        int(produto_final_id) if produto_final_id else None,
        produto_final_nome,
        lote,
        data_fabricacao,
        data_validade,
        validade_dias,
        agora_brasilia()
    ))

    producao_id = cursor.lastrowid

    for item in previsao["itens"]:
        cursor.execute("""
            UPDATE ingredientes
            SET estoque_atual = estoque_atual - ?,
                updated_at = ?
            WHERE id = ?
        """, (
            float(item["quantidade_baixar"]),
            agora_brasilia(),
            int(item["ingrediente_id"])
        ))

        cursor.execute("""
            INSERT INTO producao_itens (
                producao_id,
                ingrediente_id,
                ingrediente_nome,
                quantidade_baixada,
                unidade
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            producao_id,
            int(item["ingrediente_id"]),
            item["ingrediente_nome"],
            float(item["quantidade_baixar"]),
            item["unidade"]
        ))

        cursor.execute("""
            INSERT INTO movimentacoes_estoque (
                ingrediente_id,
                ingrediente_nome,
                tipo,
                quantidade,
                unidade,
                observacao,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            int(item["ingrediente_id"]),
            item["ingrediente_nome"],
            "PRODUCAO",
            float(item["quantidade_baixar"]),
            item["unidade"],
            f"Baixa automática da produção #{producao_id} - {previsao['receita_nome']}",
            agora_brasilia()
        ))

    if produto_final_id and produto_final_nome:
        cursor.execute("""
            UPDATE produtos_finais
            SET estoque_atual = estoque_atual + ?,
                updated_at = ?
            WHERE id = ?
        """, (
            float(previsao["quantidade"]),
            agora_brasilia(),
            int(produto_final_id)
        ))

        cursor.execute("""
            INSERT INTO estoque_produto_acabado (
                produto_final_id, produto_final_nome, receita_id, receita_nome, producao_id, lote,
                quantidade_produzida, saldo_atual, data_fabricacao, data_validade, observacao, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(produto_final_id), produto_final_nome, int(previsao["receita_id"]), previsao["receita_nome"],
            producao_id, lote, float(previsao["quantidade"]), float(previsao["quantidade"]),
            data_fabricacao, data_validade, observacao, agora_brasilia()
        ))

    # Qualidade nasce automaticamente da produção/OP, com checklist e processo padrão.
    registrar_qualidade_padrao_producao(
        cursor,
        producao_id,
        lote,
        previsao["receita_nome"],
        op_codigo_qualidade,
        responsavel_qualidade or "Sistema"
    )

    # Após produção, o motor central mantém CMV e auditoria coerentes.
    try:
        _erp_recalcular_cmv_todos(cursor)
        _erp_registrar_evento(
            cursor,
            "Produção",
            "registrar_producao_integrada",
            "producoes",
            producao_id,
            "sucesso",
            f"Produção integrada ao estoque, lote, qualidade e CMV. Lote {lote}.",
            ""
        )
    except Exception as exc:
        # Não bloqueia a produção se a auditoria/motor ainda estiverem em evolução.
        try:
            _erp_registrar_evento(cursor, "Produção", "registrar_producao_integrada", "producoes", producao_id, "parcial", "Produção registrada; recálculo central pendente.", str(exc))
        except Exception:
            pass

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "producao_id": producao_id,
        "lote": lote,
        "data_fabricacao": data_fabricacao,
        "data_validade": data_validade,
        "mensagem": f"Produção registrada. Lote {lote}. Validade {data_validade}." if produto_final_nome else f"Produção registrada. Lote {lote}."
    })


@app.route("/producoes")
def listar_producoes():
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, receita_nome, quantidade, rendimento_base, custo_total, custo_unitario, observacao, produto_final_nome, lote, data_fabricacao, data_validade, validade_dias, created_at
        FROM producoes
        ORDER BY id DESC
        LIMIT 80
    """)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([
        {
            "id": item["id"],
            "receita_nome": item["receita_nome"],
            "quantidade": item["quantidade"],
            "rendimento_base": item["rendimento_base"],
            "custo_total": item["custo_total"],
            "custo_unitario": item["custo_unitario"],
            "observacao": item["observacao"],
            "produto_final_nome": item["produto_final_nome"],
            "lote": item["lote"],
            "data_fabricacao": item["data_fabricacao"],
            "data_validade": item["data_validade"],
            "validade_dias": item["validade_dias"],
            "created_at": item["created_at"]
        }
        for item in linhas
    ])


@app.route("/estoque_produto_acabado")
def estoque_produto_acabado():
    conn = conectar_banco()
    cursor = conn.cursor()

    # Garante que produtos finais com estoque inicial digitado apareçam no controle por lote.
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


@app.route("/lote/<lote>")
def detalhe_lote(lote):
    conn = conectar_banco()
    cursor = conn.cursor()

    # Primeiro procura o lote no estoque de produto acabado.
    # Isso permite rastrear também os lotes automáticos criados a partir
    # do estoque inicial informado no cadastro do Produto Final.
    cursor.execute("""
        SELECT *
        FROM estoque_produto_acabado
        WHERE lote = ?
        ORDER BY id DESC
        LIMIT 1
    """, (lote,))
    estoque = cursor.fetchone()

    producao = None
    itens = []

    if estoque and estoque["producao_id"]:
        cursor.execute("""
            SELECT *
            FROM producoes
            WHERE id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (int(estoque["producao_id"]),))
        producao = cursor.fetchone()

    # Caso seja um lote antigo/normal que ainda não tenha registro no estoque
    # de produto acabado, procura pelo código do lote na tabela de produções.
    if not producao:
        cursor.execute("""
            SELECT *
            FROM producoes
            WHERE lote = ?
            ORDER BY id DESC
            LIMIT 1
        """, (lote,))
        producao = cursor.fetchone()

    if producao:
        cursor.execute("""
            SELECT ingrediente_nome, quantidade_baixada, unidade
            FROM producao_itens
            WHERE producao_id = ?
            ORDER BY ingrediente_nome ASC
        """, (int(producao["id"]),))
        itens = cursor.fetchall()

        if not estoque:
            cursor.execute("""
                SELECT *
                FROM estoque_produto_acabado
                WHERE lote = ?
                ORDER BY id DESC
                LIMIT 1
            """, (lote,))
            estoque = cursor.fetchone()

    if not estoque and not producao:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Lote não encontrado"}), 404

    conn.close()

    return jsonify({
        "status": "sucesso",
        "tipo": "PRODUCAO" if producao else "AJUSTE_ESTOQUE",
        "producao": dict(producao) if producao else None,
        "estoque": dict(estoque) if estoque else None,
        "itens": [dict(item) for item in itens]
    })



@app.route("/movimentacoes_produto_acabado")
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


@app.route("/registrar_saida_produto_acabado", methods=["POST"])
def registrar_saida_produto_acabado():
    dados = request.json or {}
    estoque_id = dados.get("estoque_id")
    quantidade = normalizar_float(dados.get("quantidade"), 0)
    observacao = str(dados.get("observacao", "")).strip()
    tipo_operacao = str(dados.get("tipo_operacao", "Venda") or "Venda").strip()
    valor_unitario_informado = normalizar_float(dados.get("valor_unitario"), 0)
    status_financeiro = str(dados.get("status_financeiro", "Recebido") or "Recebido").strip()
    forma_pagamento = str(dados.get("forma_pagamento", "") or "").strip()

    if not estoque_id:
        return jsonify({"status": "erro", "mensagem": "Selecione um lote do produto acabado."}), 400

    if quantidade <= 0:
        return jsonify({"status": "erro", "mensagem": "Informe uma quantidade maior que zero."}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")

    cursor.execute("""
        SELECT e.*, COALESCE(pf.preco_venda, 0) AS preco_venda
        FROM estoque_produto_acabado e
        LEFT JOIN produtos_finais pf ON pf.id = e.produto_final_id
        WHERE e.id = ?
    """, (int(estoque_id),))
    estoque = cursor.fetchone()

    if not estoque:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Lote não encontrado no estoque de produto acabado."}), 404

    saldo_anterior = float(estoque["saldo_atual"] or 0)
    if quantidade > saldo_anterior:
        conn.close()
        return jsonify({
            "status": "erro",
            "mensagem": f"Saldo insuficiente. Saldo atual: {saldo_anterior:.3f}."
        }), 400

    novo_saldo = round(saldo_anterior - quantidade, 3)

    cursor.execute("""
        UPDATE estoque_produto_acabado
        SET saldo_atual = ?
        WHERE id = ?
    """, (novo_saldo, int(estoque_id)))

    produto_final_id = estoque["produto_final_id"]
    if produto_final_id:
        cursor.execute("""
            UPDATE produtos_finais
            SET estoque_atual = CASE
                    WHEN estoque_atual - ? < 0 THEN 0
                    ELSE ROUND(estoque_atual - ?, 3)
                END,
                updated_at = ?
            WHERE id = ?
        """, (quantidade, quantidade, agora_brasilia(), int(produto_final_id)))

    tipo_movimento = "VENDA" if tipo_operacao.lower() == "venda" else "SAIDA"
    cursor.execute("""
        INSERT INTO movimentacoes_produto_acabado (
            estoque_id, produto_final_id, produto_final_nome, lote, tipo,
            quantidade, saldo_anterior, saldo_atual, observacao, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(estoque_id),
        int(produto_final_id) if produto_final_id else None,
        estoque["produto_final_nome"],
        estoque["lote"],
        tipo_movimento,
        quantidade,
        saldo_anterior,
        novo_saldo,
        observacao,
        agora_brasilia()
    ))

    venda_id = None
    valor_unitario = valor_unitario_informado if valor_unitario_informado > 0 else float(estoque["preco_venda"] or 0)
    valor_total = round(valor_unitario * quantidade, 2)
    custo_unitario = 0.0
    if estoque["producao_id"]:
        cursor.execute("SELECT custo_unitario FROM producoes WHERE id=?", (int(estoque["producao_id"]),))
        prod = cursor.fetchone()
        custo_unitario = float(prod["custo_unitario"] or 0) if prod else 0.0
    cmv_total = round(custo_unitario * quantidade, 2)
    lucro_estimado = round(valor_total - cmv_total, 2)

    if tipo_movimento == "VENDA":
        cursor.execute("""
            INSERT INTO vendas_erp (
                origem, produto_final_id, produto_final_nome, estoque_id, lote, quantidade,
                valor_unitario, valor_total, custo_unitario, cmv_total, lucro_estimado, observacao, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "Venda Manual",
            int(produto_final_id) if produto_final_id else None,
            estoque["produto_final_nome"],
            int(estoque_id),
            estoque["lote"],
            quantidade,
            valor_unitario,
            valor_total,
            custo_unitario,
            cmv_total,
            lucro_estimado,
            observacao,
            agora_brasilia()
        ))
        venda_id = cursor.lastrowid
        if valor_total > 0:
            status_receita = "Pendente" if status_financeiro.lower() in ("pendente", "a receber") else "Recebido"
            registrar_lancamento_financeiro(cursor, "receita", f"Venda - {estoque['produto_final_nome']}", valor_total, "Vendas", "Venda Manual", venda_id, status_receita, forma_pagamento or "Venda", observacao or f"Venda do lote {estoque['lote']}")
        if cmv_total > 0:
            registrar_lancamento_financeiro(cursor, "despesa", f"CMV - {estoque['produto_final_nome']}", cmv_total, "CMV", "Venda Manual", venda_id, "Pago", "Automático", f"CMV vinculado à venda do lote {estoque['lote']}")

    conn.commit()
    conn.close()

    return jsonify({
        "status": "sucesso",
        "mensagem": f"{('Venda' if tipo_movimento == 'VENDA' else 'Saída')} registrada. Novo saldo do lote {estoque['lote']}: {novo_saldo:.3f}.",
        "venda_id": venda_id,
        "valor_total": valor_total,
        "cmv_total": cmv_total,
        "lucro_estimado": lucro_estimado
    })


@app.route("/rotulo_lote/<lote>")
def rotulo_lote(lote):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM estoque_produto_acabado
        WHERE lote = ?
        ORDER BY id DESC
        LIMIT 1
    """, (lote,))
    estoque = cursor.fetchone()

    producao = None
    if estoque and estoque["producao_id"]:
        cursor.execute("""
            SELECT * FROM producoes
            WHERE id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (int(estoque["producao_id"]),))
        producao = cursor.fetchone()

    if not producao:
        cursor.execute("""
            SELECT * FROM producoes
            WHERE lote = ?
            ORDER BY id DESC
            LIMIT 1
        """, (lote,))
        producao = cursor.fetchone()

    if not estoque and producao:
        cursor.execute("""
            SELECT * FROM estoque_produto_acabado
            WHERE lote = ?
            ORDER BY id DESC
            LIMIT 1
        """, (lote,))
        estoque = cursor.fetchone()

    if not estoque and not producao:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Lote não encontrado"}), 404

    receita_id = None
    produto_final_id = None
    if producao:
        receita_id = producao["receita_id"]
        produto_final_id = producao["produto_final_id"] if "produto_final_id" in producao.keys() else None
    elif estoque:
        receita_id = estoque["receita_id"]
        produto_final_id = estoque["produto_final_id"]

    receita = None
    if receita_id:
        cursor.execute("SELECT * FROM receitas WHERE id = ?", (int(receita_id),))
        receita = cursor.fetchone()

    produto_final = None
    if produto_final_id:
        cursor.execute("SELECT * FROM produtos_finais WHERE id = ?", (int(produto_final_id),))
        produto_final = cursor.fetchone()

    if not produto_final and receita_id:
        cursor.execute("""
            SELECT * FROM produtos_finais
            WHERE receita_id = ? AND ativo = 1
            ORDER BY id DESC
            LIMIT 1
        """, (int(receita_id),))
        produto_final = cursor.fetchone()

    itens_receita = []
    if receita_id:
        cursor.execute("""
            SELECT ingrediente_id, ingrediente_nome, gramas
            FROM receita_itens
            WHERE receita_id = ?
            ORDER BY id ASC
        """, (int(receita_id),))
        itens_receita = cursor.fetchall()

    itens_lote = []
    if producao:
        cursor.execute("""
            SELECT ingrediente_nome, quantidade_baixada, unidade
            FROM producao_itens
            WHERE producao_id = ?
            ORDER BY ingrediente_nome ASC
        """, (int(producao["id"]),))
        itens_lote = cursor.fetchall()

    conn.close()

    receita_dict = dict(receita) if receita else None
    if receita_dict is not None:
        receita_dict["itens"] = [
            {"id": item["ingrediente_id"], "nome": item["ingrediente_nome"], "gramas": item["gramas"]}
            for item in itens_receita
        ]

    return jsonify({
        "status": "sucesso",
        "tipo": "PRODUCAO" if producao else "AJUSTE_ESTOQUE",
        "producao": dict(producao) if producao else None,
        "receita": receita_dict,
        "produto_final": dict(produto_final) if produto_final else None,
        "estoque": dict(estoque) if estoque else None,
        "itens_lote": [dict(item) for item in itens_lote]
    })


@app.route("/produtos_finais")
def listar_produtos_finais():
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            pf.*,
            r.nome AS receita_nome
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        ORDER BY pf.id DESC
    """)

    linhas = cursor.fetchall()
    conn.close()

    return jsonify([dict(item) for item in linhas])


@app.route("/produto_final/<int:produto_id>")
def abrir_produto_final(produto_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM produtos_finais WHERE id = ?", (int(produto_id),))
    produto = cursor.fetchone()
    conn.close()

    if not produto:
        return jsonify({"status": "erro", "mensagem": "Produto final não encontrado"}), 404

    return jsonify({"status": "sucesso", "produto": dict(produto)})


def parse_linhas_receitas_combo(texto):
    itens = []
    for linha in str(texto or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue

        nome = linha
        gramas = 0.0
        if " - " in linha:
            nome, quantidade = linha.rsplit(" - ", 1)
            gramas = parse_peso_texto_para_gramas(quantidade)

        itens.append({"nome": nome.strip(), "gramas": gramas})
    return itens


def parse_peso_texto_para_gramas(texto):
    valor = str(texto or "").strip().lower()
    if not valor:
        return 0.0

    eh_kg = "kg" in valor or "quilo" in valor
    valor = (
        valor.replace("quilogramas", "")
        .replace("quilograma", "")
        .replace("quilos", "")
        .replace("quilo", "")
        .replace("kg", "")
        .replace("gramas", "")
        .replace("grs", "")
        .replace("g", "")
        .strip()
    )
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    elif valor.count(".") == 1 and len(valor.split(".")[-1]) == 3 and not eh_kg:
        valor = valor.replace(".", "")

    gramas = normalizar_float(valor, 0)
    return gramas * 1000 if eh_kg else gramas


def montar_ficha_produto_final_combo(cursor, produto):
    itens_combo = parse_linhas_receitas_combo(produto["receitas_combo"] if "receitas_combo" in produto.keys() else "")
    receita_id_vinculada = produto["receita_id"] if "receita_id" in produto.keys() else None
    if not itens_combo and receita_id_vinculada:
        cursor.execute("SELECT id, nome FROM receitas WHERE id = ?", (int(produto["receita_id"]),))
        receita_vinculada = cursor.fetchone()
        if receita_vinculada:
            peso_texto = produto["peso_liquido"] if "peso_liquido" in produto.keys() else ""
            peso_combo = parse_peso_texto_para_gramas(peso_texto)
            itens_combo = [{"nome": receita_vinculada["nome"], "gramas": peso_combo}]
    detalhamento = []
    total_gramas = 0.0
    custo_total = 0.0
    nutrientes_totais = {
        "calorias": 0.0,
        "carboidratos": 0.0,
        "acucares_totais": 0.0,
        "acucares_adicionados": 0.0,
        "proteinas": 0.0,
        "gorduras": 0.0,
        "gorduras_saturadas": 0.0,
        "gorduras_trans": 0.0,
        "fibra": 0.0,
        "sodio": 0.0,
    }

    for item_combo in itens_combo:
        cursor.execute("""
            SELECT *
            FROM receitas
            WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))
            ORDER BY id DESC
            LIMIT 1
        """, (item_combo["nome"],))
        receita = cursor.fetchone()
        if not receita:
            continue

        recalcular_receitas_salvas(cursor, int(receita["id"]))
        cursor.execute("SELECT * FROM receitas WHERE id = ?", (int(receita["id"]),))
        receita = cursor.fetchone()

        cursor.execute("""
            SELECT ri.ingrediente_id, ri.ingrediente_nome, ri.gramas,
                   i.preco_kg, i.unidade
            FROM receita_itens ri
            LEFT JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receita_id = ?
            ORDER BY ri.id ASC
        """, (int(receita["id"]),))
        itens_receita = cursor.fetchall()

        total_receita_base = sum(float(subitem["gramas"] or 0) for subitem in itens_receita)
        quantidade_combo = float(item_combo["gramas"] or 0) or total_receita_base
        if total_receita_base <= 0:
            total_receita_base = quantidade_combo or 1

        fator = quantidade_combo / total_receita_base if total_receita_base else 1
        ingredientes = []
        custo_prato = 0.0

        for subitem in itens_receita:
            gramas_calculadas = round(float(subitem["gramas"] or 0) * fator, 2)
            custo = round(calcular_custo_por_quantidade(
                subitem["preco_kg"] or 0,
                subitem["unidade"] or "KG",
                gramas_calculadas
            ), 2)
            custo_prato += custo
            ingredientes.append({
                "id": subitem["ingrediente_id"],
                "nome": subitem["ingrediente_nome"],
                "gramas": gramas_calculadas,
                "custo": custo
            })

        proporcao_nutri = quantidade_combo / 100 if quantidade_combo else 0
        for campo in nutrientes_totais.keys():
            nutrientes_totais[campo] += float(receita[campo] or 0) * proporcao_nutri

        total_gramas += quantidade_combo
        custo_total += custo_prato
        detalhamento.append({
            "receita_id": receita["id"],
            "prato": receita["nome"],
            "quantidade_combo": round(quantidade_combo, 2),
            "total_gramas_prato_base": round(total_receita_base, 2),
            "fator": round(fator, 6),
            "custo": round(custo_prato, 2),
            "ingredientes": ingredientes
        })

    divisor = total_gramas / 100 if total_gramas else 1
    return {
        "id": f"produto-{produto['id']}",
        "nome": produto["nome"],
        "categoria_nome": produto["categoria"] or "Combo",
        "rendimento": 1,
        "margem_lucro": 0,
        "custo_total": round(custo_total, 2),
        "custo_porcao": round(custo_total, 2),
        "preco_venda": produto["preco_venda"] or custo_total,
        "preco_venda_porcao": produto["preco_venda"] or custo_total,
        "calorias": round(nutrientes_totais["calorias"] / divisor, 1),
        "carboidratos": round(nutrientes_totais["carboidratos"] / divisor, 1),
        "acucares_totais": round(nutrientes_totais["acucares_totais"] / divisor, 1),
        "acucares_adicionados": round(nutrientes_totais["acucares_adicionados"] / divisor, 1),
        "proteinas": round(nutrientes_totais["proteinas"] / divisor, 1),
        "gorduras": round(nutrientes_totais["gorduras"] / divisor, 1),
        "gorduras_saturadas": round(nutrientes_totais["gorduras_saturadas"] / divisor, 1),
        "gorduras_trans": round(nutrientes_totais["gorduras_trans"] / divisor, 1),
        "fibra": round(nutrientes_totais["fibra"] / divisor, 1),
        "sodio": round(nutrientes_totais["sodio"] / divisor, 1),
        "itens": [],
        "combo_detalhado": detalhamento,
        "rotulo": {
            "peso_liquido": produto["peso_liquido"] or "",
            "porcao": produto["porcao"] or "100 g",
            "fabricante": produto["fabricante"] or "Wevvo",
            "ingredientes": produto["ingredientes_rotulo"] or "",
            "alergicos": produto["alergicos"] or "",
            "gluten": produto["gluten"] or "CONTÉM GLÚTEN",
            "lactose": produto["lactose"] or "CONTÉM LACTOSE",
            "conservacao": produto["conservacao"] or "",
            "modo_preparo": produto["modo_preparo"] or "",
            "empresa": produto["empresa"] or "PRODUZIDO POR: WEVVO",
            "responsavel": produto["responsavel"] or "Indústria Brasileira",
            "endereco": produto["endereco"] or ""
        }
    }


@app.route("/produto_final_ficha/<int:produto_id>")
def produto_final_ficha(produto_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM produtos_finais WHERE id = ?", (int(produto_id),))
    produto = cursor.fetchone()
    if not produto:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Combo não encontrado"}), 404

    ficha = montar_ficha_produto_final_combo(cursor, produto)
    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "receita": ficha})


@app.route("/produto_final_por_receita/<int:receita_id>")
def produto_final_por_receita(receita_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT *
        FROM produtos_finais
        WHERE receita_id = ? AND ativo = 1
        ORDER BY id DESC
        LIMIT 1
    """, (int(receita_id),))
    produto = cursor.fetchone()
    conn.close()

    if not produto:
        return jsonify({"status": "erro", "mensagem": "Nenhum produto final vinculado a esta receita"}), 404

    return jsonify({"status": "sucesso", "produto": dict(produto)})


@app.route("/salvar_produto_final", methods=["POST"])
def salvar_produto_final():
    dados = request.json or {}

    produto_id = dados.get("id")
    nome = str(dados.get("nome", "")).strip()

    if not nome:
        return jsonify({"status": "erro", "mensagem": "Nome do produto final é obrigatório"}), 400

    receita_id = dados.get("receita_id") or None
    if receita_id in ["", 0, "0"]:
        receita_id = None

    campos = {
        "receita_id": receita_id,
        "codigo_interno": str(dados.get("codigo_interno", "")).strip(),
        "nome": nome,
        "categoria": str(dados.get("categoria", "")).strip(),
        "peso_liquido": str(dados.get("peso_liquido", "")).strip(),
        "porcao": str(dados.get("porcao", "100 g (1 embalagem)")).strip(),
        "preco_venda": normalizar_float(dados.get("preco_venda"), 0),
        "estoque_atual": normalizar_float(dados.get("estoque_atual"), 0),
        "estoque_minimo": normalizar_float(dados.get("estoque_minimo"), 0),
        "validade_dias": int(normalizar_float(dados.get("validade_dias"), 0)),
        "conservacao": str(dados.get("conservacao", "")).strip(),
        "modo_preparo": str(dados.get("modo_preparo", "")).strip(),
        "ingredientes_rotulo": str(dados.get("ingredientes_rotulo", "")).strip(),
        "receitas_combo": str(dados.get("receitas_combo", "")).strip(),
        "alergicos": str(dados.get("alergicos", "")).strip(),
        "gluten": str(dados.get("gluten", "CONTÉM GLÚTEN")).strip(),
        "lactose": str(dados.get("lactose", "CONTÉM LACTOSE")).strip(),
        "fabricante": str(dados.get("fabricante", "Wevvo")).strip(),
        "empresa": str(dados.get("empresa", "PRODUZIDO POR: WEVVO")).strip(),
        "responsavel": str(dados.get("responsavel", "Indústria Brasileira")).strip(),
        "endereco": str(dados.get("endereco", "")).strip(),
        "ativo": 1 if dados.get("ativo", True) else 0,
        "updated_at": agora_brasilia()
    }

    conn = conectar_banco()
    cursor = conn.cursor()

    if produto_id:
        cursor.execute("""
            UPDATE produtos_finais
            SET receita_id = ?, codigo_interno = ?, nome = ?, categoria = ?, peso_liquido = ?, porcao = ?,
                preco_venda = ?, estoque_atual = ?, estoque_minimo = ?, validade_dias = ?, conservacao = ?,
                modo_preparo = ?, ingredientes_rotulo = ?, receitas_combo = ?, alergicos = ?, gluten = ?, lactose = ?, fabricante = ?,
                empresa = ?, responsavel = ?, endereco = ?, ativo = ?, updated_at = ?
            WHERE id = ?
        """, (
            campos["receita_id"], campos["codigo_interno"], campos["nome"], campos["categoria"], campos["peso_liquido"], campos["porcao"],
            campos["preco_venda"], campos["estoque_atual"], campos["estoque_minimo"], campos["validade_dias"], campos["conservacao"],
            campos["modo_preparo"], campos["ingredientes_rotulo"], campos["receitas_combo"], campos["alergicos"], campos["gluten"], campos["lactose"], campos["fabricante"],
            campos["empresa"], campos["responsavel"], campos["endereco"], campos["ativo"], campos["updated_at"], int(produto_id)
        ))
        produto_id_final = int(produto_id)
    else:
        cursor.execute("""
            INSERT INTO produtos_finais (
                receita_id, codigo_interno, nome, categoria, peso_liquido, porcao, preco_venda,
                estoque_atual, estoque_minimo, validade_dias, conservacao, modo_preparo,
                ingredientes_rotulo, receitas_combo, alergicos, gluten, lactose, fabricante, empresa,
                responsavel, endereco, ativo, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            campos["receita_id"], campos["codigo_interno"], campos["nome"], campos["categoria"], campos["peso_liquido"], campos["porcao"], campos["preco_venda"],
            campos["estoque_atual"], campos["estoque_minimo"], campos["validade_dias"], campos["conservacao"], campos["modo_preparo"],
            campos["ingredientes_rotulo"], campos["receitas_combo"], campos["alergicos"], campos["gluten"], campos["lactose"], campos["fabricante"], campos["empresa"],
            campos["responsavel"], campos["endereco"], campos["ativo"], agora_brasilia(), campos["updated_at"]
        ))
        produto_id_final = cursor.lastrowid

    sincronizar_produto_acabado_por_estoque(cursor, produto_id_final)

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso", "id": produto_id_final})


@app.route("/excluir_produto_final/<int:produto_id>", methods=["DELETE"])
def excluir_produto_final(produto_id):
    conn = conectar_banco()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM produtos_finais WHERE id = ?", (int(produto_id),))

    conn.commit()
    conn.close()

    return jsonify({"status": "sucesso"})


@app.route("/conferencia_sistema")
def conferencia_sistema():
    conn = conectar_banco()
    cursor = conn.cursor()

    hoje = data_brasilia_obj().date()
    limite_validade = hoje + timedelta(days=7)

    def buscar_lista(sql, params=()):
        cursor.execute(sql, params)
        return [dict(linha) for linha in cursor.fetchall()]

    def buscar_total(sql, params=()):
        cursor.execute(sql, params)
        linha = cursor.fetchone()
        return list(dict(linha).values())[0] if linha else 0

    secoes = []

    ingredientes_sem_preco = buscar_lista("""
        SELECT id, nome, unidade
        FROM ingredientes
        WHERE COALESCE(preco_kg, 0) <= 0
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "ingredientes_sem_preco",
        "titulo": "Ingredientes sem preço",
        "gravidade": "ALTA",
        "total": buscar_total("SELECT COUNT(*) AS total FROM ingredientes WHERE COALESCE(preco_kg, 0) <= 0"),
        "itens": ingredientes_sem_preco
    })

    ingredientes_sem_nutriente = buscar_lista("""
        SELECT id, nome
        FROM ingredientes
        WHERE COALESCE(fonte_nutricional, '') NOT IN ('NAO_ALIMENTO', 'AUTO_TABELA_COMPLEMENTAR')
          AND COALESCE(calorias_100g, 0) = 0
          AND COALESCE(carboidratos_100g, 0) = 0
          AND COALESCE(acucares_totais_100g, 0) = 0
          AND COALESCE(acucares_adicionados_100g, 0) = 0
          AND COALESCE(proteinas_100g, 0) = 0
          AND COALESCE(gorduras_100g, 0) = 0
          AND COALESCE(gorduras_saturadas_100g, 0) = 0
          AND COALESCE(gorduras_trans_100g, 0) = 0
          AND COALESCE(fibra_alimentar_100g, 0) = 0
          AND COALESCE(sodio_100g, 0) = 0
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "ingredientes_sem_nutriente",
        "titulo": "Ingredientes sem nutrientes",
        "gravidade": "MEDIA",
        "total": buscar_total("""
            SELECT COUNT(*) AS total
            FROM ingredientes
            WHERE COALESCE(fonte_nutricional, '') NOT IN ('NAO_ALIMENTO', 'AUTO_TABELA_COMPLEMENTAR')
              AND COALESCE(calorias_100g, 0) = 0
              AND COALESCE(carboidratos_100g, 0) = 0
              AND COALESCE(acucares_totais_100g, 0) = 0
              AND COALESCE(acucares_adicionados_100g, 0) = 0
              AND COALESCE(proteinas_100g, 0) = 0
              AND COALESCE(gorduras_100g, 0) = 0
              AND COALESCE(gorduras_saturadas_100g, 0) = 0
              AND COALESCE(gorduras_trans_100g, 0) = 0
              AND COALESCE(fibra_alimentar_100g, 0) = 0
              AND COALESCE(sodio_100g, 0) = 0
        """),
        "itens": ingredientes_sem_nutriente
    })

    receitas_sem_categoria = buscar_lista("""
        SELECT id, nome
        FROM receitas
        WHERE COALESCE(categoria_id, 0) = 0
          AND COALESCE(TRIM(categoria_nome), '') = ''
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "receitas_sem_categoria",
        "titulo": "Receitas sem categoria",
        "gravidade": "MEDIA",
        "total": buscar_total("""
            SELECT COUNT(*) AS total
            FROM receitas
            WHERE COALESCE(categoria_id, 0) = 0
              AND COALESCE(TRIM(categoria_nome), '') = ''
        """),
        "itens": receitas_sem_categoria
    })

    receitas_custo_zerado = buscar_lista("""
        SELECT id, nome, categoria_nome, custo_total
        FROM receitas
        WHERE COALESCE(custo_total, 0) <= 0
        ORDER BY categoria_nome ASC, nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "receitas_custo_zerado",
        "titulo": "Receitas com custo zerado",
        "gravidade": "ALTA",
        "total": buscar_total("SELECT COUNT(*) AS total FROM receitas WHERE COALESCE(custo_total, 0) <= 0"),
        "itens": receitas_custo_zerado
    })

    combos_peso_suspeito = buscar_lista("""
        SELECT r.id, r.nome, COALESCE(SUM(ri.gramas), 0) AS total_gramas
        FROM receitas r
        LEFT JOIN receita_itens ri ON ri.receita_id = r.id
        WHERE r.categoria_nome = 'Combos'
        GROUP BY r.id, r.nome
        HAVING total_gramas < 1000
        ORDER BY r.nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "combos_peso_suspeito",
        "titulo": "Combos com peso suspeito",
        "gravidade": "ALTA",
        "total": len(combos_peso_suspeito),
        "itens": combos_peso_suspeito
    })

    produtos_sem_receita = buscar_lista("""
        SELECT id, nome, codigo_interno
        FROM produtos_finais
        WHERE ativo = 1
          AND COALESCE(receita_id, 0) = 0
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "produtos_sem_receita",
        "titulo": "Produtos finais sem receita vinculada",
        "gravidade": "ALTA",
        "total": buscar_total("""
            SELECT COUNT(*) AS total
            FROM produtos_finais
            WHERE ativo = 1
              AND COALESCE(receita_id, 0) = 0
        """),
        "itens": produtos_sem_receita
    })

    produtos_sem_preco = buscar_lista("""
        SELECT id, nome, preco_venda
        FROM produtos_finais
        WHERE ativo = 1
          AND COALESCE(preco_venda, 0) <= 0
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "produtos_sem_preco",
        "titulo": "Produtos finais sem preço de venda",
        "gravidade": "MEDIA",
        "total": buscar_total("""
            SELECT COUNT(*) AS total
            FROM produtos_finais
            WHERE ativo = 1
              AND COALESCE(preco_venda, 0) <= 0
        """),
        "itens": produtos_sem_preco
    })

    estoque_baixo = buscar_lista("""
        SELECT id, nome, estoque_atual, estoque_minimo, unidade
        FROM ingredientes
        WHERE estoque_minimo > 0
          AND estoque_atual <= estoque_minimo
        ORDER BY nome ASC
        LIMIT 80
    """)
    secoes.append({
        "chave": "estoque_baixo",
        "titulo": "Ingredientes com estoque baixo",
        "gravidade": "MEDIA",
        "total": buscar_total("""
            SELECT COUNT(*) AS total
            FROM ingredientes
            WHERE estoque_minimo > 0
              AND estoque_atual <= estoque_minimo
        """),
        "itens": estoque_baixo
    })

    lotes_alerta = []
    for lote in buscar_lista("""
        SELECT e.id, e.produto_final_nome, e.lote, e.saldo_atual, e.data_validade
        FROM estoque_produto_acabado e
        INNER JOIN produtos_finais pf ON pf.id = e.produto_final_id
        WHERE COALESCE(pf.ativo, 1) = 1
          AND e.saldo_atual > 0
          AND COALESCE(e.data_validade, '') <> ''
        ORDER BY e.data_validade ASC
        LIMIT 200
    """):
        try:
            validade = datetime.strptime(lote["data_validade"], "%d/%m/%Y").date()
        except Exception:
            continue
        if validade <= limite_validade:
            lote["dias_para_vencer"] = (validade - hoje).days
            lotes_alerta.append(lote)

    secoes.append({
        "chave": "lotes_vencendo",
        "titulo": "Lotes vencidos ou próximos do vencimento",
        "gravidade": "ALTA",
        "total": len(lotes_alerta),
        "itens": lotes_alerta[:80]
    })

    total_alertas = sum(int(secao["total"] or 0) for secao in secoes)
    alertas_altos = sum(int(secao["total"] or 0) for secao in secoes if secao["gravidade"] == "ALTA")
    alertas_medios = sum(int(secao["total"] or 0) for secao in secoes if secao["gravidade"] == "MEDIA")
    alertas_baixos = sum(int(secao["total"] or 0) for secao in secoes if secao["gravidade"] == "BAIXA")
    resumo = {
        "total_alertas": total_alertas,
        "alertas_altos": alertas_altos,
        "alertas_medios": alertas_medios,
        "alertas_baixos": alertas_baixos,
        "receitas": buscar_total("SELECT COUNT(*) AS total FROM receitas"),
        "ingredientes": buscar_total("SELECT COUNT(*) AS total FROM ingredientes"),
        "produtos_finais": buscar_total("SELECT COUNT(*) AS total FROM produtos_finais WHERE ativo = 1")
    }

    conn.close()
    return jsonify({"status": "sucesso", "resumo": resumo, "secoes": secoes})


@app.route("/preencher_nutrientes_automatico", methods=["POST"])
def preencher_nutrientes_automatico():
    conn = conectar_banco()
    cursor = conn.cursor()

    campos_nutri = [
        "calorias_100g",
        "carboidratos_100g",
        "acucares_totais_100g",
        "acucares_adicionados_100g",
        "proteinas_100g",
        "gorduras_100g",
        "gorduras_saturadas_100g",
        "gorduras_trans_100g",
        "fibra_alimentar_100g",
        "sodio_100g"
    ]

    condicao_vazio = " AND ".join([f"COALESCE({campo}, 0) = 0" for campo in campos_nutri])
    condicao_com_nutri = " OR ".join([f"COALESCE({campo}, 0) > 0" for campo in campos_nutri])

    cursor.execute(f"""
        SELECT id, nome
        FROM ingredientes
        WHERE COALESCE(fonte_nutricional, '') NOT IN ('NAO_ALIMENTO', 'AUTO_TABELA_COMPLEMENTAR')
          AND {condicao_vazio}
        ORDER BY nome ASC
    """)
    pendentes = [dict(linha) for linha in cursor.fetchall()]

    cursor.execute(f"""
        SELECT id, codigo_taco, nome, fonte_nutricional, taco_match_nome, {", ".join(campos_nutri)}
        FROM ingredientes
        WHERE ({condicao_com_nutri})
          AND (
                COALESCE(fonte_nutricional, '') LIKE '%TACO%'
                OR nome LIKE '%(TACO)%'
                OR COALESCE(taco_match_nome, '') <> ''
                OR COALESCE(fonte_nutricional, '') = 'RECEITA_TECNICA'
          )
        ORDER BY
            CASE
                WHEN COALESCE(fonte_nutricional, '') LIKE '%TACO%' THEN 0
                WHEN COALESCE(taco_match_nome, '') <> '' THEN 1
                WHEN COALESCE(fonte_nutricional, '') = 'RECEITA_TECNICA' THEN 2
                ELSE 3
            END,
            nome ASC
    """)
    bases = [dict(linha) for linha in cursor.fetchall()]

    atualizados = []
    sem_impacto = []
    ignorados = []
    limite_similaridade = 0.55

    for item in pendentes:
        if eh_item_sem_impacto_nutricional(item["nome"]):
            cursor.execute("""
                UPDATE ingredientes
                SET fonte_nutricional = ?,
                    taco_match_nome = ?,
                    taco_match_codigo = NULL,
                    taco_match_similaridade = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                "NAO_ALIMENTO",
                "Sem impacto nutricional",
                100,
                agora_brasilia(),
                item["id"]
            ))
            sem_impacto.append({"id": item["id"], "nome": item["nome"]})
            continue

        melhor = None
        melhor_score = 0.0

        for base in bases:
            if int(base["id"]) == int(item["id"]):
                continue
            score = similaridade_nutricional(item["nome"], base["nome"])
            if base["fonte_nutricional"] == "RECEITA_TECNICA" and score < 0.92:
                continue
            if score > melhor_score:
                melhor_score = score
                melhor = base

        if not melhor or melhor_score < limite_similaridade:
            complemento = nutrientes_complementares(item["nome"])
            if complemento:
                cursor.execute("""
                    UPDATE ingredientes
                    SET calorias_100g = ?,
                        carboidratos_100g = ?,
                        acucares_totais_100g = ?,
                        acucares_adicionados_100g = ?,
                        proteinas_100g = ?,
                        gorduras_100g = ?,
                        gorduras_saturadas_100g = ?,
                        gorduras_trans_100g = ?,
                        fibra_alimentar_100g = ?,
                        sodio_100g = ?,
                        fonte_nutricional = ?,
                        taco_match_nome = ?,
                        taco_match_codigo = NULL,
                        taco_match_similaridade = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    *complemento["valores"],
                    "AUTO_TABELA_COMPLEMENTAR",
                    complemento["base"],
                    100,
                    agora_brasilia(),
                    item["id"]
                ))
                atualizados.append({
                    "id": item["id"],
                    "nome": item["nome"],
                    "base": complemento["base"],
                    "similaridade": 100
                })
                continue

            ignorados.append({
                "id": item["id"],
                "nome": item["nome"],
                "similaridade": round(melhor_score * 100, 1),
                "base": melhor["nome"] if melhor else ""
            })
            continue

        valores = [float(melhor[campo] or 0) for campo in campos_nutri]
        cursor.execute(f"""
            UPDATE ingredientes
            SET calorias_100g = ?,
                carboidratos_100g = ?,
                acucares_totais_100g = ?,
                acucares_adicionados_100g = ?,
                proteinas_100g = ?,
                gorduras_100g = ?,
                gorduras_saturadas_100g = ?,
                gorduras_trans_100g = ?,
                fibra_alimentar_100g = ?,
                sodio_100g = ?,
                fonte_nutricional = ?,
                taco_match_nome = ?,
                taco_match_codigo = ?,
                taco_match_similaridade = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            *valores,
            "AUTO_SIMILAR_TACO",
            melhor["nome"],
            melhor["codigo_taco"],
            round(melhor_score * 100, 1),
            agora_brasilia(),
            item["id"]
        ))
        atualizados.append({
            "id": item["id"],
            "nome": item["nome"],
            "base": melhor["nome"],
            "similaridade": round(melhor_score * 100, 1)
        })

    conn.commit()

    cursor.execute(f"""
        SELECT COUNT(*) AS total
        FROM ingredientes
        WHERE COALESCE(fonte_nutricional, '') NOT IN ('NAO_ALIMENTO', 'AUTO_TABELA_COMPLEMENTAR')
          AND {condicao_vazio}
    """)
    restantes = int(cursor.fetchone()["total"] or 0)
    conn.close()

    return jsonify({
        "status": "sucesso",
        "verificados": len(pendentes),
        "atualizados": len(atualizados),
        "sem_impacto": len(sem_impacto),
        "restantes": restantes,
        "ignorados": ignorados[:30],
        "exemplos": atualizados[:15],
        "exemplos_sem_impacto": sem_impacto[:15]
    })


@app.route("/dashboard_gerencial")
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



@app.route("/dashboard_gerencial_fase3")
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



@app.route("/dashboard_financeiro")
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


@app.route("/dashboard_inteligencia")
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



def mascarar_segredo(valor):
    valor = str(valor or '').strip()
    if not valor:
        return ''
    if len(valor) <= 6:
        return '*' * len(valor)
    return valor[:3] + ('*' * max(3, len(valor) - 6)) + valor[-3:]


def requisitos_integracao(nome):
    nome_normalizado = normalizar_busca(nome)
    if 'pix' in nome_normalizado:
        return ['chave_pix']
    if 'impressoras' in nome_normalizado or 'impressora' in nome_normalizado:
        return ['loja']
    if 'whatsapp' in nome_normalizado:
        return ['token', 'usuario']
    if 'nfc' in nome_normalizado or 'sat' in nome_normalizado:
        return ['api_key', 'loja']
    return ['api_key', 'token']


def avaliar_status_integracao(cfg):
    if not cfg:
        return 'Pendente', 'Aguardando configuração.'
    obrigatorios = requisitos_integracao(cfg.get('nome'))
    faltando = [campo for campo in obrigatorios if not str(cfg.get(campo) or '').strip()]
    if faltando:
        return 'Pendente', 'Campos pendentes: ' + ', '.join(faltando).replace('api_key', 'API key').replace('chave_pix', 'chave PIX')
    if int(cfg.get('ativo') or 0) != 1:
        return 'Configurado', 'Credenciais cadastradas. Ative a integração para liberar testes internos.'
    return 'Ativo', 'Configuração mínima preenchida e integração marcada como ativa.'


def carregar_configuracoes_integracoes(cursor):
    cursor.execute("SELECT * FROM integracoes_config ORDER BY id")
    return [dict(linha) for linha in cursor.fetchall()]


def registrar_integracao_log(cursor, integracao, acao, status, mensagem, detalhes=''):
    cursor.execute("""
        INSERT INTO integracoes_logs (integracao, acao, status, mensagem, detalhes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (integracao, acao, status, mensagem, detalhes, agora_brasilia()))


def acoes_disponiveis_integracao(nome):
    nome_normalizado = normalizar_busca(nome)
    if 'bling' in nome_normalizado:
        return [
            {'codigo': 'enviar_produtos', 'label': 'Enviar produtos'},
            {'codigo': 'importar_pedidos', 'label': 'Importar pedidos'},
            {'codigo': 'atualizar_estoque', 'label': 'Atualizar estoque'}
        ]
    if 'mercado livre' in nome_normalizado or 'shopee' in nome_normalizado:
        return [
            {'codigo': 'sincronizar_anuncios', 'label': 'Sincronizar anúncios'},
            {'codigo': 'importar_vendas', 'label': 'Importar vendas'},
            {'codigo': 'atualizar_estoque', 'label': 'Atualizar estoque'}
        ]
    if 'ifood' in nome_normalizado:
        return [
            {'codigo': 'sincronizar_cardapio', 'label': 'Sincronizar cardápio'},
            {'codigo': 'importar_pedidos', 'label': 'Importar pedidos'},
            {'codigo': 'pausar_itens_sem_estoque', 'label': 'Pausar sem estoque'}
        ]
    if 'whatsapp' in nome_normalizado:
        return [
            {'codigo': 'enviar_alerta', 'label': 'Enviar alerta'},
            {'codigo': 'enviar_lista_compras', 'label': 'Lista de compras'},
            {'codigo': 'enviar_pedido', 'label': 'Enviar pedido'}
        ]
    if 'pix' in nome_normalizado:
        return [
            {'codigo': 'gerar_cobranca', 'label': 'Gerar cobrança'},
            {'codigo': 'registrar_pagamento', 'label': 'Registrar pagamento'}
        ]
    if 'nfc' in nome_normalizado or 'sat' in nome_normalizado:
        return [
            {'codigo': 'validar_dados_fiscais', 'label': 'Validar dados fiscais'},
            {'codigo': 'preparar_emissao', 'label': 'Preparar emissão'}
        ]
    if 'impressora' in nome_normalizado:
        return [
            {'codigo': 'imprimir_teste', 'label': 'Imprimir teste'},
            {'codigo': 'imprimir_etiquetas', 'label': 'Imprimir etiquetas'},
            {'codigo': 'imprimir_pedidos', 'label': 'Imprimir pedidos'}
        ]
    return [{'codigo': 'sincronizar', 'label': 'Sincronizar'}]


def simular_execucao_integracao(cursor, nome, acao):
    """Etapa 6.4: ações simuladas com impacto interno real e rastreável no ERP.

    Não chama APIs externas. Quando possível, cria/atualiza registros internos seguros,
    como baixa de estoque de produto acabado, fila de produtos, mensagens e registros de pagamento.
    """
    agora = agora_brasilia()

    def contar(sql, params=()):
        try:
            cursor.execute(sql, params)
            linha = cursor.fetchone()
            return int((linha[0] if linha else 0) or 0)
        except Exception:
            return 0

    def registrar_operacao(tipo_impacto, entidade='', entidade_id=None, quantidade=0, valor=0, mensagem=''):
        cursor.execute("""
            INSERT INTO integracoes_operacoes (
                integracao, acao, tipo_impacto, entidade, entidade_id,
                quantidade, valor, mensagem, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nome, acao, tipo_impacto, entidade, entidade_id, quantidade, valor, mensagem, agora))

    def produto_modelo():
        cursor.execute("""
            SELECT id, nome, preco_venda, estoque_atual
            FROM produtos_finais
            WHERE COALESCE(ativo, 1) = 1
            ORDER BY id ASC
            LIMIT 1
        """)
        return cursor.fetchone()

    def lote_com_saldo():
        cursor.execute("""
            SELECT *
            FROM estoque_produto_acabado
            WHERE COALESCE(saldo_atual, 0) > 0
            ORDER BY id ASC
            LIMIT 1
        """)
        return cursor.fetchone()

    produtos = contar("SELECT COUNT(*) FROM produtos_finais WHERE COALESCE(ativo, 1) = 1")
    ingredientes = contar("SELECT COUNT(*) FROM ingredientes")
    lotes = contar("SELECT COUNT(*) FROM estoque_produto_acabado WHERE COALESCE(saldo_atual, 0) > 0")
    receitas = contar("SELECT COUNT(*) FROM receitas")

    if acao in ('importar_pedidos', 'importar_vendas'):
        lote = lote_com_saldo()
        if lote:
            quantidade = min(1.0, float(lote['saldo_atual'] or 0))
            saldo_anterior = float(lote['saldo_atual'] or 0)
            novo_saldo = round(saldo_anterior - quantidade, 3)
            cursor.execute("UPDATE estoque_produto_acabado SET saldo_atual=? WHERE id=?", (novo_saldo, int(lote['id'])))
            produto_final_id = lote['produto_final_id']
            if produto_final_id:
                cursor.execute("""
                    UPDATE produtos_finais
                    SET estoque_atual = CASE
                            WHEN COALESCE(estoque_atual, 0) - ? < 0 THEN 0
                            ELSE ROUND(COALESCE(estoque_atual, 0) - ?, 3)
                        END,
                        updated_at = ?
                    WHERE id = ?
                """, (quantidade, quantidade, agora, int(produto_final_id)))
            observacao = f'{nome}: {acao.replace("_", " ")} simulada na Etapa 6.4.'
            cursor.execute("""
                INSERT INTO movimentacoes_produto_acabado (
                    estoque_id, produto_final_id, produto_final_nome, lote, tipo,
                    quantidade, saldo_anterior, saldo_atual, observacao, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                int(lote['id']), int(produto_final_id) if produto_final_id else None,
                lote['produto_final_nome'], lote['lote'], 'VENDA', quantidade,
                saldo_anterior, novo_saldo, observacao, agora
            ))
            registrar_operacao('venda_importada', 'estoque_produto_acabado', int(lote['id']), quantidade, 0, observacao)
            return f'Pedido/venda simulada importada: baixa de {quantidade:.3f} un do lote {lote["lote"]}. Estoque atualizado internamente.'
        produto = produto_modelo()
        if produto:
            valor = float(produto['preco_venda'] or 0)
            mensagem = f'Pedido simulado recebido para {produto["nome"]}; não houve baixa porque não existe lote com saldo.'
            registrar_operacao('pedido_sem_baixa', 'produtos_finais', int(produto['id']), 1, valor, mensagem)
            return mensagem
        mensagem = 'Nenhum produto/lote disponível para simular importação de pedido.'
        registrar_operacao('sem_dados', '', None, 0, 0, mensagem)
        return mensagem

    if acao in ('atualizar_estoque', 'pausar_itens_sem_estoque'):
        baixos = contar("SELECT COUNT(*) FROM produtos_finais WHERE COALESCE(ativo,1)=1 AND COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0)")
        mensagem = f'Estoque conferido internamente: {produtos} produtos, {ingredientes} ingredientes e {lotes} lotes com saldo. {baixos} produto(s) abaixo ou no mínimo.'
        registrar_operacao('conferencia_estoque', 'estoque', None, baixos, 0, mensagem)
        return mensagem

    if acao in ('enviar_produtos', 'sincronizar_anuncios', 'sincronizar_cardapio'):
        base = produtos if acao != 'sincronizar_cardapio' else receitas
        entidade = 'produtos_finais' if acao != 'sincronizar_cardapio' else 'receitas'
        mensagem = f'{base} item(ns) preparados na fila interna para envio/sincronização. Nenhuma API externa foi acionada.'
        registrar_operacao('fila_envio', entidade, None, base, 0, mensagem)
        return mensagem

    if acao in ('enviar_alerta', 'enviar_lista_compras', 'enviar_pedido'):
        if acao == 'enviar_lista_compras':
            pendentes = contar("SELECT COUNT(*) FROM ingredientes WHERE COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0)")
            mensagem = f'Mensagem de WhatsApp preparada com lista de compras: {pendentes} ingrediente(s) no mínimo.'
        elif acao == 'enviar_pedido':
            mensagem = 'Mensagem de pedido preparada para WhatsApp com dados internos do ERP.'
        else:
            mensagem = 'Alerta operacional preparado para WhatsApp com base nos indicadores internos.'
        registrar_operacao('mensagem_whatsapp', 'mensagens', None, 1, 0, mensagem)
        return mensagem

    if acao in ('gerar_cobranca', 'registrar_pagamento'):
        valor = 0
        cursor.execute("SELECT COALESCE(SUM(preco_venda),0) FROM produtos_finais WHERE COALESCE(ativo,1)=1")
        linha = cursor.fetchone()
        valor = round(float((linha[0] if linha else 0) or 0), 2)
        mensagem = 'Cobrança PIX simulada registrada internamente.' if acao == 'gerar_cobranca' else 'Pagamento PIX simulado registrado para conciliação futura.'
        registrar_operacao('pix', 'financeiro', None, 1, valor, mensagem)
        return f'{mensagem} Valor de referência: R$ {valor:.2f}.'

    if acao in ('validar_dados_fiscais', 'preparar_emissao'):
        mensagem = 'Dados fiscais preparados internamente para NFC-e/SAT; nenhuma nota foi transmitida.'
        registrar_operacao('fiscal', 'documentos_fiscais', None, 1, 0, mensagem)
        return mensagem

    if acao in ('imprimir_teste', 'imprimir_etiquetas', 'imprimir_pedidos'):
        quantidade = produtos if acao == 'imprimir_etiquetas' else 1
        mensagem = f'Fila de impressão interna criada: {quantidade} item(ns). Nenhuma impressora externa foi acionada.'
        registrar_operacao('fila_impressao', 'impressao', None, quantidade, 0, mensagem)
        return mensagem

    mensagem = 'Sincronização interna executada e registrada em modo seguro.'
    registrar_operacao('sincronizacao', '', None, 1, 0, mensagem)
    return mensagem



def registrar_lancamento_financeiro(cursor, tipo, descricao, valor, categoria='Geral', origem='Manual', origem_id=None, status='Pendente', forma_pagamento='', observacao='', canal='ERP'):
    """Registra um lançamento financeiro sem duplicar lançamentos automáticos da mesma origem."""
    tipo = str(tipo or 'receita').lower().strip()
    if tipo not in ('receita', 'despesa'):
        tipo = 'receita'
    valor = abs(float(valor or 0))
    if valor <= 0:
        return None
    agora = agora_brasilia()
    if origem_id is not None and origem != 'Manual':
        cursor.execute("""
            SELECT id FROM financeiro_lancamentos
            WHERE origem = ? AND origem_id = ? AND tipo = ? AND descricao = ?
            LIMIT 1
        """, (origem, int(origem_id), tipo, descricao))
        existe = cursor.fetchone()
        if existe:
            return int(existe['id'])
    cursor.execute("""
        INSERT INTO financeiro_lancamentos (
            tipo, descricao, categoria, origem, origem_id, canal, valor,
            data_emissao, data_vencimento, data_pagamento, status,
            forma_pagamento, observacao, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tipo, descricao, categoria or 'Geral', origem or 'Manual', origem_id, canal or 'ERP', valor,
        agora[:10], agora[:10], agora[:10] if status in ('Pago', 'Recebido') else None,
        status or 'Pendente', forma_pagamento or '', observacao or '', agora, agora
    ))
    return cursor.lastrowid


def sincronizar_financeiro_com_vendas(cursor):
    """Garante que vendas do motor ERP apareçam no financeiro sem duplicidade."""
    cursor.execute("SELECT * FROM vendas_erp ORDER BY id DESC LIMIT 500")
    vendas = cursor.fetchall()
    for venda in vendas:
        venda_id = int(venda['id'])
        produto = venda['produto_final_nome'] or 'Produto acabado'
        valor = float(venda['valor_total'] or 0)
        cmv = float(venda['cmv_total'] or 0)
        origem = venda['origem'] or 'ERP'
        if valor > 0:
            registrar_lancamento_financeiro(cursor, 'receita', f'Venda - {produto}', valor, 'Vendas', origem, venda_id, 'Recebido', 'Automação ERP', venda['observacao'] or '', origem)
        if cmv > 0:
            registrar_lancamento_financeiro(cursor, 'despesa', f'CMV - {produto}', cmv, 'CMV', origem, venda_id, 'Pago', 'Automação ERP', f'CMV vinculado à venda #{venda_id}', origem)


def resumo_financeiro(cursor):
    sincronizar_financeiro_com_vendas(cursor)
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN tipo='receita' AND status IN ('Recebido','Pago') THEN valor ELSE 0 END),0) AS receitas_realizadas,
            COALESCE(SUM(CASE WHEN tipo='despesa' AND status IN ('Pago','Recebido') THEN valor ELSE 0 END),0) AS despesas_realizadas,
            COALESCE(SUM(CASE WHEN tipo='receita' AND status NOT IN ('Recebido','Pago','Cancelado') THEN valor ELSE 0 END),0) AS contas_receber,
            COALESCE(SUM(CASE WHEN tipo='despesa' AND status NOT IN ('Pago','Recebido','Cancelado') THEN valor ELSE 0 END),0) AS contas_pagar
        FROM financeiro_lancamentos
    """)
    base = dict(cursor.fetchone() or {})
    receitas = float(base.get('receitas_realizadas') or 0)
    despesas = float(base.get('despesas_realizadas') or 0)
    receber = float(base.get('contas_receber') or 0)
    pagar = float(base.get('contas_pagar') or 0)
    lucro = receitas - despesas
    margem = (lucro / receitas * 100) if receitas > 0 else 0
    cursor.execute("SELECT COUNT(*) AS total FROM financeiro_lancamentos")
    total = int((cursor.fetchone() or {'total': 0})['total'] or 0)
    cursor.execute("""
        SELECT categoria, tipo, COALESCE(SUM(valor),0) AS valor
        FROM financeiro_lancamentos
        WHERE status != 'Cancelado'
        GROUP BY categoria, tipo
        ORDER BY valor DESC
        LIMIT 12
    """)
    categorias = [dict(linha) for linha in cursor.fetchall()]
    return {
        'receitas_realizadas': round(receitas, 2),
        'despesas_realizadas': round(despesas, 2),
        'resultado': round(lucro, 2),
        'margem_liquida': round(margem, 2),
        'contas_receber': round(receber, 2),
        'contas_pagar': round(pagar, 2),
        'saldo_previsto': round(lucro + receber - pagar, 2),
        'lancamentos': total,
        'categorias': categorias
    }


def registrar_venda_erp_automatica(cursor, origem='ERP', quantidade=1.0, observacao=''):
    """Motor de Automação: registra venda interna e baixa produto acabado por lote."""
    cursor.execute("""
        SELECT e.*, pf.preco_venda
        FROM estoque_produto_acabado e
        LEFT JOIN produtos_finais pf ON pf.id = e.produto_final_id
        WHERE COALESCE(e.saldo_atual, 0) > 0
        ORDER BY e.id ASC
        LIMIT 1
    """)
    lote = cursor.fetchone()
    if not lote:
        registrar_evento_automacao(cursor, origem, 'venda', 'estoque_produto_acabado', None, 'bloqueado', 'Venda não realizada: não há lote com saldo disponível.')
        return {'status': 'bloqueado', 'mensagem': 'Não há lote com saldo disponível para venda automática.'}

    quantidade = min(float(quantidade or 1), float(lote['saldo_atual'] or 0))
    saldo_anterior = float(lote['saldo_atual'] or 0)
    novo_saldo = round(saldo_anterior - quantidade, 3)
    valor_unitario = float(lote['preco_venda'] or 0)
    valor_total = round(valor_unitario * quantidade, 2)

    custo_unitario = 0.0
    if lote['producao_id']:
        cursor.execute("SELECT custo_unitario FROM producoes WHERE id=?", (lote['producao_id'],))
        prod = cursor.fetchone()
        custo_unitario = float(prod['custo_unitario'] or 0) if prod else 0.0
    cmv_total = round(custo_unitario * quantidade, 2)
    lucro_estimado = round(valor_total - cmv_total, 2)

    cursor.execute("UPDATE estoque_produto_acabado SET saldo_atual=? WHERE id=?", (novo_saldo, int(lote['id'])))
    if lote['produto_final_id']:
        cursor.execute("""
            UPDATE produtos_finais
            SET estoque_atual = CASE WHEN COALESCE(estoque_atual,0) - ? < 0 THEN 0 ELSE ROUND(COALESCE(estoque_atual,0) - ?, 3) END,
                updated_at = ?
            WHERE id = ?
        """, (quantidade, quantidade, agora_brasilia(), int(lote['produto_final_id'])))

    cursor.execute("""
        INSERT INTO movimentacoes_produto_acabado (
            estoque_id, produto_final_id, produto_final_nome, lote, tipo,
            quantidade, saldo_anterior, saldo_atual, observacao, created_at
        ) VALUES (?, ?, ?, ?, 'VENDA_AUTO', ?, ?, ?, ?, ?)
    """, (int(lote['id']), int(lote['produto_final_id']) if lote['produto_final_id'] else None, lote['produto_final_nome'], lote['lote'], quantidade, saldo_anterior, novo_saldo, observacao or f'{origem}: venda automática pelo motor do ERP.', agora_brasilia()))

    cursor.execute("""
        INSERT INTO vendas_erp (
            origem, produto_final_id, produto_final_nome, estoque_id, lote, quantidade,
            valor_unitario, valor_total, custo_unitario, cmv_total, lucro_estimado, observacao, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (origem, int(lote['produto_final_id']) if lote['produto_final_id'] else None, lote['produto_final_nome'], int(lote['id']), lote['lote'], quantidade, valor_unitario, valor_total, custo_unitario, cmv_total, lucro_estimado, observacao, agora_brasilia()))

    venda_id = cursor.lastrowid
    registrar_lancamento_financeiro(cursor, 'receita', f'Venda automática - {lote["produto_final_nome"]}', valor_total, 'Vendas', origem, venda_id, 'Recebido', 'Automação ERP', observacao or f'Venda do lote {lote["lote"]}')
    if cmv_total > 0:
        registrar_lancamento_financeiro(cursor, 'despesa', f'CMV da venda - {lote["produto_final_nome"]}', cmv_total, 'CMV', origem, venda_id, 'Pago', 'Automação ERP', f'Custo estimado vinculado à venda do lote {lote["lote"]}')
    mensagem = f'Venda automática registrada: {quantidade:.3f} un de {lote["produto_final_nome"]}. CMV R$ {cmv_total:.2f}, lucro estimado R$ {lucro_estimado:.2f}.'
    registrar_evento_automacao(cursor, origem, 'venda', 'vendas_erp', venda_id, 'sucesso', mensagem)
    return {'status': 'sucesso', 'mensagem': mensagem}


def registrar_compras_automaticas(cursor):
    """Motor de Automação: repõe ingredientes abaixo do mínimo usando quantidade_compra."""
    cursor.execute("""
        SELECT id, nome, unidade, estoque_atual, estoque_minimo, quantidade_compra, preco_compra
        FROM ingredientes
        WHERE COALESCE(estoque_minimo,0) > 0
          AND COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0)
        ORDER BY nome ASC
    """)
    itens = cursor.fetchall()
    total = 0
    valor_total = 0.0
    for item in itens:
        atual = float(item['estoque_atual'] or 0)
        minimo = float(item['estoque_minimo'] or 0)
        quantidade_compra = float(item['quantidade_compra'] or 0)
        comprar = quantidade_compra if quantidade_compra > 0 else max(minimo - atual, minimo)
        if comprar <= 0:
            continue
        novo = round(atual + comprar, 3)
        valor = round(comprar * float(item['preco_compra'] or 0), 2)
        valor_total += valor
        cursor.execute("UPDATE ingredientes SET estoque_atual=?, updated_at=? WHERE id=?", (novo, agora_brasilia(), int(item['id'])))
        cursor.execute("""
            INSERT INTO movimentacoes_estoque (ingrediente_id, ingrediente_nome, tipo, quantidade, unidade, observacao, created_at)
            VALUES (?, ?, 'COMPRA_AUTO', ?, ?, ?, ?)
        """, (int(item['id']), item['nome'], comprar, item['unidade'], f'Compra automática pelo motor do ERP. Estoque anterior {atual:.3f}, novo saldo {novo:.3f}.', agora_brasilia()))
        registrar_evento_automacao(cursor, 'Compras', 'compra_automatica', 'ingredientes', int(item['id']), 'sucesso', f'{item["nome"]}: compra registrada de {comprar:.3f} {item["unidade"]}.')
        total += 1
    mensagem = f'Compra automática processada para {total} ingrediente(s). Valor estimado R$ {valor_total:.2f}.' if total else 'Nenhum ingrediente abaixo do mínimo para compra automática.'
    registrar_evento_automacao(cursor, 'Compras', 'lote_compras', 'ingredientes', None, 'sucesso', mensagem)
    return {'status': 'sucesso', 'mensagem': mensagem, 'total': total, 'valor_total': valor_total}


@app.route('/automacoes_status')
def automacoes_status():
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM ingredientes WHERE COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<=COALESCE(estoque_minimo,0)")
    ingredientes_baixo = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM produtos_finais WHERE COALESCE(ativo,1)=1 AND COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<=COALESCE(estoque_minimo,0)")
    produtos_baixo = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM vendas_erp")
    vendas = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COALESCE(SUM(valor_total),0) AS total FROM vendas_erp")
    faturamento = float(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COALESCE(SUM(cmv_total),0) AS total FROM vendas_erp")
    cmv = float(cursor.fetchone()['total'] or 0)
    cursor.execute("""
        SELECT id, origem, acao, entidade, status, mensagem, created_at
        FROM automacoes_eventos
        ORDER BY id DESC
        LIMIT 30
    """)
    eventos = [dict(x) for x in cursor.fetchall()]
    conn.close()
    return jsonify({
        'status': 'sucesso',
        'resumo': {
            'ingredientes_baixo_minimo': ingredientes_baixo,
            'produtos_baixo_minimo': produtos_baixo,
            'vendas_automatizadas': vendas,
            'faturamento_automatizado': round(faturamento, 2),
            'cmv_automatizado': round(cmv, 2),
            'lucro_estimado': round(faturamento - cmv, 2)
        },
        'eventos': eventos
    })


@app.route('/automacoes_executar', methods=['POST'])
def automacoes_executar():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    tipo = str(dados.get('tipo') or '').strip()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        if tipo == 'venda':
            resultado = registrar_venda_erp_automatica(cursor, 'Motor ERP', 1, 'Execução manual da Etapa 7.')
        elif tipo == 'compras':
            resultado = registrar_compras_automaticas(cursor)
        elif tipo == 'recalcular':
            total = recalcular_todas_receitas(cursor)
            registrar_evento_automacao(cursor, 'CMV', 'recalculo_receitas', 'receitas', None, 'sucesso', f'{total} receita(s) recalculada(s).')
            resultado = {'status': 'sucesso', 'mensagem': f'{total} receita(s) recalculada(s).'}
        else:
            conn.close()
            return jsonify({'status': 'erro', 'mensagem': 'Ação de automação inválida.'}), 400
        conn.commit()
        return jsonify(resultado)
    except Exception as exc:
        conn.rollback()
        try:
            registrar_evento_automacao(cursor, 'Motor ERP', tipo or 'indefinido', '', None, 'erro', str(exc))
            conn.commit()
        except Exception:
            pass
        return jsonify({'status': 'erro', 'mensagem': str(exc)}), 500
    finally:
        conn.close()

@app.route("/integracoes_status")
def integracoes_status():
    """Etapa 6.1: central visível de integrações com configuração local e teste interno, sem acionar APIs externas."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()

    def contar(sql, params=()):
        try:
            cursor.execute(sql, params)
            linha = cursor.fetchone()
            return int((linha[0] if linha else 0) or 0)
        except Exception:
            return 0

    def tabela_existe(nome):
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,))
            return cursor.fetchone() is not None
        except Exception:
            return False

    receitas = contar("SELECT COUNT(*) FROM receitas")
    ingredientes = contar("SELECT COUNT(*) FROM ingredientes")
    produtos = contar("SELECT COUNT(*) FROM produtos_finais WHERE COALESCE(ativo, 1) = 1")
    lotes_saldo = contar("SELECT COUNT(*) FROM lotes_producao WHERE COALESCE(saldo_atual, 0) > 0")
    movimentos_saida = contar("SELECT COUNT(*) FROM movimentacoes_produto_acabado WHERE LOWER(COALESCE(tipo, '')) IN ('saida', 'venda')")
    perfil_configurado = contar("SELECT COUNT(*) FROM perfil_empresa") if tabela_existe('perfil_empresa') else 0

    acoes_base = {
        'Bling': 'Enviar produtos, importar pedidos e atualizar estoque quando a API real for ativada.',
        'Mercado Livre': 'Sincronizar anúncios, preço e saldo disponível por SKU.',
        'Shopee': 'Sincronizar produtos, pedidos e baixa de estoque.',
        'iFood': 'Sincronizar cardápio, disponibilidade e pedidos.',
        'WhatsApp': 'Enviar orçamento, pedido, alerta e lista de compras.',
        'PIX': 'Registrar chave/recebedor para futura geração de cobrança.',
        'NFC-e / SAT': 'Preparar emissão fiscal com certificado, NCM/CFOP e tributação.',
        'Impressoras térmicas': 'Preparar impressão de pedido, etiqueta e comprovante.'
    }

    integracoes = []
    for cfg in carregar_configuracoes_integracoes(cursor):
        status_calc, obs_calc = avaliar_status_integracao(cfg)
        if cfg.get('status') != status_calc:
            cursor.execute("UPDATE integracoes_config SET status=?, observacao=?, updated_at=? WHERE id=?", (status_calc, obs_calc, agora_brasilia(), cfg['id']))
            cfg['status'] = status_calc
            cfg['observacao'] = obs_calc
        integracoes.append({
            'id': cfg['id'],
            'nome': cfg['nome'],
            'tipo': cfg.get('tipo') or '',
            'ambiente': cfg.get('ambiente') or 'sandbox',
            'loja': cfg.get('loja') or '',
            'usuario': cfg.get('usuario') or '',
            'endpoint': cfg.get('endpoint') or '',
            'api_key_mascara': mascarar_segredo(cfg.get('api_key')),
            'token_mascara': mascarar_segredo(cfg.get('token')),
            'chave_pix_mascara': mascarar_segredo(cfg.get('chave_pix')),
            'ativo': int(cfg.get('ativo') or 0) == 1,
            'status': cfg.get('status') or status_calc,
            'ultimo_teste': cfg.get('ultimo_teste') or '',
            'ultima_sincronizacao': cfg.get('ultima_sincronizacao') or '',
            'observacao': cfg.get('observacao') or obs_calc,
            'requisitos': requisitos_integracao(cfg.get('nome')),
            'acoes_disponiveis': acoes_disponiveis_integracao(cfg.get('nome')),
            'acao': acoes_base.get(cfg.get('nome'), 'Configuração local preparada.')
        })

    cursor.execute("SELECT * FROM integracoes_logs ORDER BY id DESC LIMIT 25")
    historico = [dict(linha) for linha in cursor.fetchall()]

    conn.commit()
    configuradas = sum(1 for item in integracoes if item['status'] in ('Configurado', 'Ativo'))
    ativas = sum(1 for item in integracoes if item['ativo'])
    conn.close()

    return jsonify({
        'status': 'sucesso',
        'resumo': {
            'integracoes_mapeadas': len(integracoes),
            'integracoes_configuradas': configuradas,
            'integracoes_ativas': ativas,
            'produtos_ativos': produtos,
            'ingredientes': ingredientes,
            'lotes_com_saldo': lotes_saldo,
            'saidas_registradas': movimentos_saida,
            'perfil_configurado': perfil_configurado > 0
        },
        'integracoes': integracoes,
        'historico': historico,
        'observacao': 'Etapa 6.4 executa impactos internos simulados e rastreáveis no ERP. Nenhuma API externa é acionada ainda.'
    })


@app.route("/integracoes_configurar", methods=["POST"])
def integracoes_configurar():
    """Salva configuração local da integração sem chamar serviços externos."""
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get('nome') or '').strip()
    if not nome:
        return jsonify({'status': 'erro', 'mensagem': 'Integração não informada.'}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM integracoes_config WHERE nome=?", (nome,))
    atual = cursor.fetchone()
    if not atual:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Integração não encontrada.'}), 404

    campos = {
        'ambiente': str(dados.get('ambiente') or 'sandbox').strip() or 'sandbox',
        'loja': str(dados.get('loja') or '').strip(),
        'usuario': str(dados.get('usuario') or '').strip(),
        'endpoint': str(dados.get('endpoint') or '').strip(),
        'ativo': 1 if dados.get('ativo') in (True, 1, '1', 'true', 'on', 'sim') else 0,
    }
    for segredo in ('api_key', 'token', 'chave_pix'):
        valor = str(dados.get(segredo) or '').strip()
        if valor:
            campos[segredo] = valor

    cfg_temp = dict(atual)
    cfg_temp.update(campos)
    status_calc, obs_calc = avaliar_status_integracao(cfg_temp)
    campos['status'] = status_calc
    campos['observacao'] = obs_calc
    campos['updated_at'] = agora_brasilia()

    set_sql = ', '.join([f"{campo}=?" for campo in campos])
    valores = list(campos.values()) + [nome]
    cursor.execute(f"UPDATE integracoes_config SET {set_sql} WHERE nome=?", valores)
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': f'Configuração de {nome} salva.', 'novo_status': status_calc})


@app.route("/integracoes_testar", methods=["POST"])
def integracoes_testar():
    """Teste interno de prontidão. Não aciona API externa nesta etapa."""
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get('nome') or '').strip()
    if not nome:
        return jsonify({'status': 'erro', 'mensagem': 'Integração não informada.'}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM integracoes_config WHERE nome=?", (nome,))
    linha = cursor.fetchone()
    if not linha:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Integração não encontrada.'}), 404

    cfg = dict(linha)
    status_calc, obs_calc = avaliar_status_integracao(cfg)
    sucesso = status_calc == 'Ativo'
    mensagem = 'Teste interno aprovado. Integração pronta para receber conector real.' if sucesso else obs_calc
    cursor.execute("""
        UPDATE integracoes_config
        SET status=?, observacao=?, ultimo_teste=?, updated_at=?
        WHERE nome=?
    """, (status_calc, mensagem, agora_brasilia(), agora_brasilia(), nome))
    registrar_integracao_log(cursor, nome, 'teste_conexao', 'sucesso' if sucesso else 'pendente', mensagem)
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso' if sucesso else 'pendente', 'mensagem': mensagem, 'novo_status': status_calc})


@app.route("/integracoes_executar", methods=["POST"])
def integracoes_executar():
    """Etapa 6.4: executa impactos internos simulados e rastreáveis, sem acionar APIs externas."""
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get('nome') or '').strip()
    acao = str(dados.get('acao') or '').strip()
    if not nome or not acao:
        return jsonify({'status': 'erro', 'mensagem': 'Integração e ação são obrigatórias.'}), 400

    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM integracoes_config WHERE nome=?", (nome,))
    linha = cursor.fetchone()
    if not linha:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Integração não encontrada.'}), 404

    cfg = dict(linha)
    status_calc, obs_calc = avaliar_status_integracao(cfg)
    if status_calc != 'Ativo':
        mensagem = f'Ação bloqueada: {obs_calc}'
        registrar_integracao_log(cursor, nome, acao, 'bloqueado', mensagem)
        conn.commit()
        conn.close()
        return jsonify({'status': 'bloqueado', 'mensagem': mensagem}), 400

    permitidas = {item['codigo'] for item in acoes_disponiveis_integracao(nome)}
    if acao not in permitidas:
        mensagem = 'Ação não disponível para esta integração.'
        registrar_integracao_log(cursor, nome, acao, 'erro', mensagem)
        conn.commit()
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': mensagem}), 400

    mensagem = simular_execucao_integracao(cursor, nome, acao)
    agora = agora_brasilia()
    cursor.execute("""
        UPDATE integracoes_config
        SET ultima_sincronizacao=?, observacao=?, updated_at=?
        WHERE nome=?
    """, (agora, mensagem, agora, nome))
    registrar_integracao_log(cursor, nome, acao, 'sucesso', mensagem, 'Etapa 6.4: impacto interno simulado/rastreável no ERP')
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': mensagem, 'ultima_sincronizacao': agora})



@app.route('/financeiro_status')
def financeiro_status():
    """Etapa 8: módulo financeiro completo, conectado às vendas automáticas e lançamentos manuais."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    resumo = resumo_financeiro(cursor)
    conn.commit()
    cursor.execute("""
        SELECT * FROM financeiro_lancamentos
        ORDER BY COALESCE(data_vencimento, data_emissao) DESC, id DESC
        LIMIT 60
    """)
    lancamentos = [dict(linha) for linha in cursor.fetchall()]
    cursor.execute("""
        SELECT status, COUNT(*) AS total, COALESCE(SUM(valor),0) AS valor
        FROM financeiro_lancamentos
        GROUP BY status
        ORDER BY valor DESC
    """)
    status_lista = [dict(linha) for linha in cursor.fetchall()]
    conn.close()
    return jsonify({'status': 'sucesso', 'resumo': resumo, 'lancamentos': lancamentos, 'status_lista': status_lista})


@app.route('/financeiro_contas/<tipo>')
def financeiro_contas_tipo(tipo):
    """Retorna contas a pagar ou a receber separadas, sem reaproveitar a tela financeira geral."""
    criar_tabelas()
    tipo = (tipo or '').lower().strip()
    if tipo not in ('pagar', 'receber'):
        return jsonify({'status': 'erro', 'mensagem': 'Tipo inválido.'}), 400
    tipo_lancamento = 'despesa' if tipo == 'pagar' else 'receita'
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM financeiro_lancamentos
        WHERE LOWER(tipo) = ?
        ORDER BY COALESCE(data_vencimento, data_emissao) DESC, id DESC
        LIMIT 500
    """, (tipo_lancamento,))
    lancamentos = [dict(linha) for linha in cursor.fetchall()]
    resumo = {
        'total': sum(float(item.get('valor') or 0) for item in lancamentos),
        'pendente': sum(float(item.get('valor') or 0) for item in lancamentos if str(item.get('status') or '').lower() == 'pendente'),
        'vencido': sum(float(item.get('valor') or 0) for item in lancamentos if str(item.get('status') or '').lower() == 'vencido'),
        'pago': sum(float(item.get('valor') or 0) for item in lancamentos if str(item.get('status') or '').lower() == 'pago'),
        'recebido': sum(float(item.get('valor') or 0) for item in lancamentos if str(item.get('status') or '').lower() == 'recebido'),
        'quantidade': len(lancamentos)
    }
    conn.close()
    return jsonify({'status': 'sucesso', 'tipo': tipo, 'resumo': resumo, 'lancamentos': lancamentos})



def _moeda_api(valor):
    try:
        return round(float(valor or 0), 2)
    except Exception:
        return 0.0


def _status_em_aberto(status):
    return str(status or '').strip().lower() not in ('pago', 'recebido', 'cancelado')


def _financeiro_submodulo_payload(cursor, modulo):
    """Dados reais e separados para cada submenu financeiro, sem reaproveitar a página geral."""
    modulo = (modulo or '').strip().lower()
    hoje = hoje_brasilia()
    cursor.execute("SELECT * FROM financeiro_lancamentos WHERE status != 'Cancelado' ORDER BY COALESCE(data_vencimento, data_emissao) DESC, id DESC LIMIT 800")
    lancamentos = [dict(l) for l in cursor.fetchall()]
    receitas = [l for l in lancamentos if str(l.get('tipo') or '').lower() == 'receita']
    despesas = [l for l in lancamentos if str(l.get('tipo') or '').lower() == 'despesa']
    abertos_receber = [l for l in receitas if _status_em_aberto(l.get('status'))]
    abertos_pagar = [l for l in despesas if _status_em_aberto(l.get('status'))]
    pagos = [l for l in lancamentos if str(l.get('status') or '').lower() in ('pago', 'recebido')]

    def soma(lista): return _moeda_api(sum(float(x.get('valor') or 0) for x in lista))
    def vencidos(lista): return [x for x in lista if (x.get('data_vencimento') or x.get('data_emissao') or '9999-99-99') < hoje and _status_em_aberto(x.get('status'))]
    def linhas(lista, limite=18):
        return [[x.get('descricao') or '-', x.get('status') or x.get('tipo') or '-', f"R$ {_moeda_api(x.get('valor')):.2f}"] for x in lista[:limite]]
    def por_categoria(lista):
        grupos = {}
        for x in lista:
            chave = x.get('categoria') or 'Sem categoria'
            grupos[chave] = grupos.get(chave, 0) + float(x.get('valor') or 0)
        return [[k, 'Total', f"R$ {_moeda_api(v):.2f}"] for k, v in sorted(grupos.items(), key=lambda kv: kv[1], reverse=True)[:18]]
    def por_forma(lista):
        grupos = {}
        for x in lista:
            chave = x.get('forma_pagamento') or 'Não informado'
            grupos[chave] = grupos.get(chave, 0) + float(x.get('valor') or 0)
        return [[k, 'Uso financeiro', f"R$ {_moeda_api(v):.2f}"] for k, v in sorted(grupos.items(), key=lambda kv: kv[1], reverse=True)[:18]]

    if modulo == 'caixas-bancos':
        entradas = soma([l for l in pagos if str(l.get('tipo')).lower() == 'receita'])
        saidas = soma([l for l in pagos if str(l.get('tipo')).lower() == 'despesa'])
        return {'titulo':'Caixas e bancos','resumo':{'kpi1':1,'kpi2':entradas-saidas,'kpi3':entradas,'kpi4':saidas},'linhas1':[['Caixa principal','Saldo realizado',f'R$ {entradas-saidas:.2f}'],['Banco/Conta digital','Previsto',f'R$ {soma(abertos_receber)-soma(abertos_pagar):.2f}']], 'linhas2':linhas(pagos)}
    if modulo == 'contas-financeiras':
        return {'titulo':'Contas financeiras','resumo':{'kpi1':2,'kpi2':soma(receitas)-soma(despesas),'kpi3':soma(receitas),'kpi4':soma(despesas)},'linhas1':[['Caixa principal','Ativo',f'R$ {soma(receitas)-soma(despesas):.2f}'],['Conta ERP','Previsto',f'R$ {soma(abertos_receber)-soma(abertos_pagar):.2f}']], 'linhas2':linhas(lancamentos)}
    if modulo == 'categorias-financeiras':
        return {'titulo':'Categorias financeiras','resumo':{'kpi1':len({l.get('categoria') for l in lancamentos if l.get('categoria')}),'kpi2':soma(receitas),'kpi3':soma(despesas),'kpi4':soma(receitas)-soma(despesas)},'linhas1':por_categoria(lancamentos),'linhas2':linhas(lancamentos)}
    if modulo == 'formas-pagamento':
        pend = len([l for l in lancamentos if _status_em_aberto(l.get('status'))])
        return {'titulo':'Formas de pagamento','resumo':{'kpi1':len({l.get('forma_pagamento') for l in lancamentos if l.get('forma_pagamento')}),'kpi2':soma(receitas),'kpi3':soma(despesas),'kpi4':pend},'linhas1':por_forma(lancamentos),'linhas2':linhas(lancamentos)}
    if modulo == 'cobrancas':
        return {'titulo':'Cobranças','resumo':{'kpi1':len(abertos_receber),'kpi2':soma(abertos_receber),'kpi3':soma(vencidos(abertos_receber)),'kpi4':soma([l for l in receitas if str(l.get('status')).lower()=='recebido'])},'linhas1':linhas(abertos_receber),'linhas2':linhas(vencidos(abertos_receber))}
    if modulo == 'remessas-retornos':
        return {'titulo':'Remessas e retornos','resumo':{'kpi1':0,'kpi2':0,'kpi3':len(abertos_receber)+len(abertos_pagar),'kpi4':len(pagos)},'linhas1':[['Aguardando integração bancária','Preparado','0 arquivo']], 'linhas2':linhas(lancamentos)}
    if modulo == 'ficha-financeira':
        return {'titulo':'Ficha financeira','resumo':{'kpi1':len(lancamentos),'kpi2':soma(receitas),'kpi3':soma(despesas),'kpi4':soma(receitas)-soma(despesas)},'linhas1':linhas(lancamentos,25),'linhas2':por_categoria(lancamentos)}
    if modulo == 'comissoes':
        cursor.execute("SELECT COUNT(*) AS total, COALESCE(SUM(valor_total),0) AS valor FROM vendas_erp")
        v = dict(cursor.fetchone() or {})
        base = float(v.get('valor') or 0)
        comissao = round(base * 0.03, 2)
        return {'titulo':'Comissões','resumo':{'kpi1':comissao,'kpi2':int(v.get('total') or 0),'kpi3':comissao,'kpi4':0},'linhas1':[['Comissão padrão 3%','A pagar',f'R$ {comissao:.2f}']], 'linhas2':[['Vendas vinculadas','Base de cálculo',f'R$ {base:.2f}']]}
    if modulo == 'controle-caixa':
        entradas = soma([l for l in receitas if str(l.get('status')).lower()=='recebido'])
        saidas = soma([l for l in despesas if str(l.get('status')).lower()=='pago'])
        return {'titulo':'Controle de caixa','resumo':{'kpi1':entradas-saidas,'kpi2':entradas,'kpi3':saidas,'kpi4':0},'linhas1':[['Caixa do dia','Saldo',f'R$ {entradas-saidas:.2f}']], 'linhas2':linhas(pagos)}
    if modulo == 'faturamento-agrupado':
        return {'titulo':'Faturamento agrupado','resumo':{'kpi1':len(receitas),'kpi2':len(receitas),'kpi3':soma(receitas),'kpi4':len(abertos_receber)},'linhas1':por_categoria(receitas),'linhas2':linhas(receitas)}
    if modulo in ('das-mei','gnre-dare','espaco-contador'):
        titulo = {'das-mei':'DAS MEI','gnre-dare':'GNRE e DARE-SP','espaco-contador':'Espaço meu contador'}[modulo]
        return {'titulo':titulo,'resumo':{'kpi1':0,'kpi2':len(abertos_pagar),'kpi3':len([l for l in despesas if str(l.get('status')).lower()=='pago']),'kpi4':soma(despesas)},'linhas1':[[titulo,'Sem guia cadastrada','Criar etapa fiscal']], 'linhas2':por_categoria(despesas)}
    return {'titulo':'Financeiro','resumo':{'kpi1':len(lancamentos),'kpi2':soma(receitas),'kpi3':soma(despesas),'kpi4':soma(receitas)-soma(despesas)},'linhas1':linhas(lancamentos),'linhas2':por_categoria(lancamentos)}


@app.route('/financeiro_submodulo/<modulo>')
def financeiro_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    sincronizar_financeiro_com_vendas(cursor)
    payload = _financeiro_submodulo_payload(cursor, modulo)
    conn.commit()
    conn.close()
    payload['status'] = 'sucesso'
    payload['modulo'] = modulo
    return jsonify(payload)


@app.route('/financeiro_lancamento', methods=['POST'])
def financeiro_lancamento():
    """Cria receita/despesa manual sem interferir nos lançamentos automáticos."""
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    descricao = str(dados.get('descricao') or '').strip()
    tipo = str(dados.get('tipo') or 'receita').lower().strip()
    valor = normalizar_float(dados.get('valor'), 0)
    if not descricao or valor <= 0:
        return jsonify({'status': 'erro', 'mensagem': 'Descrição e valor são obrigatórios.'}), 400
    status = str(dados.get('status') or ('Recebido' if tipo == 'receita' else 'Pago')).strip()
    categoria = str(dados.get('categoria') or ('Vendas' if tipo == 'receita' else 'Despesas')).strip()
    forma = str(dados.get('forma_pagamento') or '').strip()
    observacao = str(dados.get('observacao') or '').strip()
    vencimento = str(dados.get('data_vencimento') or '').strip() or data_brasilia_obj().isoformat()
    conn = conectar_banco()
    cursor = conn.cursor()
    agora = agora_brasilia()
    cursor.execute("""
        INSERT INTO financeiro_lancamentos (
            tipo, descricao, categoria, origem, canal, valor, data_emissao,
            data_vencimento, data_pagamento, status, forma_pagamento, observacao, created_at, updated_at
        ) VALUES (?, ?, ?, 'Manual', 'ERP', ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tipo if tipo in ('receita','despesa') else 'receita', descricao, categoria, valor,
        agora[:10], vencimento, agora[:10] if status in ('Pago','Recebido') else None,
        status, forma, observacao, agora, agora
    ))
    conn.commit()
    lancamento_id = cursor.lastrowid
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': 'Lançamento financeiro salvo.', 'id': lancamento_id})


@app.route('/financeiro_atualizar_status', methods=['POST'])
def financeiro_atualizar_status():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    lancamento_id = int(dados.get('id') or 0)
    status = str(dados.get('status') or '').strip()
    if not lancamento_id or not status:
        return jsonify({'status': 'erro', 'mensagem': 'Lançamento e status são obrigatórios.'}), 400
    conn = conectar_banco()
    cursor = conn.cursor()
    agora = agora_brasilia()
    data_pagamento = agora[:10] if status in ('Pago','Recebido') else None
    cursor.execute("""
        UPDATE financeiro_lancamentos
        SET status=?, data_pagamento=?, updated_at=?
        WHERE id=?
    """, (status, data_pagamento, agora, lancamento_id))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': 'Status financeiro atualizado.'})



def erp_garantir_conta_pagar_compra(cursor, pedido_id):
    """Cria ou atualiza a conta a pagar vinculada a um pedido de compra, sem duplicar."""
    cursor.execute("SELECT * FROM compras_pedidos WHERE id=?", (int(pedido_id),))
    ped = cursor.fetchone()
    if not ped:
        return None
    valor_total = normalizar_float(ped['valor_total'] if 'valor_total' in ped.keys() else 0, 0)
    if valor_total <= 0:
        return None
    agora = agora_brasilia()
    vencimento = (ped['previsao_entrega'] if 'previsao_entrega' in ped.keys() and ped['previsao_entrega'] else agora[:10])
    fornecedor = (ped['fornecedor_nome'] if 'fornecedor_nome' in ped.keys() and ped['fornecedor_nome'] else (ped['fornecedor'] if 'fornecedor' in ped.keys() else 'Fornecedor'))
    ingrediente = ped['ingrediente_nome'] if 'ingrediente_nome' in ped.keys() else 'Ingrediente'
    descricao = f"Conta a pagar - Compra #{int(pedido_id)} - {fornecedor} - {ingrediente}"
    cursor.execute("""
        SELECT id FROM financeiro_lancamentos
        WHERE origem='Compras' AND origem_id=? AND tipo='despesa'
        ORDER BY id ASC LIMIT 1
    """, (int(pedido_id),))
    existente = cursor.fetchone()
    if existente:
        fid = int(existente['id'])
        cursor.execute("""
            UPDATE financeiro_lancamentos
            SET descricao=?, categoria='Compras / Fornecedor', canal='ERP', valor=?, data_vencimento=?,
                status=CASE WHEN status IN ('Pago','Recebido','Cancelado') THEN status ELSE 'Pendente' END,
                forma_pagamento=COALESCE(NULLIF(forma_pagamento,''),'A definir'),
                observacao=?, updated_at=?
            WHERE id=?
        """, (descricao, valor_total, vencimento, f"Gerado automaticamente pelo módulo Compras. Fornecedor: {fornecedor}.", agora, fid))
        return fid
    cursor.execute("""
        INSERT INTO financeiro_lancamentos (
            tipo, descricao, categoria, origem, origem_id, canal, valor, data_emissao,
            data_vencimento, status, forma_pagamento, observacao, created_at, updated_at
        ) VALUES ('despesa', ?, 'Compras / Fornecedor', 'Compras', ?, 'ERP', ?, ?, ?, 'Pendente', 'A definir', ?, ?, ?)
    """, (descricao, int(pedido_id), valor_total, agora[:10], vencimento, f"Gerado automaticamente pelo módulo Compras. Fornecedor: {fornecedor}.", agora, agora))
    return int(cursor.lastrowid)


def erp_sincronizar_compras_estoque_financeiro(cursor):
    """Sincroniza pedidos de compra com contas a pagar e recalcula impactos de custo/CMV."""
    if not _erp_tabela_existe(cursor, 'compras_pedidos'):
        return {'pedidos': 0, 'contas_pagar': 0, 'recebidos': 0}
    cursor.execute("SELECT id, status FROM compras_pedidos ORDER BY id DESC LIMIT 1000")
    pedidos = cursor.fetchall()
    contas = 0
    recebidos = 0
    for ped in pedidos:
        fid = erp_garantir_conta_pagar_compra(cursor, int(ped['id']))
        if fid:
            contas += 1
            cursor.execute("UPDATE compras_pedidos SET financeiro_id=COALESCE(financeiro_id, ?), updated_at=? WHERE id=?", (fid, agora_brasilia(), int(ped['id'])))
        if str(ped['status'] or '') == 'Recebido':
            recebidos += 1
    try:
        _erp_recalcular_cmv_todos(cursor)
    except Exception as exc:
        _erp_registrar_evento(cursor, 'CMV', 'recalculo_pos_sincronizacao_compras', status='erro', mensagem=str(exc))
    _erp_registrar_evento(cursor, 'Compras', 'sincronizar_estoque_financeiro', status='sucesso', mensagem=f'{len(pedidos)} pedido(s), {contas} conta(s) a pagar conferidas.')
    return {'pedidos': len(pedidos), 'contas_pagar': contas, 'recebidos': recebidos}


@app.route('/compras_status')
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


@app.route('/compras_criar_solicitacao', methods=['POST'])
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


@app.route('/compras_registrar_cotacao', methods=['POST'])
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


@app.route('/compras_gerar_pedido', methods=['POST'])
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


@app.route('/compras_receber_pedido', methods=['POST'])
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


@app.route('/compras_sincronizar_erp', methods=['POST'])
def compras_sincronizar_erp():
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    _erp_garantir_tabelas_base(cursor)
    resultado = erp_sincronizar_compras_estoque_financeiro(cursor)
    financeiro = resumo_financeiro(cursor)
    auditoria = _erp_auditoria_geral(cursor)
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Compras, contas a pagar, CMV e auditoria sincronizados.', 'resultado': resultado, 'financeiro': financeiro, 'auditoria': auditoria})


@app.route("/cmv_resumo")
def cmv_resumo():
    """CMV profissional: lê receitas e produtos finais ativos sem quebrar os vínculos existentes."""
    conn = conectar_banco()
    cursor = conn.cursor()

    recalcular_receitas_salvas(cursor)
    conn.commit()

    def precificacao(entidade_tipo, entidade_id):
        cursor.execute("""
            SELECT * FROM cmv_precificacao
            WHERE entidade_tipo = ? AND entidade_id = ?
        """, (entidade_tipo, int(entidade_id)))
        linha = cursor.fetchone()
        return dict(linha) if linha else {}

    def montar_item(entidade_tipo, entidade_id, nome, custo_base, preco_atual, estoque_atual=0, origem="Receita"):
        cfg = precificacao(entidade_tipo, entidade_id)
        embalagem = float(cfg.get("embalagem_unitaria") or 0)
        fixo = float(cfg.get("custo_fixo_unitario") or 0)
        variavel_pct = float(cfg.get("custo_variavel_percentual") or 0)
        margem_pct = float(cfg.get("margem_desejada_percentual") or 30)
        preco_praticado_cfg = float(cfg.get("preco_praticado") or 0)

        custo_base = normalizar_decimal_cmv(custo_base, 0)
        preco_venda = preco_praticado_cfg or normalizar_decimal_cmv(preco_atual, 0)
        calculo = calcular_precificacao_cmv(custo_base, embalagem, fixo, variavel_pct, margem_pct, preco_venda)
        custo_unitario = calculo["cmv_real"]
        preco_minimo = calculo["preco_minimo"]
        preco_sugerido = calculo["preco_sugerido"]
        lucro = calculo["lucro_real"]
        margem_real = calculo["margem_real_percentual"]
        markup = calculo["markup"]
        status = calculo["status"]

        # Mantém o banco coerente também ao carregar a tela: todos os campos
        # calculados ficam gravados e disponíveis para dashboard/relatórios.
        cursor.execute("""
            INSERT INTO cmv_precificacao (
                entidade_tipo, entidade_id, embalagem_unitaria, custo_fixo_unitario,
                custo_variavel_percentual, margem_desejada_percentual, preco_praticado,
                custo_base, cmv_real, preco_minimo, preco_sugerido, lucro_real,
                margem_real_percentual, markup, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entidade_tipo, entidade_id) DO UPDATE SET
                custo_base = excluded.custo_base,
                cmv_real = excluded.cmv_real,
                preco_minimo = excluded.preco_minimo,
                preco_sugerido = excluded.preco_sugerido,
                lucro_real = excluded.lucro_real,
                margem_real_percentual = excluded.margem_real_percentual,
                markup = excluded.markup,
                status = excluded.status,
                updated_at = excluded.updated_at
        """, (
            entidade_tipo, int(entidade_id), embalagem, fixo, variavel_pct, margem_pct,
            preco_venda, round(custo_base, 6), round(custo_unitario, 6), preco_minimo,
            preco_sugerido, round(lucro, 2), round(margem_real, 2), round(markup, 4),
            status, agora_brasilia()
        ))

        return {
            "tipo": entidade_tipo,
            "id": entidade_id,
            "origem": origem,
            "nome": nome,
            "custo_base": round(custo_base, 2),
            "embalagem_unitaria": round(embalagem, 2),
            "custo_fixo_unitario": round(fixo, 2),
            "custo_variavel_percentual": round(variavel_pct, 2),
            "custo_unitario": round(custo_unitario, 2),
            "preco_minimo": preco_minimo,
            "margem_desejada_percentual": round(margem_pct, 2),
            "preco_sugerido": preco_sugerido,
            "preco_venda": round(preco_venda, 2),
            "lucro_unitario": round(lucro, 2),
            "margem_percentual": round(margem_real, 1),
            "markup": round(markup, 2),
            "estoque_atual": estoque_atual or 0,
            "status": status
        }

    itens = []

    cursor.execute("""
        SELECT
            pf.id, pf.nome, pf.preco_venda, pf.estoque_atual,
            r.custo_porcao, r.custo_total, r.rendimento
        FROM produtos_finais pf
        LEFT JOIN receitas r ON r.id = pf.receita_id
        WHERE COALESCE(pf.ativo, 1) = 1
        ORDER BY pf.nome ASC
    """)
    for linha in cursor.fetchall():
        custo = float(linha["custo_porcao"] or 0)
        if custo <= 0:
            rendimento = float(linha["rendimento"] or 1) or 1
            custo = float(linha["custo_total"] or 0) / rendimento
        itens.append(montar_item(
            "produto", linha["id"], linha["nome"], custo, linha["preco_venda"], linha["estoque_atual"], "Produto final"
        ))

    # Inclui receitas que ainda não viraram produto final ativo, para não esconder fichas sem precificação.
    cursor.execute("""
        SELECT r.id, r.nome, r.custo_total, r.custo_porcao, r.rendimento, r.preco_venda
        FROM receitas r
        WHERE NOT EXISTS (
            SELECT 1 FROM produtos_finais pf
            WHERE pf.receita_id = r.id AND COALESCE(pf.ativo, 1) = 1
        )
        ORDER BY r.nome ASC
    """)
    for linha in cursor.fetchall():
        custo = float(linha["custo_porcao"] or 0)
        if custo <= 0:
            rendimento = float(linha["rendimento"] or 1) or 1
            custo = float(linha["custo_total"] or 0) / rendimento
        itens.append(montar_item(
            "receita", linha["id"], linha["nome"], custo, linha["preco_venda"], 0, "Receita"
        ))

    resumo = {
        "total_produtos": len(itens),
        "sem_preco": sum(1 for i in itens if i["status"] == "Sem preço"),
        "prejuizo": sum(1 for i in itens if i["status"] == "Prejuízo"),
        "atencao": sum(1 for i in itens if i["status"] == "Atenção"),
        "saudavel": sum(1 for i in itens if i["status"] == "Saudável"),
    }

    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso", "itens": itens, "resumo": resumo})


@app.route("/salvar_precificacao_cmv", methods=["POST"])
def salvar_precificacao_cmv():
    """Salva precificação usando a fórmula única oficial do ERP."""
    criar_tabelas()
    dados = request.json or {}
    entidade_tipo = str(dados.get("tipo", "")).strip().lower()
    entidade_id = dados.get("id")

    if entidade_tipo not in ["produto", "receita"] or not entidade_id:
        return jsonify({"status": "erro", "mensagem": "Item inválido para precificação."}), 400

    entidade_id = int(entidade_id)
    embalagem = normalizar_decimal_cmv(dados.get("embalagem_unitaria"), 0)
    fixo = normalizar_decimal_cmv(dados.get("custo_fixo_unitario"), 0)
    variavel = normalizar_decimal_cmv(dados.get("custo_variavel_percentual"), 0)
    margem = normalizar_decimal_cmv(dados.get("margem_desejada_percentual"), 30)
    preco = normalizar_decimal_cmv(dados.get("preco_venda"), 0)

    conn = conectar_banco()
    cursor = conn.cursor()
    base = obter_custo_base_entidade(cursor, entidade_tipo, entidade_id)
    if not base:
        conn.close()
        return jsonify({"status": "erro", "mensagem": "Item não encontrado para precificação."}), 404

    calculo = calcular_precificacao_cmv(
        base["custo_base"], embalagem, fixo, variavel, margem, preco
    )
    agora = salvar_calculo_cmv(cursor, entidade_tipo, entidade_id, calculo)

    if entidade_tipo == "produto":
        cursor.execute("UPDATE produtos_finais SET preco_venda = ?, updated_at = ? WHERE id = ?", (preco, agora, entidade_id))
    else:
        cursor.execute("""
            UPDATE receitas
            SET preco_venda = ?, preco_venda_porcao = ?, updated_at = ?
            WHERE id = ?
        """, (preco, round(preco / (base["rendimento"] or 1), 2), agora, entidade_id))

    conn.commit()
    conn.close()
    return jsonify({
        "status": "sucesso",
        "mensagem": "Precificação salva e recalculada no banco.",
        "calculo": {
            "custo_base": round(calculo["custo_base"], 2),
            "custo_unitario": round(calculo["cmv_real"], 2),
            "preco_minimo": calculo["preco_minimo"],
            "preco_sugerido": calculo["preco_sugerido"],
            "preco_venda": round(calculo["preco_praticado"], 2),
            "lucro_unitario": calculo["lucro_real"],
            "margem_percentual": calculo["margem_real_percentual"],
            "markup": calculo["markup"],
            "status": calculo["status"]
        }
    })


@app.route("/auditoria_banco_dados", methods=["POST"])
def auditoria_banco_dados():
    """Recalcula campos derivados essenciais sem apagar cadastros existentes."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    resumo = {"receitas_recalculadas": 0, "cmv_recalculados": 0, "financeiro_normalizado": 0}

    # Receitas: garante custo salvo antes de alimentar CMV e dashboard.
    try:
        recalcular_receitas_salvas(cursor)
        resumo["receitas_recalculadas"] = 1
    except Exception as exc:
        resumo["receitas_erro"] = str(exc)

    # CMV: recalcula todos os produtos finais e receitas com configuração preservada.
    cursor.execute("SELECT entidade_tipo, entidade_id, embalagem_unitaria, custo_fixo_unitario, custo_variavel_percentual, margem_desejada_percentual, preco_praticado FROM cmv_precificacao")
    configs = {(r["entidade_tipo"], int(r["entidade_id"])): dict(r) for r in cursor.fetchall()}

    entidades = []
    cursor.execute("SELECT id FROM produtos_finais WHERE COALESCE(ativo,1)=1")
    entidades += [("produto", int(r["id"])) for r in cursor.fetchall()]
    cursor.execute("SELECT id FROM receitas")
    entidades += [("receita", int(r["id"])) for r in cursor.fetchall()]

    for tipo, item_id in entidades:
        base = obter_custo_base_entidade(cursor, tipo, item_id)
        if not base:
            continue
        cfg = configs.get((tipo, item_id), {})
        preco = normalizar_decimal_cmv(cfg.get("preco_praticado"), 0) or base["preco_atual"]
        calculo = calcular_precificacao_cmv(
            base["custo_base"],
            cfg.get("embalagem_unitaria", 0),
            cfg.get("custo_fixo_unitario", 0),
            cfg.get("custo_variavel_percentual", 0),
            cfg.get("margem_desejada_percentual", 30),
            preco,
        )
        salvar_calculo_cmv(cursor, tipo, item_id, calculo, "Recalculado pela auditoria")
        resumo["cmv_recalculados"] += 1

    # Financeiro: padroniza valores nulos/status sem duplicar lançamentos.
    try:
        cursor.execute("UPDATE financeiro_lancamentos SET valor = COALESCE(valor,0), status = COALESCE(status,'Aberto')")
        resumo["financeiro_normalizado"] = cursor.rowcount if cursor.rowcount is not None else 0
    except Exception as exc:
        resumo["financeiro_erro"] = str(exc)

    conn.commit()
    conn.close()
    return jsonify({"status": "sucesso", "mensagem": "Auditoria executada com recálculo dos campos derivados.", "resumo": resumo})


def resumo_clientes(cursor):
    cursor.execute("SELECT COUNT(*) AS total FROM clientes")
    total = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM clientes WHERE status='Ativo'")
    ativos = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM clientes WHERE status<>'Ativo'")
    inativos_cadastro = int(cursor.fetchone()['total'] or 0)
    cursor.execute("""
        SELECT COALESCE(SUM(valor_total),0) AS faturamento, COUNT(*) AS vendas,
               COALESCE(AVG(valor_total),0) AS ticket
        FROM vendas_erp
        WHERE cliente_id IS NOT NULL
    """)
    v = cursor.fetchone()
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM clientes c
        WHERE NOT EXISTS (
            SELECT 1 FROM vendas_erp v
            WHERE v.cliente_id = c.id
              AND date(substr(v.created_at, 1, 10)) >= date('now','-60 day')
        )
    """)
    inativos_60 = int(cursor.fetchone()['total'] or 0)
    return {
        'total_clientes': total,
        'ativos': ativos,
        'inativos_cadastro': inativos_cadastro,
        'inativos_60_dias': inativos_60,
        'faturamento_clientes': round(float(v['faturamento'] or 0), 2),
        'vendas_clientes': int(v['vendas'] or 0),
        'ticket_medio': round(float(v['ticket'] or 0), 2)
    }


def montar_analise_clientes(cursor):
    cursor.execute("""
        SELECT c.*, COALESCE(SUM(v.valor_total),0) AS total_comprado,
               COUNT(v.id) AS quantidade_compras,
               COALESCE(AVG(v.valor_total),0) AS ticket_medio,
               MAX(substr(v.created_at, 1, 10)) AS ultima_compra
        FROM clientes c
        LEFT JOIN vendas_erp v ON v.cliente_id = c.id
        GROUP BY c.id
        ORDER BY c.nome COLLATE NOCASE
    """)
    clientes = [dict(row) for row in cursor.fetchall()]
    faturamento_total = sum(float(c.get('total_comprado') or 0) for c in clientes)
    acumulado = 0
    ordenados = sorted(clientes, key=lambda x: float(x.get('total_comprado') or 0), reverse=True)
    for pos, item in enumerate(ordenados, start=1):
        total = float(item.get('total_comprado') or 0)
        acumulado += total
        participacao = (total / faturamento_total * 100) if faturamento_total > 0 else 0
        acumulado_pct = (acumulado / faturamento_total * 100) if faturamento_total > 0 else 0
        if acumulado_pct <= 80:
            curva = 'A'
        elif acumulado_pct <= 95:
            curva = 'B'
        else:
            curva = 'C'
        item['ranking'] = pos
        item['participacao'] = round(participacao, 2)
        item['curva_abc'] = curva
        item['vip'] = total > 0 and (pos <= 5 or curva == 'A')
        item['inativo_60_dias'] = not item.get('ultima_compra')
    mapa = {i['id']: i for i in ordenados}
    clientes = [mapa.get(c['id'], c) for c in clientes]
    return clientes, ordenados[:10]


@app.route('/clientes_status')
def clientes_status():
    """Etapa 10: CRM e clientes conectado a vendas, financeiro, PIX e WhatsApp."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    clientes, top_clientes = montar_analise_clientes(cursor)
    resumo = resumo_clientes(cursor)
    cursor.execute("""
        SELECT v.*, c.nome AS cliente_nome_cadastro
        FROM vendas_erp v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        WHERE v.cliente_id IS NOT NULL
        ORDER BY v.id DESC
        LIMIT 80
    """)
    historico = [dict(row) for row in cursor.fetchall()]
    cursor.execute("""
        SELECT * FROM clientes_interacoes
        ORDER BY id DESC
        LIMIT 50
    """)
    interacoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({
        'status': 'sucesso',
        'resumo': resumo,
        'clientes': clientes,
        'top_clientes': top_clientes,
        'historico': historico,
        'interacoes': interacoes
    })


@app.route('/clientes_salvar', methods=['POST'])
def clientes_salvar():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get('nome') or '').strip()
    if not nome:
        return jsonify({'status': 'erro', 'mensagem': 'Nome do cliente é obrigatório.'}), 400
    cliente_id = int(dados.get('id') or 0)
    tipo_pessoa = str(dados.get('tipo_pessoa') or 'PF').strip().upper() or 'PF'
    campos = {
        'tipo_pessoa': tipo_pessoa if tipo_pessoa in ('PF','PJ') else 'PF',
        'nome': nome,
        'documento': str(dados.get('documento') or '').strip(),
        'telefone': str(dados.get('telefone') or '').strip(),
        'whatsapp': str(dados.get('whatsapp') or '').strip(),
        'email': str(dados.get('email') or '').strip(),
        'cep': ''.join(ch for ch in str(dados.get('cep') or '') if ch.isdigit())[:8],
        'endereco': str(dados.get('endereco') or '').strip(),
        'numero': str(dados.get('numero') or '').strip(),
        'complemento': str(dados.get('complemento') or '').strip(),
        'bairro': str(dados.get('bairro') or '').strip(),
        'cidade': str(dados.get('cidade') or '').strip(),
        'uf': str(dados.get('uf') or '').strip().upper()[:2],
        'condicao_pagamento': str(dados.get('condicao_pagamento') or '').strip(),
        'limite_credito': normalizar_float(dados.get('limite_credito'), 0),
        'observacao': str(dados.get('observacao') or '').strip(),
        'status': str(dados.get('status') or 'Ativo').strip() or 'Ativo',
        'updated_at': agora_brasilia()
    }
    conn = conectar_banco()
    cursor = conn.cursor()
    if cliente_id:
        set_sql = ', '.join([f'{k}=?' for k in campos.keys()])
        cursor.execute(f"UPDATE clientes SET {set_sql} WHERE id=?", list(campos.values()) + [cliente_id])
        mensagem = 'Cliente atualizado.'
    else:
        campos['created_at'] = agora_brasilia()
        colunas = ', '.join(campos.keys())
        marcas = ', '.join(['?'] * len(campos))
        cursor.execute(f"INSERT INTO clientes ({colunas}) VALUES ({marcas})", list(campos.values()))
        cliente_id = cursor.lastrowid
        mensagem = 'Cliente cadastrado.'
    cursor.execute("""
        INSERT INTO clientes_interacoes (cliente_id, tipo, descricao, origem, created_at)
        VALUES (?, 'cadastro', ?, 'CRM', ?)
    """, (cliente_id, mensagem, agora_brasilia()))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': mensagem, 'id': cliente_id})


@app.route('/clientes_registrar_compra', methods=['POST'])
def clientes_registrar_compra():
    """Registra compra/venda simplificada vinculada ao cliente e cria conta a receber."""
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    cliente_id = int(dados.get('cliente_id') or 0)
    produto = str(dados.get('produto') or 'Venda manual CRM').strip()
    quantidade = normalizar_float(dados.get('quantidade'), 1)
    valor_total = normalizar_float(dados.get('valor_total'), 0)
    forma = str(dados.get('forma_pagamento') or 'PIX').strip()
    status_fin = str(dados.get('status') or 'Recebido').strip()
    if not cliente_id or valor_total <= 0:
        return jsonify({'status': 'erro', 'mensagem': 'Cliente e valor são obrigatórios.'}), 400
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cursor.fetchone()
    if not cliente:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Cliente não encontrado.'}), 404
    quantidade = quantidade if quantidade > 0 else 1
    valor_unitario = valor_total / quantidade
    agora = agora_brasilia()
    cursor.execute("""
        INSERT INTO vendas_erp (
            origem, produto_final_nome, quantidade, valor_unitario, valor_total,
            custo_unitario, cmv_total, lucro_estimado, observacao, created_at,
            cliente_id, cliente_nome
        ) VALUES ('CRM', ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
    """, (produto, quantidade, valor_unitario, valor_total, valor_total, 'Venda registrada pelo CRM de clientes.', agora, cliente_id, cliente['nome']))
    venda_id = cursor.lastrowid
    registrar_lancamento_financeiro(
        cursor, 'receita', f'Venda CRM - {cliente["nome"]}', valor_total,
        'Vendas por cliente', 'CRM', venda_id, status_fin, forma,
        f'Cliente: {cliente["nome"]} | Produto: {produto}', 'CRM'
    )
    cursor.execute("UPDATE financeiro_lancamentos SET cliente_id=?, cliente_nome=? WHERE origem='CRM' AND origem_id=?", (cliente_id, cliente['nome'], venda_id))
    cursor.execute("""
        INSERT INTO clientes_interacoes (cliente_id, tipo, descricao, origem, created_at)
        VALUES (?, 'compra', ?, 'CRM', ?)
    """, (cliente_id, f'Compra registrada: {produto} - R$ {valor_total:.2f}', agora))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': 'Compra vinculada ao cliente e financeiro atualizado.', 'venda_id': venda_id})


@app.route('/clientes_whatsapp', methods=['POST'])
def clientes_whatsapp():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    cliente_id = int(dados.get('cliente_id') or 0)
    if not cliente_id:
        return jsonify({'status': 'erro', 'mensagem': 'Cliente obrigatório.'}), 400
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes WHERE id=?", (cliente_id,))
    cliente = cursor.fetchone()
    if not cliente:
        conn.close()
        return jsonify({'status': 'erro', 'mensagem': 'Cliente não encontrado.'}), 404
    mensagem = f"Olá {cliente['nome']}, tudo bem? Aqui é da Wevvo. Podemos te ajudar com um novo pedido?"
    cursor.execute("""
        INSERT INTO clientes_interacoes (cliente_id, tipo, descricao, origem, created_at)
        VALUES (?, 'whatsapp', ?, 'WhatsApp', ?)
    """, (cliente_id, mensagem, agora_brasilia()))
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'mensagem': mensagem})


def resumo_fornecedores(cursor):
    cursor.execute("SELECT COUNT(*) AS total FROM fornecedores")
    total = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM fornecedores WHERE status='Ativo'")
    ativos = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM fornecedores WHERE status<>'Ativo'")
    inativos = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COUNT(*) AS total FROM fornecedores_ingredientes")
    vinculos = int(cursor.fetchone()['total'] or 0)
    cursor.execute("SELECT COALESCE(SUM(valor_total),0) AS valor FROM compras_pedidos WHERE fornecedor_id IS NOT NULL")
    valor = float(cursor.fetchone()['valor'] or 0)
    return {'total_fornecedores': total, 'ativos': ativos, 'inativos': inativos, 'ingredientes_vinculados': vinculos, 'valor_compras': round(valor, 2)}


def montar_analise_fornecedores(cursor):
    cursor.execute("""
        SELECT f.*, COALESCE(SUM(p.valor_total),0) AS total_comprado,
               COUNT(p.id) AS quantidade_pedidos,
               COALESCE(AVG(p.valor_total),0) AS ticket_medio,
               MAX(substr(p.created_at, 1, 10)) AS ultima_compra
        FROM fornecedores f
        LEFT JOIN compras_pedidos p ON p.fornecedor_id = f.id
        GROUP BY f.id
        ORDER BY f.nome COLLATE NOCASE
    """)
    fornecedores = [dict(row) for row in cursor.fetchall()]
    ranking = sorted(fornecedores, key=lambda x: (float(x.get('total_comprado') or 0), int(x.get('avaliacao') or 0)), reverse=True)
    for pos, item in enumerate(ranking, start=1):
        item['ranking'] = pos
    mapa = {i['id']: i for i in ranking}
    fornecedores = [mapa.get(f['id'], f) for f in fornecedores]
    return fornecedores, ranking[:10]


@app.route('/fornecedores_status')
def fornecedores_status():
    """Etapa 11: fornecedores conectados a ingredientes, compras, histórico de preços e CEP automático no front-end."""
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    fornecedores, top_fornecedores = montar_analise_fornecedores(cursor)
    resumo = resumo_fornecedores(cursor)
    cursor.execute("SELECT id, nome, unidade, preco_kg FROM ingredientes ORDER BY nome COLLATE NOCASE")
    ingredientes = [dict(row) for row in cursor.fetchall()]
    cursor.execute("""
        SELECT fi.*, f.nome AS fornecedor_nome
        FROM fornecedores_ingredientes fi
        LEFT JOIN fornecedores f ON f.id = fi.fornecedor_id
        ORDER BY fi.id DESC LIMIT 120
    """)
    vinculos = [dict(row) for row in cursor.fetchall()]
    cursor.execute("""
        SELECT p.*, f.nome AS fornecedor_nome_cadastro
        FROM compras_pedidos p
        LEFT JOIN fornecedores f ON f.id = p.fornecedor_id
        WHERE p.fornecedor_id IS NOT NULL
        ORDER BY p.id DESC LIMIT 80
    """)
    historico = [dict(row) for row in cursor.fetchall()]
    cursor.execute("""
        SELECT fi.*,
               f.nome AS fornecedor_nome
        FROM fornecedores_ingredientes fi
        LEFT JOIN fornecedores f ON f.id = fi.fornecedor_id
        WHERE fi.preco_unitario > 0
        ORDER BY fi.ingrediente_nome COLLATE NOCASE, fi.preco_unitario ASC
        LIMIT 80
    """)
    melhores = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'status':'sucesso','resumo':resumo,'fornecedores':fornecedores,'top_fornecedores':top_fornecedores,'ingredientes':ingredientes,'vinculos':vinculos,'historico':historico,'melhores_precos':melhores})


@app.route('/fornecedores_salvar', methods=['POST'])
def fornecedores_salvar():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get('nome') or '').strip()
    if not nome:
        return jsonify({'status':'erro','mensagem':'Nome / razão social do fornecedor é obrigatório.'}), 400
    fornecedor_id = int(dados.get('id') or 0)
    tipo_pessoa = str(dados.get('tipo_pessoa') or 'PJ').strip().upper() or 'PJ'
    campos = {
        'tipo_pessoa': tipo_pessoa if tipo_pessoa in ('PF','PJ') else 'PJ',
        'nome': nome,
        'documento': str(dados.get('documento') or '').strip(),
        'contato': str(dados.get('contato') or '').strip(),
        'telefone': str(dados.get('telefone') or '').strip(),
        'whatsapp': str(dados.get('whatsapp') or '').strip(),
        'email': str(dados.get('email') or '').strip(),
        'cep': ''.join(ch for ch in str(dados.get('cep') or '') if ch.isdigit())[:8],
        'endereco': str(dados.get('endereco') or '').strip(),
        'numero': str(dados.get('numero') or '').strip(),
        'complemento': str(dados.get('complemento') or '').strip(),
        'bairro': str(dados.get('bairro') or '').strip(),
        'cidade': str(dados.get('cidade') or '').strip(),
        'uf': str(dados.get('uf') or '').strip().upper()[:2],
        'condicao_pagamento': str(dados.get('condicao_pagamento') or '').strip(),
        'prazo_padrao_dias': int(normalizar_float(dados.get('prazo_padrao_dias'), 0)),
        'avaliacao': max(1, min(5, int(normalizar_float(dados.get('avaliacao'), 5)))),
        'produtos_fornecidos': str(dados.get('produtos_fornecidos') or '').strip(),
        'observacao': str(dados.get('observacao') or '').strip(),
        'status': str(dados.get('status') or 'Ativo').strip() or 'Ativo',
        'updated_at': agora_brasilia()
    }
    conn = conectar_banco(); cursor = conn.cursor()
    if fornecedor_id:
        set_sql = ', '.join([f'{k}=?' for k in campos.keys()])
        cursor.execute(f"UPDATE fornecedores SET {set_sql} WHERE id=?", list(campos.values()) + [fornecedor_id])
        mensagem = 'Fornecedor atualizado.'
    else:
        campos['created_at'] = agora_brasilia()
        colunas = ', '.join(campos.keys())
        marcas = ', '.join(['?'] * len(campos))
        cursor.execute(f"INSERT INTO fornecedores ({colunas}) VALUES ({marcas})", list(campos.values()))
        fornecedor_id = cursor.lastrowid
        mensagem = 'Fornecedor cadastrado.'
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':mensagem,'id':fornecedor_id})


@app.route('/fornecedores_vincular_ingrediente', methods=['POST'])
def fornecedores_vincular_ingrediente():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    fornecedor_id = int(dados.get('fornecedor_id') or 0)
    ingrediente_id = int(dados.get('ingrediente_id') or 0)
    preco_unitario = normalizar_float(dados.get('preco_unitario'), 0)
    prazo_dias = int(normalizar_float(dados.get('prazo_dias'), 0))
    observacao = str(dados.get('observacao') or '').strip()
    if not fornecedor_id or not ingrediente_id or preco_unitario <= 0:
        return jsonify({'status':'erro','mensagem':'Fornecedor, ingrediente e preço são obrigatórios.'}), 400
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM fornecedores WHERE id=?", (fornecedor_id,))
    forn = cursor.fetchone()
    cursor.execute("SELECT id, nome, unidade FROM ingredientes WHERE id=?", (ingrediente_id,))
    ing = cursor.fetchone()
    if not forn or not ing:
        conn.close(); return jsonify({'status':'erro','mensagem':'Fornecedor ou ingrediente não encontrado.'}), 404
    agora = agora_brasilia()
    cursor.execute("""
        INSERT INTO fornecedores_ingredientes (fornecedor_id, ingrediente_id, ingrediente_nome, unidade, preco_unitario, prazo_dias, observacao, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (fornecedor_id, ingrediente_id, ing['nome'], ing['unidade'] if 'unidade' in ing.keys() else 'KG', preco_unitario, prazo_dias, observacao, agora, agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Ingrediente vinculado ao fornecedor com histórico de preço.'})


@app.route('/fornecedores_whatsapp', methods=['POST'])
def fornecedores_whatsapp():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    fornecedor_id = int(dados.get('fornecedor_id') or 0)
    if not fornecedor_id:
        return jsonify({'status':'erro','mensagem':'Fornecedor obrigatório.'}), 400
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM fornecedores WHERE id=?", (fornecedor_id,))
    fornecedor = cursor.fetchone()
    if not fornecedor:
        conn.close(); return jsonify({'status':'erro','mensagem':'Fornecedor não encontrado.'}), 404
    mensagem = f"Olá {fornecedor['nome']}, tudo bem? Aqui é da Wevvo. Gostaria de solicitar cotação dos itens cadastrados para nossa próxima compra."
    conn.close()
    return jsonify({'status':'sucesso','mensagem':mensagem})



@app.route('/pcp_ordens', methods=['GET'])
def listar_pcp_ordens():
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("""
        SELECT id, codigo, receita_id, receita_nome, produto_final_id, produto_final_nome,
               quantidade_planejada, quantidade_produzida, data_planejada, turno, responsavel,
               capacidade_hora, tempo_previsto_horas, prioridade, status, observacao, producao_id, lote, created_at, updated_at
        FROM pcp_ordens_producao
        ORDER BY
            CASE status
                WHEN 'Planejada' THEN 1
                WHEN 'Liberada' THEN 2
                WHEN 'Em produção' THEN 3
                WHEN 'Concluída' THEN 4
                WHEN 'Cancelada' THEN 5
                ELSE 9
            END,
            id DESC
        LIMIT 120
    """)
    ordens = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(ordens)


@app.route('/pcp_ordens', methods=['POST'])
def criar_pcp_ordem():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    receita_id = dados.get('receita_id')
    quantidade = normalizar_float(dados.get('quantidade_planejada', 0), 0)
    data_planejada = (dados.get('data_planejada') or '').strip()
    turno = (dados.get('turno') or '').strip()
    responsavel = (dados.get('responsavel') or '').strip()
    prioridade = (dados.get('prioridade') or 'Normal').strip()
    capacidade_hora = normalizar_float(dados.get('capacidade_hora', 0), 0)
    observacao = (dados.get('observacao') or '').strip()

    if not receita_id:
        return jsonify({'status':'erro','mensagem':'Selecione uma receita/ficha técnica.'}), 400
    if quantidade <= 0:
        return jsonify({'status':'erro','mensagem':'Informe a quantidade planejada.'}), 400

    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM receitas WHERE id = ?", (int(receita_id),))
    receita = cursor.fetchone()
    if not receita:
        conn.close(); return jsonify({'status':'erro','mensagem':'Receita não encontrada.'}), 404

    tempo_previsto = round(quantidade / capacidade_hora, 2) if capacidade_hora > 0 else 0
    agora = agora_brasilia()
    cursor.execute("SELECT COUNT(*) AS total FROM pcp_ordens_producao")
    seq = int((cursor.fetchone()['total'] or 0) + 1)
    codigo = f"OP-{seq:05d}"

    cursor.execute("""
        INSERT INTO pcp_ordens_producao (
            codigo, receita_id, receita_nome, quantidade_planejada, data_planejada,
            turno, responsavel, capacidade_hora, tempo_previsto_horas, prioridade,
            status, observacao, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Planejada', ?, ?, ?)
    """, (codigo, int(receita['id']), receita['nome'], quantidade, data_planejada, turno,
          responsavel, capacidade_hora, tempo_previsto, prioridade, observacao, agora, agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':f'Ordem de Produção {codigo} criada.', 'codigo': codigo})


@app.route('/pcp_ordens/<int:ordem_id>/status', methods=['POST'])
def atualizar_status_pcp_ordem(ordem_id):
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    status = (dados.get('status') or '').strip()
    status_validos = ['Planejada', 'Liberada', 'Em produção', 'Concluída', 'Cancelada']
    if status not in status_validos:
        return jsonify({'status':'erro','mensagem':'Status inválido.'}), 400
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM pcp_ordens_producao WHERE id = ?", (ordem_id,))
    if not cursor.fetchone():
        conn.close(); return jsonify({'status':'erro','mensagem':'OP não encontrada.'}), 404
    cursor.execute("UPDATE pcp_ordens_producao SET status = ?, updated_at = ? WHERE id = ?", (status, agora_brasilia(), ordem_id))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Status da OP atualizado.'})


@app.route('/pcp_ordens/<int:ordem_id>/produzir', methods=['POST'])
def produzir_pcp_ordem(ordem_id):
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM pcp_ordens_producao WHERE id = ?", (ordem_id,))
    ordem = cursor.fetchone()
    conn.close()
    if not ordem:
        return jsonify({'status':'erro','mensagem':'OP não encontrada.'}), 404
    if ordem['status'] in ['Concluída', 'Cancelada']:
        return jsonify({'status':'erro','mensagem':'Esta OP não pode mais ser produzida.'}), 400

    payload = {
        'receita_id': ordem['receita_id'],
        'quantidade': ordem['quantidade_planejada'],
        'observacao': f"Produção vinculada à {ordem['codigo']} - {ordem['observacao'] or ''}".strip(),
        'produto_final_id': dados.get('produto_final_id') or ordem['produto_final_id'],
        'op_codigo': ordem['codigo'],
        'responsavel': ordem['responsavel'] or 'Sistema'
    }
    with app.test_request_context('/registrar_producao', method='POST', json=payload):
        resposta = registrar_producao()
    obj = resposta[0] if isinstance(resposta, tuple) else resposta
    status_code = resposta[1] if isinstance(resposta, tuple) else 200
    dados_resposta = obj.get_json() if hasattr(obj, 'get_json') else {}
    if status_code >= 400 or dados_resposta.get('status') != 'sucesso':
        return jsonify(dados_resposta), status_code

    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("""
        UPDATE pcp_ordens_producao
        SET status = 'Concluída', quantidade_produzida = ?, producao_id = ?, lote = ?, updated_at = ?
        WHERE id = ?
    """, (float(ordem['quantidade_planejada'] or 0), dados_resposta.get('producao_id'), dados_resposta.get('lote'), agora_brasilia(), ordem_id))
    conn.commit(); conn.close()
    dados_resposta['mensagem'] = f"OP {ordem['codigo']} concluída. {dados_resposta.get('mensagem','')}"
    return jsonify(dados_resposta)


@app.route('/pcp_indicadores')
def pcp_indicadores():
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM pcp_ordens_producao WHERE status IN ('Planejada','Liberada','Em produção')")
    abertas = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COUNT(*) AS total FROM pcp_ordens_producao WHERE status='Concluída'")
    concluidas = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COALESCE(SUM(quantidade_planejada),0) AS total FROM pcp_ordens_producao WHERE status IN ('Planejada','Liberada','Em produção')")
    qtd_aberta = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COALESCE(SUM(tempo_previsto_horas),0) AS total FROM pcp_ordens_producao WHERE status IN ('Planejada','Liberada','Em produção')")
    horas = cursor.fetchone()['total'] or 0
    conn.close()
    return jsonify({'abertas': abertas, 'concluidas': concluidas, 'quantidade_planejada_aberta': qtd_aberta, 'horas_previstas_abertas': horas})


@app.route('/qualidade_status')
def qualidade_status():
    """Etapa 13: Qualidade, boas práticas, temperatura, não conformidades e rastreabilidade."""
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    hoje = hoje_brasilia()
    cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE data_registro=?", (hoje,))
    checklists_hoje = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE status<>'Conforme'")
    pendencias = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COUNT(*) AS total FROM qualidade_nao_conformidades WHERE status<>'Concluída'")
    ncs_abertas = cursor.fetchone()['total'] or 0
    cursor.execute("SELECT COUNT(*) AS total FROM qualidade_temperaturas WHERE status<>'Conforme'")
    temp_alertas = cursor.fetchone()['total'] or 0
    cursor.execute("""
        SELECT lote, receita_nome, produto_final_nome, data_fabricacao, data_validade,
               CAST(julianday(data_validade) - julianday('now') AS INTEGER) AS dias
        FROM producoes
        WHERE data_validade IS NOT NULL AND data_validade <> ''
        ORDER BY data_validade ASC
        LIMIT 12
    """)
    validades = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM qualidade_checklists ORDER BY id DESC LIMIT 20")
    checklists = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM qualidade_temperaturas ORDER BY id DESC LIMIT 20")
    temperaturas = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM qualidade_nao_conformidades ORDER BY id DESC LIMIT 20")
    nao_conformidades = [dict(row) for row in cursor.fetchall()]
    cursor.execute("SELECT * FROM qualidade_auditoria ORDER BY id DESC LIMIT 30")
    auditoria = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({
        'status': 'sucesso',
        'resumo': {'checklists_hoje': checklists_hoje, 'pendencias': pendencias, 'ncs_abertas': ncs_abertas, 'temp_alertas': temp_alertas},
        'validades': validades,
        'checklists': checklists,
        'temperaturas': temperaturas,
        'nao_conformidades': nao_conformidades,
        'auditoria': auditoria
    })

@app.route('/qualidade_checklist', methods=['POST'])
def qualidade_checklist():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    area = (dados.get('area') or 'Produção').strip()
    responsavel = dados.get('responsavel') or ''
    observacao = dados.get('observacao') or ''
    lote = dados.get('lote') or ''
    op_codigo = dados.get('op_codigo') or ''
    agora = agora_brasilia()
    data_registro = dados.get('data_registro') or hoje_brasilia()

    itens = dados.get('itens')
    if isinstance(itens, list) and itens:
        existe_nao_conforme = any(not bool(item.get('conforme', True)) for item in itens if isinstance(item, dict))
        if existe_nao_conforme and not observacao.strip():
            return jsonify({'status':'erro','mensagem':'Informe uma observação para registrar item não conforme.'}), 400
        conn = conectar_banco(); cursor = conn.cursor()
        total = 0
        for item_dados in itens:
            if not isinstance(item_dados, dict):
                continue
            item = (item_dados.get('item') or '').strip()
            if not item:
                continue
            status = 'Conforme' if bool(item_dados.get('conforme', True)) else 'Não conforme'
            cursor.execute("""
                INSERT INTO qualidade_checklists (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, agora))
            cursor.execute("INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at) VALUES (?, ?, ?, ?, ?)", ('Checklist', f'{area}: {item}', lote or op_codigo or '', status, agora))
            total += 1
            if status != 'Conforme':
                cursor.execute("""
                    INSERT INTO qualidade_nao_conformidades (data_registro, origem, lote, op_codigo, descricao, gravidade, acao_corretiva, responsavel, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (data_registro, 'Checklist de qualidade', lote, op_codigo, f'Item obrigatório não conforme: {item}. {observacao}'.strip(), 'Alta', 'Corrigir item antes de aprovar/liberar a produção.', responsavel, 'Aberta', agora, agora))
        conn.commit(); conn.close()
        return jsonify({'status':'sucesso','mensagem':f'Checklist padrão registrado com {total} itens.'})

    item = (dados.get('item') or '').strip()
    if not item:
        return jsonify({'status':'erro','mensagem':'Informe o item verificado.'}), 400
    status = dados.get('status') or 'Conforme'
    if status != 'Conforme' and not observacao.strip():
        return jsonify({'status':'erro','mensagem':'Informe uma observação para registrar item não conforme.'}), 400
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO qualidade_checklists (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data_registro, area, responsavel, item, status, observacao, lote, op_codigo, agora))
    cursor.execute("INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at) VALUES (?, ?, ?, ?, ?)", ('Checklist', f'{area}: {item}', lote or op_codigo or '', status, agora))
    if status != 'Conforme':
        cursor.execute("""
            INSERT INTO qualidade_nao_conformidades (data_registro, origem, lote, op_codigo, descricao, gravidade, acao_corretiva, responsavel, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data_registro, 'Checklist de qualidade', lote, op_codigo, f'Item obrigatório não conforme: {item}. {observacao}'.strip(), 'Alta', 'Corrigir item antes de aprovar/liberar a produção.', responsavel, 'Aberta', agora, agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Checklist registrado com sucesso.'})

@app.route('/qualidade_temperatura', methods=['POST'])
def qualidade_temperatura():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    processo = (dados.get('processo') or dados.get('area') or 'Fabricação').strip()

    padrao = None
    for item in QUALIDADE_TEMPERATURAS_PADRAO:
        if item['area'].lower() == processo.lower():
            padrao = item
            break
    if padrao:
        area = padrao['area']
        equipamento = dados.get('equipamento') or padrao['equipamento']
        temperatura = float(dados.get('temperatura') if dados.get('temperatura') not in (None, '') else padrao['temperatura'])
        limite_min = padrao['limite_min']
        limite_max = padrao['limite_max']
        observacao_padrao = padrao['observacao']
    else:
        area = processo or 'Produção'
        equipamento = dados.get('equipamento') or ''
        temperatura = float(dados.get('temperatura') or 0)
        limite_min = dados.get('limite_min')
        limite_max = dados.get('limite_max')
        limite_min = float(limite_min) if limite_min not in (None, '') else None
        limite_max = float(limite_max) if limite_max not in (None, '') else None
        observacao_padrao = ''

    status = 'Conforme'
    if limite_min is not None and temperatura < limite_min:
        status = 'Alerta'
    if limite_max is not None and temperatura > limite_max:
        status = 'Alerta'

    agora = agora_brasilia()
    observacao = (dados.get('observacao') or observacao_padrao).strip()
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO qualidade_temperaturas (data_registro, area, equipamento, temperatura, limite_min, limite_max, status, responsavel, observacao, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (dados.get('data_registro') or hoje_brasilia(), area, equipamento, temperatura, limite_min, limite_max, status, dados.get('responsavel') or '', observacao, agora))
    cursor.execute("INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at) VALUES (?, ?, ?, ?, ?)", ('Temperatura', f'{area} {temperatura:.1f}°C', equipamento, status, agora))
    if status != 'Conforme':
        cursor.execute("""
            INSERT INTO qualidade_nao_conformidades (data_registro, origem, descricao, gravidade, acao_corretiva, responsavel, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (hoje_brasilia(), 'Temperatura', f'Temperatura fora do padrão em {area}: {temperatura:.1f}°C', 'Alta', 'Verificar processo e segregar lote/produtos se necessário.', dados.get('responsavel') or '', 'Aberta', agora, agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':f'Temperatura registrada. Status: {status}.','resultado':status})

@app.route('/qualidade_nao_conformidade', methods=['POST'])
def qualidade_nao_conformidade():
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    descricao = (dados.get('descricao') or '').strip()
    if not descricao:
        return jsonify({'status':'erro','mensagem':'Informe a descrição da não conformidade.'}), 400
    agora = agora_brasilia()
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO qualidade_nao_conformidades (data_registro, origem, lote, op_codigo, descricao, gravidade, acao_corretiva, responsavel, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (dados.get('data_registro') or hoje_brasilia(), dados.get('origem') or 'Manual', dados.get('lote') or '', dados.get('op_codigo') or '', descricao, dados.get('gravidade') or 'Média', dados.get('acao_corretiva') or '', dados.get('responsavel') or '', dados.get('status') or 'Aberta', agora, agora))
    cursor.execute("INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at) VALUES (?, ?, ?, ?, ?)", ('Não conformidade', descricao[:120], dados.get('lote') or dados.get('op_codigo') or '', dados.get('status') or 'Aberta', agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Não conformidade registrada.'})

@app.route('/qualidade_nc/<int:nc_id>/status', methods=['POST'])
def qualidade_nc_status(nc_id):
    criar_tabelas()
    dados = request.get_json(silent=True) or {}
    status = dados.get('status') or 'Concluída'
    agora = agora_brasilia()
    conn = conectar_banco(); cursor = conn.cursor()
    cursor.execute("UPDATE qualidade_nao_conformidades SET status=?, updated_at=? WHERE id=?", (status, agora, nc_id))
    cursor.execute("INSERT INTO qualidade_auditoria (tipo, descricao, referencia, status, created_at) VALUES (?, ?, ?, ?, ?)", ('Atualização NC', f'NC #{nc_id} atualizada', str(nc_id), status, agora))
    conn.commit(); conn.close()
    return jsonify({'status':'sucesso','mensagem':'Status da não conformidade atualizado.'})


# ============================================================
# Núcleo inteligente do ERP - auditoria e recálculo centralizado
# Esta etapa não remove funcionalidades existentes. Ela cria uma camada
# única para verificar vínculos, recalcular CMV/precificação e registrar
# inconsistências entre módulos.
# ============================================================

def _erp_tabela_existe(cursor, nome):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,))
    return cursor.fetchone() is not None


def _erp_colunas(cursor, tabela):
    if not _erp_tabela_existe(cursor, tabela):
        return set()
    cursor.execute(f"PRAGMA table_info({tabela})")
    return {row[1] for row in cursor.fetchall()}


def _erp_garantir_tabelas_base(cursor):
    """Cria tabelas auxiliares da base inteligente sem alterar tabelas críticas."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_motor_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo TEXT NOT NULL,
            acao TEXT NOT NULL,
            entidade TEXT,
            entidade_id INTEGER,
            status TEXT NOT NULL DEFAULT 'sucesso',
            mensagem TEXT,
            detalhes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_auditoria_integrada (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            modulo TEXT NOT NULL,
            item TEXT NOT NULL,
            severidade TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'aberto',
            mensagem TEXT,
            referencia TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_relacionamentos_modulos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origem_modulo TEXT NOT NULL,
            origem_tabela TEXT,
            origem_id INTEGER,
            destino_modulo TEXT NOT NULL,
            destino_tabela TEXT,
            destino_id INTEGER,
            tipo_vinculo TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ativo',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erp_motor_modulo ON erp_motor_eventos(modulo, acao)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_erp_auditoria_modulo ON erp_auditoria_integrada(modulo, severidade, status)")


def _erp_registrar_evento(cursor, modulo, acao, entidade=None, entidade_id=None, status='sucesso', mensagem='', detalhes=''):
    _erp_garantir_tabelas_base(cursor)
    cursor.execute("""
        INSERT INTO erp_motor_eventos (modulo, acao, entidade, entidade_id, status, mensagem, detalhes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (modulo, acao, entidade, entidade_id, status, mensagem, detalhes, agora_brasilia()))


def _erp_limpar_auditoria(cursor):
    _erp_garantir_tabelas_base(cursor)
    cursor.execute("DELETE FROM erp_auditoria_integrada")


def _erp_auditar(cursor, modulo, item, severidade='info', mensagem='', referencia=''):
    _erp_garantir_tabelas_base(cursor)
    cursor.execute("""
        INSERT INTO erp_auditoria_integrada (modulo, item, severidade, status, mensagem, referencia, created_at)
        VALUES (?, ?, ?, 'aberto', ?, ?, ?)
    """, (modulo, item, severidade, mensagem, referencia, agora_brasilia()))


def _erp_recalcular_cmv_todos(cursor):
    """Recalcula CMV de receitas e produtos acabados usando a fórmula oficial."""
    _erp_garantir_tabelas_base(cursor)
    total = 0
    erros = []

    entidades = []
    if _erp_tabela_existe(cursor, 'receitas'):
        cursor.execute("SELECT id FROM receitas")
        entidades += [('receita', int(r['id'])) for r in cursor.fetchall()]
    if _erp_tabela_existe(cursor, 'produtos_finais'):
        cursor.execute("SELECT id FROM produtos_finais")
        entidades += [('produto', int(r['id'])) for r in cursor.fetchall()]

    for tipo, eid in entidades:
        try:
            base = obter_custo_base_entidade(cursor, tipo, eid)
            if not base:
                erros.append(f'{tipo} #{eid} sem cadastro base')
                continue
            cursor.execute("""
                SELECT embalagem_unitaria, custo_fixo_unitario, custo_variavel_percentual,
                       margem_desejada_percentual, preco_praticado
                FROM cmv_precificacao
                WHERE entidade_tipo=? AND entidade_id=?
            """, (tipo, eid))
            cfg = cursor.fetchone()
            embalagem = cfg['embalagem_unitaria'] if cfg else 0
            fixo = cfg['custo_fixo_unitario'] if cfg else 0
            taxas = cfg['custo_variavel_percentual'] if cfg else 0
            margem = cfg['margem_desejada_percentual'] if cfg else 30
            preco = cfg['preco_praticado'] if cfg and normalizar_decimal_cmv(cfg['preco_praticado'], 0) > 0 else base.get('preco_atual', 0)
            calculo = calcular_precificacao_cmv(base['custo_base'], embalagem, fixo, taxas, margem, preco)
            salvar_calculo_cmv(cursor, tipo, eid, calculo, 'Recalculado pelo motor central do ERP')
            total += 1
        except Exception as exc:
            erros.append(f'{tipo} #{eid}: {exc}')

    _erp_registrar_evento(cursor, 'CMV', 'recalculo_central', 'cmv_precificacao', None, 'sucesso' if not erros else 'parcial', f'{total} itens recalculados', '; '.join(erros[:20]))
    return {'recalculados': total, 'erros': erros[:50]}


def _erp_sincronizar_financeiro_central(cursor):
    """Centraliza sincronizações financeiras automáticas já existentes."""
    total_antes = 0
    if _erp_tabela_existe(cursor, 'financeiro_lancamentos'):
        cursor.execute("SELECT COUNT(*) AS total FROM financeiro_lancamentos")
        total_antes = int((cursor.fetchone() or {'total': 0})['total'] or 0)
    compras = {'pedidos': 0, 'contas_pagar': 0, 'recebidos': 0}
    try:
        sincronizar_financeiro_com_vendas(cursor)
        compras = erp_sincronizar_compras_estoque_financeiro(cursor)
    except Exception as exc:
        _erp_registrar_evento(cursor, 'Financeiro', 'sincronizar_compras_vendas', status='erro', mensagem=str(exc))
        return {'status': 'erro', 'mensagem': str(exc), 'novos': 0, 'compras': compras}
    cursor.execute("SELECT COUNT(*) AS total FROM financeiro_lancamentos")
    total_depois = int((cursor.fetchone() or {'total': 0})['total'] or 0)
    novos = max(0, total_depois - total_antes)
    _erp_registrar_evento(cursor, 'Financeiro', 'sincronizar_vendas', status='sucesso', mensagem=f'{novos} lançamento(s) sincronizado(s)')
    return {'status': 'sucesso', 'novos': novos, 'compras': compras}


def _erp_auditoria_geral(cursor):
    """Audita os principais vínculos sem apagar ou sobrescrever dados existentes."""
    _erp_limpar_auditoria(cursor)
    resumo = {}

    def count(tabela, where='1=1'):
        if not _erp_tabela_existe(cursor, tabela):
            return 0
        cursor.execute(f"SELECT COUNT(*) AS total FROM {tabela} WHERE {where}")
        return int((cursor.fetchone() or {'total': 0})['total'] or 0)

    resumo['ingredientes'] = count('ingredientes')
    resumo['receitas'] = count('receitas')
    resumo['produtos_finais'] = count('produtos_finais')
    resumo['lotes'] = count('estoque_produto_acabado')
    resumo['movimentacoes_ingredientes'] = count('movimentacoes_estoque')
    resumo['vendas'] = count('vendas_erp')
    resumo['financeiro'] = count('financeiro_lancamentos')
    resumo['cmv'] = count('cmv_precificacao')

    # Receitas sem ficha técnica
    if _erp_tabela_existe(cursor, 'receitas') and _erp_tabela_existe(cursor, 'receita_itens'):
        cursor.execute("""
            SELECT r.id, r.nome FROM receitas r
            LEFT JOIN receita_itens ri ON ri.receita_id = r.id
            GROUP BY r.id
            HAVING COUNT(ri.id) = 0
            LIMIT 100
        """)
        for r in cursor.fetchall():
            _erp_auditar(cursor, 'Receitas', 'Receita sem ficha técnica', 'alerta', f"Receita sem ingredientes vinculados: {r['nome']}", str(r['id']))

    # Produtos sem receita vinculada
    if _erp_tabela_existe(cursor, 'produtos_finais'):
        cols = _erp_colunas(cursor, 'produtos_finais')
        if 'receita_id' in cols:
            cursor.execute("SELECT id, nome FROM produtos_finais WHERE COALESCE(receita_id, 0)=0 LIMIT 100")
            for r in cursor.fetchall():
                _erp_auditar(cursor, 'Produtos', 'Produto sem receita', 'critico', f"Produto acabado sem receita vinculada: {r['nome']}", str(r['id']))

    # Ingredientes abaixo do mínimo
    if _erp_tabela_existe(cursor, 'ingredientes'):
        cursor.execute("""
            SELECT id, nome, estoque_atual, estoque_minimo FROM ingredientes
            WHERE COALESCE(estoque_minimo,0) > 0 AND COALESCE(estoque_atual,0) < COALESCE(estoque_minimo,0)
            LIMIT 100
        """)
        for r in cursor.fetchall():
            _erp_auditar(cursor, 'Estoque', 'Ingrediente abaixo do mínimo', 'alerta', f"{r['nome']}: atual {r['estoque_atual']} / mínimo {r['estoque_minimo']}", str(r['id']))

    # CMV sem preço ou com prejuízo
    if _erp_tabela_existe(cursor, 'cmv_precificacao'):
        cursor.execute("""
            SELECT entidade_tipo, entidade_id, status, preco_praticado, cmv_real, lucro_real, margem_real_percentual
            FROM cmv_precificacao
            WHERE COALESCE(preco_praticado,0) <= 0 OR status IN ('Prejuízo','Sem preço') OR COALESCE(lucro_real,0) < 0
            LIMIT 150
        """)
        for r in cursor.fetchall():
            sev = 'critico' if r['status'] == 'Prejuízo' else 'alerta'
            _erp_auditar(cursor, 'CMV', f"CMV {r['status'] or 'inconsistente'}", sev, f"{r['entidade_tipo']} #{r['entidade_id']} - preço {r['preco_praticado']} / CMV {r['cmv_real']} / lucro {r['lucro_real']}", f"{r['entidade_tipo']}:{r['entidade_id']}")

    # Financeiro vencido
    if _erp_tabela_existe(cursor, 'financeiro_lancamentos'):
        hoje = hoje_brasilia()
        cursor.execute("""
            SELECT id, tipo, descricao, valor, data_vencimento, status
            FROM financeiro_lancamentos
            WHERE COALESCE(data_vencimento,'9999-99-99') < ?
              AND LOWER(COALESCE(status,'')) NOT IN ('pago','recebido','cancelado')
            LIMIT 100
        """, (hoje,))
        for r in cursor.fetchall():
            _erp_auditar(cursor, 'Financeiro', 'Lançamento vencido', 'alerta', f"{r['descricao']} - R$ {float(r['valor'] or 0):.2f} venc. {r['data_vencimento']}", str(r['id']))

    cursor.execute("SELECT severidade, COUNT(*) AS total FROM erp_auditoria_integrada GROUP BY severidade")
    severidades = {r['severidade']: int(r['total'] or 0) for r in cursor.fetchall()}
    resumo['auditoria'] = {
        'criticos': severidades.get('critico', 0),
        'alertas': severidades.get('alerta', 0),
        'infos': severidades.get('info', 0),
        'total': sum(severidades.values())
    }
    _erp_registrar_evento(cursor, 'ERP', 'auditoria_geral', status='sucesso', mensagem='Auditoria integrada concluída')
    return resumo


@app.route('/api/erp_motor/recalcular', methods=['POST'])
def api_erp_motor_recalcular():
    """Executa recálculo central do ERP: CMV, financeiro e auditoria."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    _erp_garantir_tabelas_base(cursor)
    cmv = _erp_recalcular_cmv_todos(cursor)
    financeiro = _erp_sincronizar_financeiro_central(cursor)
    auditoria = _erp_auditoria_geral(cursor)
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'cmv': cmv, 'financeiro': financeiro, 'auditoria': auditoria})


@app.route('/api/erp_auditoria_integrada')
def api_erp_auditoria_integrada():
    """Retorna diagnóstico dos módulos e inconsistências encontradas."""
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    _erp_garantir_tabelas_base(cursor)
    resumo = _erp_auditoria_geral(cursor)
    cursor.execute("""
        SELECT modulo, item, severidade, mensagem, referencia, created_at
        FROM erp_auditoria_integrada
        ORDER BY CASE severidade WHEN 'critico' THEN 1 WHEN 'alerta' THEN 2 ELSE 3 END, modulo, id
        LIMIT 300
    """)
    itens = [dict(r) for r in cursor.fetchall()]
    conn.commit()
    conn.close()
    return jsonify({'status': 'sucesso', 'resumo': resumo, 'itens': itens})


# ============================================================
# Etapa seguinte: relatórios e indicadores por submenu
# Mantém páginas existentes, mas cria uma fonte de dados específica
# para cada relatório/setor, sem reaproveitar o mesmo conteúdo.
# ============================================================

def _erp_valor(cursor, sql, params=(), campo='valor', padrao=0):
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if not row:
            return padrao
        return row[campo] if campo in row.keys() else list(row)[0]
    except Exception:
        return padrao


def _erp_lista(cursor, sql, params=(), limite=100):
    try:
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()[:limite]]
    except Exception:
        return []


def _relatorio_financeiro(cursor):
    hoje = hoje_brasilia()
    dados = {
        'titulo': 'Relatório financeiro',
        'indicadores': {
            'contas_a_pagar_abertas': 0,
            'valor_a_pagar': 0,
            'contas_a_receber_abertas': 0,
            'valor_a_receber': 0,
            'recebido': 0,
            'pago': 0,
            'saldo_previsto': 0,
        },
        'tabelas': {}
    }
    if _erp_tabela_existe(cursor, 'financeiro_lancamentos'):
        pagar_qtd = _erp_valor(cursor, "SELECT COUNT(*) AS valor FROM financeiro_lancamentos WHERE tipo='despesa' AND LOWER(COALESCE(status,'')) NOT IN ('pago','cancelado')")
        pagar_valor = _erp_valor(cursor, "SELECT COALESCE(SUM(valor),0) AS valor FROM financeiro_lancamentos WHERE tipo='despesa' AND LOWER(COALESCE(status,'')) NOT IN ('pago','cancelado')")
        receber_qtd = _erp_valor(cursor, "SELECT COUNT(*) AS valor FROM financeiro_lancamentos WHERE tipo='receita' AND LOWER(COALESCE(status,'')) NOT IN ('recebido','pago','cancelado')")
        receber_valor = _erp_valor(cursor, "SELECT COALESCE(SUM(valor),0) AS valor FROM financeiro_lancamentos WHERE tipo='receita' AND LOWER(COALESCE(status,'')) NOT IN ('recebido','pago','cancelado')")
        recebido = _erp_valor(cursor, "SELECT COALESCE(SUM(valor),0) AS valor FROM financeiro_lancamentos WHERE tipo='receita' AND LOWER(COALESCE(status,'')) IN ('recebido','pago')")
        pago = _erp_valor(cursor, "SELECT COALESCE(SUM(valor),0) AS valor FROM financeiro_lancamentos WHERE tipo='despesa' AND LOWER(COALESCE(status,''))='pago'")
        dados['indicadores'].update({
            'contas_a_pagar_abertas': int(pagar_qtd or 0),
            'valor_a_pagar': round(float(pagar_valor or 0), 2),
            'contas_a_receber_abertas': int(receber_qtd or 0),
            'valor_a_receber': round(float(receber_valor or 0), 2),
            'recebido': round(float(recebido or 0), 2),
            'pago': round(float(pago or 0), 2),
            'saldo_previsto': round(float(receber_valor or 0) - float(pagar_valor or 0), 2),
        })
        dados['tabelas']['vencidos'] = _erp_lista(cursor, """
            SELECT id, tipo, descricao, valor, data_vencimento, status, origem
            FROM financeiro_lancamentos
            WHERE COALESCE(data_vencimento,'9999-99-99') < ?
              AND LOWER(COALESCE(status,'')) NOT IN ('pago','recebido','cancelado')
            ORDER BY data_vencimento ASC LIMIT 80
        """, (hoje,))
        dados['tabelas']['proximos'] = _erp_lista(cursor, """
            SELECT id, tipo, descricao, valor, data_vencimento, status, origem
            FROM financeiro_lancamentos
            WHERE COALESCE(data_vencimento,'9999-99-99') >= ?
              AND LOWER(COALESCE(status,'')) NOT IN ('pago','recebido','cancelado')
            ORDER BY data_vencimento ASC LIMIT 80
        """, (hoje,))
    return dados


def _relatorio_estoque(cursor):
    dados = {'titulo': 'Relatório de estoque', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'ingredientes'):
        dados['indicadores']['ingredientes'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM ingredientes") or 0)
        dados['indicadores']['ingredientes_abaixo_minimo'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM ingredientes WHERE COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<COALESCE(estoque_minimo,0)") or 0)
        dados['indicadores']['valor_ingredientes'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(COALESCE(estoque_atual,0)*COALESCE(preco_kg,0)),0) AS valor FROM ingredientes") or 0), 2)
        dados['tabelas']['ingredientes_baixo'] = _erp_lista(cursor, """
            SELECT id, nome, unidade, estoque_atual, estoque_minimo, preco_kg
            FROM ingredientes
            WHERE COALESCE(estoque_minimo,0)>0 AND COALESCE(estoque_atual,0)<COALESCE(estoque_minimo,0)
            ORDER BY nome COLLATE NOCASE LIMIT 100
        """)
    if _erp_tabela_existe(cursor, 'lotes'):
        dados['indicadores']['lotes_com_saldo'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM lotes WHERE COALESCE(saldo,0)>0") or 0)
        dados['tabelas']['lotes'] = _erp_lista(cursor, "SELECT lote, produto_nome, saldo, validade, created_at FROM lotes WHERE COALESCE(saldo,0)>0 ORDER BY validade ASC LIMIT 100")
    return dados


def _relatorio_producao(cursor):
    dados = {'titulo': 'Relatório de produção', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'producoes'):
        dados['indicadores']['ops'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM producoes") or 0)
        dados['indicadores']['produzido_total'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(quantidade),0) AS valor FROM producoes") or 0), 3)
        dados['tabelas']['ultimas_producoes'] = _erp_lista(cursor, "SELECT * FROM producoes ORDER BY id DESC LIMIT 80")
    if _erp_tabela_existe(cursor, 'lotes'):
        dados['indicadores']['lotes_gerados'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM lotes") or 0)
    return dados


def _relatorio_vendas(cursor):
    dados = {'titulo': 'Relatório de vendas', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'vendas_erp'):
        dados['indicadores']['vendas'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM vendas_erp") or 0)
        dados['indicadores']['faturamento'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(valor_total),0) AS valor FROM vendas_erp") or 0), 2)
        dados['indicadores']['cmv'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(cmv_total),0) AS valor FROM vendas_erp") or 0), 2)
        dados['indicadores']['lucro_estimado'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(lucro_estimado),0) AS valor FROM vendas_erp") or 0), 2)
        dados['tabelas']['ultimas_vendas'] = _erp_lista(cursor, "SELECT id, origem, cliente_nome, produto_final_nome, quantidade, valor_total, cmv_total, lucro_estimado, created_at FROM vendas_erp ORDER BY id DESC LIMIT 100")
    return dados


def _relatorio_compras(cursor):
    dados = {'titulo': 'Relatório de compras', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'compras_pedidos'):
        dados['indicadores']['pedidos'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM compras_pedidos") or 0)
        dados['indicadores']['valor_pedidos'] = round(float(_erp_valor(cursor, "SELECT COALESCE(SUM(valor_total),0) AS valor FROM compras_pedidos") or 0), 2)
        dados['indicadores']['abertos'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM compras_pedidos WHERE LOWER(COALESCE(status,'')) NOT IN ('recebido','cancelado')") or 0)
        dados['tabelas']['pedidos'] = _erp_lista(cursor, "SELECT * FROM compras_pedidos ORDER BY id DESC LIMIT 100")
    return dados


def _relatorio_cmv(cursor):
    dados = {'titulo': 'Relatório de CMV e margens', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'cmv_precificacao'):
        dados['indicadores']['itens'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM cmv_precificacao") or 0)
        dados['indicadores']['sem_preco'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM cmv_precificacao WHERE COALESCE(preco_praticado,0)<=0") or 0)
        dados['indicadores']['prejuizo'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM cmv_precificacao WHERE COALESCE(lucro_real,0)<0") or 0)
        dados['indicadores']['margem_media'] = round(float(_erp_valor(cursor, "SELECT COALESCE(AVG(margem_real_percentual),0) AS valor FROM cmv_precificacao") or 0), 2)
        dados['tabelas']['itens'] = _erp_lista(cursor, "SELECT entidade_tipo, entidade_id, cmv_real, preco_sugerido, preco_praticado, lucro_real, margem_real_percentual, markup_percentual, status FROM cmv_precificacao ORDER BY status, margem_real_percentual ASC LIMIT 150")
    return dados


def _relatorio_qualidade(cursor):
    dados = {'titulo': 'Relatório de qualidade', 'indicadores': {}, 'tabelas': {}}
    if _erp_tabela_existe(cursor, 'qualidade_checklists'):
        dados['indicadores']['checklists'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM qualidade_checklists") or 0)
        dados['indicadores']['nao_conformes_checklist'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM qualidade_checklists WHERE status<>'Conforme'") or 0)
    if _erp_tabela_existe(cursor, 'qualidade_nao_conformidades'):
        dados['indicadores']['nc_abertas'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM qualidade_nao_conformidades WHERE LOWER(COALESCE(status,'')) NOT IN ('concluída','concluida','fechada','cancelada')") or 0)
        dados['tabelas']['nao_conformidades'] = _erp_lista(cursor, "SELECT * FROM qualidade_nao_conformidades ORDER BY id DESC LIMIT 100")
    if _erp_tabela_existe(cursor, 'qualidade_temperaturas'):
        dados['indicadores']['alertas_temperatura'] = int(_erp_valor(cursor, "SELECT COUNT(*) AS valor FROM qualidade_temperaturas WHERE status<>'Conforme'") or 0)
        dados['tabelas']['temperaturas'] = _erp_lista(cursor, "SELECT * FROM qualidade_temperaturas ORDER BY id DESC LIMIT 100")
    return dados


def montar_relatorio_setor(cursor, setor):
    setor = (setor or '').strip().lower()
    mapa = {
        'financeiro': _relatorio_financeiro,
        'contas-a-pagar': _relatorio_financeiro,
        'contas-a-receber': _relatorio_financeiro,
        'estoque': _relatorio_estoque,
        'producao': _relatorio_producao,
        'produção': _relatorio_producao,
        'vendas': _relatorio_vendas,
        'compras': _relatorio_compras,
        'cmv': _relatorio_cmv,
        'margens': _relatorio_cmv,
        'qualidade': _relatorio_qualidade,
    }
    func = mapa.get(setor, None)
    if func is None:
        return {'titulo': f'Relatório - {setor or "geral"}', 'indicadores': {}, 'tabelas': {}, 'mensagem': 'Setor ainda sem relatório específico.'}
    return func(cursor)


@app.route('/api/relatorios/setor/<setor>')
def api_relatorios_setor(setor):
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        _erp_garantir_tabelas_base(cursor)
        dados = montar_relatorio_setor(cursor, setor)
        _erp_registrar_evento(cursor, 'Relatórios', f'consultar_{setor}', 'relatorio', None, 'sucesso', f'Relatório {setor} consultado')
        conn.commit()
        return jsonify({'status': 'sucesso', 'setor': setor, 'dados': dados})
    finally:
        conn.close()


@app.route('/api/dashboard_executivo_integrado')
def api_dashboard_executivo_integrado():
    criar_tabelas()
    dashboard = gerar_dashboard_executivo(BANCO)
    return jsonify(dashboard)


@app.route('/api/dashboard_inteligente_erp')
def api_dashboard_inteligente_erp():
    criar_tabelas()
    dashboard = gerar_dashboard_executivo(BANCO)
    return jsonify(dashboard)


# Etapa seguinte: cadastros auxiliares com dados próprios por submenu.
def _cadastro_aux_resumo(cursor, modulo):
    total = cursor.execute("SELECT COUNT(*) AS total FROM cadastros_auxiliares WHERE modulo=?", (modulo,)).fetchone()["total"]
    ativos = cursor.execute("SELECT COUNT(*) AS total FROM cadastros_auxiliares WHERE modulo=? AND status='Ativo'", (modulo,)).fetchone()["total"]
    inativos = cursor.execute("SELECT COUNT(*) AS total FROM cadastros_auxiliares WHERE modulo=? AND status<>'Ativo'", (modulo,)).fetchone()["total"]
    valor = cursor.execute("SELECT COALESCE(SUM(valor_padrao),0) AS total FROM cadastros_auxiliares WHERE modulo=?", (modulo,)).fetchone()["total"]
    return {"total": int(total or 0), "kpi1": int(total or 0), "kpi2": int(ativos or 0), "kpi3": int(inativos or 0), "kpi4": float(valor or 0)}


def _cadastro_aux_linhas(cursor, modulo):
    rows = cursor.execute("""
        SELECT id, nome, COALESCE(descricao,'') AS descricao, status,
               COALESCE(valor_padrao,0) AS valor_padrao, COALESCE(observacao,'') AS observacao,
               COALESCE(updated_at, created_at) AS atualizado_em
        FROM cadastros_auxiliares
        WHERE modulo=?
        ORDER BY status, nome COLLATE NOCASE
    """, (modulo,)).fetchall()
    linhas1 = [[r["nome"], r["status"], r["descricao"] or "-"] for r in rows]
    linhas2 = [[r["nome"], "Valor padrão", float(r["valor_padrao"] or 0)] for r in rows]
    itens = [dict(r) for r in rows]
    return itens, linhas1, linhas2



@app.route('/api/financeiro_integrado')
def api_financeiro_integrado():
    criar_tabelas()
    return jsonify(gerar_financeiro_integrado(BANCO))

@app.route('/api/cadastros_auxiliares/<modulo>')
def api_cadastros_auxiliares(modulo):
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        modulo = (modulo or '').strip().lower()
        resumo = _cadastro_aux_resumo(cursor, modulo)
        itens, linhas1, linhas2 = _cadastro_aux_linhas(cursor, modulo)
        return jsonify({'status': 'sucesso', 'modulo': modulo, 'resumo': resumo, 'itens': itens, 'linhas1': linhas1, 'linhas2': linhas2})
    finally:
        conn.close()


@app.route('/api/cadastros_auxiliares/<modulo>', methods=['POST'])
def api_salvar_cadastro_auxiliar(modulo):
    criar_tabelas()
    dados = request.get_json(silent=True) or request.form.to_dict()
    nome = (dados.get('nome') or '').strip()
    if not nome:
        return jsonify({'status': 'erro', 'mensagem': 'Informe o nome do cadastro.'}), 400
    descricao = (dados.get('descricao') or '').strip()
    status_item = (dados.get('status') or 'Ativo').strip() or 'Ativo'
    valor_padrao = parse_float_br(dados.get('valor_padrao') or 0)
    observacao = (dados.get('observacao') or '').strip()
    item_id = dados.get('id')
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        modulo = (modulo or '').strip().lower()
        if item_id:
            cursor.execute("""
                UPDATE cadastros_auxiliares
                SET nome=?, descricao=?, status=?, valor_padrao=?, observacao=?, updated_at=?
                WHERE id=? AND modulo=?
            """, (nome, descricao, status_item, valor_padrao, observacao, agora_brasilia(), item_id, modulo))
        else:
            cursor.execute("""
                INSERT INTO cadastros_auxiliares (modulo, nome, descricao, status, valor_padrao, observacao, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (modulo, nome, descricao, status_item, valor_padrao, observacao, agora_brasilia(), agora_brasilia()))
        conn.commit()
        resumo = _cadastro_aux_resumo(cursor, modulo)
        itens, linhas1, linhas2 = _cadastro_aux_linhas(cursor, modulo)
        return jsonify({'status': 'sucesso', 'resumo': resumo, 'itens': itens, 'linhas1': linhas1, 'linhas2': linhas2})
    finally:
        conn.close()


# Etapa seguinte: submódulos reais de Qualidade com dados próprios por página.
def _qualidade_submodulo_payload(cursor, modulo):
    modulo = (modulo or '').strip().lower()
    hoje = hoje_brasilia()

    if modulo == 'qualidade-checklists':
        total = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE data_registro=?", (hoje,)).fetchone()["total"] or 0
        pendentes = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE status<>'Conforme'").fetchone()["total"] or 0
        conformes = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE status='Conforme'").fetchone()["total"] or 0
        nao_conf = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_checklists WHERE status<>'Conforme'").fetchone()["total"] or 0
        rows = cursor.execute("""
            SELECT data_registro, area, item, status, COALESCE(lote, op_codigo, '') AS referencia
            FROM qualidade_checklists
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()
        linhas1 = [[r['item'], r['status'], r['referencia'] or r['area'] or '-'] for r in rows]
        linhas2 = [[r['data_registro'], r['area'] or '-', r['status']] for r in rows if r['status'] != 'Conforme']
        return {'resumo': {'total': total, 'kpi1': total, 'kpi2': pendentes, 'kpi3': conformes, 'kpi4': nao_conf}, 'linhas1': linhas1, 'linhas2': linhas2}

    if modulo == 'qualidade-temperaturas':
        total = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_temperaturas").fetchone()["total"] or 0
        alertas = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_temperaturas WHERE status<>'Conforme'").fetchone()["total"] or 0
        fabricacao = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_temperaturas WHERE area LIKE '%Fabrica%' OR area LIKE '%Fabricação%'").fetchone()["total"] or 0
        ultra = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_temperaturas WHERE area LIKE '%Ultra%' OR area LIKE '%Congel%'").fetchone()["total"] or 0
        rows = cursor.execute("""
            SELECT data_registro, area, equipamento, temperatura, status
            FROM qualidade_temperaturas
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()
        linhas1 = [[r['area'] or '-', f"{float(r['temperatura'] or 0):.1f} °C", r['status']] for r in rows]
        linhas2 = [[r['data_registro'], r['equipamento'] or '-', f"{float(r['temperatura'] or 0):.1f} °C"] for r in rows if r['status'] != 'Conforme']
        return {'resumo': {'total': total, 'kpi1': total, 'kpi2': alertas, 'kpi3': fabricacao, 'kpi4': ultra}, 'linhas1': linhas1, 'linhas2': linhas2}

    if modulo == 'qualidade-nao-conformidades':
        abertas = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_nao_conformidades WHERE status<>'Concluída'").fetchone()["total"] or 0
        alta = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_nao_conformidades WHERE gravidade='Alta' AND status<>'Concluída'").fetchone()["total"] or 0
        concluidas = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_nao_conformidades WHERE status='Concluída'").fetchone()["total"] or 0
        pendentes = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_nao_conformidades WHERE status IN ('Aberta','Em análise','Pendente')").fetchone()["total"] or 0
        rows = cursor.execute("""
            SELECT id, data_registro, origem, descricao, gravidade, status, COALESCE(lote, op_codigo, '') AS referencia
            FROM qualidade_nao_conformidades
            ORDER BY CASE status WHEN 'Aberta' THEN 1 WHEN 'Em análise' THEN 2 WHEN 'Pendente' THEN 3 ELSE 4 END, id DESC
            LIMIT 30
        """).fetchall()
        linhas1 = [[r['descricao'][:80], r['gravidade'] or '-', r['status']] for r in rows if r['status'] != 'Concluída']
        linhas2 = [[r['data_registro'], r['origem'] or '-', r['referencia'] or f"NC #{r['id']}"] for r in rows]
        return {'resumo': {'total': abertas, 'kpi1': abertas, 'kpi2': alta, 'kpi3': concluidas, 'kpi4': pendentes}, 'linhas1': linhas1, 'linhas2': linhas2}

    if modulo == 'qualidade-validades':
        rows = cursor.execute("""
            SELECT lote, COALESCE(produto_final_nome, receita_nome, '') AS produto, data_fabricacao, data_validade,
                   COALESCE(saldo_atual, quantidade, 0) AS saldo,
                   CAST(julianday(data_validade) - julianday('now') AS INTEGER) AS dias
            FROM producoes
            WHERE data_validade IS NOT NULL AND data_validade <> ''
            ORDER BY data_validade ASC
            LIMIT 40
        """).fetchall()
        total = len(rows)
        vencendo = sum(1 for r in rows if r['dias'] is not None and 0 <= int(r['dias']) <= 30)
        vencidos = sum(1 for r in rows if r['dias'] is not None and int(r['dias']) < 0)
        saldo = sum(float(r['saldo'] or 0) for r in rows)
        linhas1 = [[r['produto'] or '-', r['lote'] or '-', r['data_validade'] or '-'] for r in rows]
        linhas2 = [[r['lote'] or '-', f"{int(r['dias'] or 0)} dias", float(r['saldo'] or 0)] for r in rows if r['dias'] is not None and int(r['dias']) <= 30]
        return {'resumo': {'total': total, 'kpi1': total, 'kpi2': vencendo, 'kpi3': vencidos, 'kpi4': saldo}, 'linhas1': linhas1, 'linhas2': linhas2}

    if modulo == 'qualidade-auditoria':
        total = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_auditoria").fetchone()["total"] or 0
        checklists = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_auditoria WHERE tipo='Checklist'").fetchone()["total"] or 0
        temperaturas = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_auditoria WHERE tipo='Temperatura'").fetchone()["total"] or 0
        ncs = cursor.execute("SELECT COUNT(*) AS total FROM qualidade_auditoria WHERE tipo LIKE '%conformidade%' OR tipo LIKE '%NC%'").fetchone()["total"] or 0
        rows = cursor.execute("""
            SELECT tipo, descricao, referencia, status, created_at
            FROM qualidade_auditoria
            ORDER BY id DESC
            LIMIT 40
        """).fetchall()
        linhas1 = [[r['tipo'] or '-', r['status'] or '-', r['descricao'] or '-'] for r in rows]
        linhas2 = [[r['created_at'] or '-', r['referencia'] or '-', r['tipo'] or '-'] for r in rows]
        return {'resumo': {'total': total, 'kpi1': total, 'kpi2': checklists, 'kpi3': temperaturas, 'kpi4': ncs}, 'linhas1': linhas1, 'linhas2': linhas2}

    return {'resumo': {}, 'linhas1': [], 'linhas2': []}


@app.route('/api/qualidade_submodulo/<modulo>')
def api_qualidade_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        payload = _qualidade_submodulo_payload(cursor, modulo)
        payload['status'] = 'sucesso'
        payload['modulo'] = modulo
        return jsonify(payload)
    finally:
        conn.close()


# Configurações reais do ERP: acesso/login, identidade visual e atalhos da engrenagem.
def _config_get(cursor, chave, padrao=''):
    row = cursor.execute("SELECT valor FROM sistema_configuracoes WHERE chave=?", (chave,)).fetchone()
    return row['valor'] if row and row['valor'] is not None else padrao


def _config_set(cursor, chave, valor, tipo='texto'):
    cursor.execute("""
        INSERT INTO sistema_configuracoes (chave, valor, tipo, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor, tipo=excluded.tipo, updated_at=excluded.updated_at
    """, (chave, valor, tipo, agora_brasilia()))


def _salvar_upload_config(arquivo, prefixo):
    if not arquivo or not getattr(arquivo, 'filename', ''):
        return None
    pasta = os.path.join(app.root_path, 'static', 'uploads')
    os.makedirs(pasta, exist_ok=True)
    nome_seguro = secure_filename(arquivo.filename)
    if not nome_seguro:
        return None
    base, ext = os.path.splitext(nome_seguro)
    nome_final = f"{prefixo}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext.lower()}"
    caminho = os.path.join(pasta, nome_final)
    arquivo.save(caminho)
    return f"/static/uploads/{nome_final}"




def _exportacao_tabela_existe(cursor, nome):
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,))
        return cursor.fetchone() is not None
    except Exception:
        return False


def _exportacao_consulta_modulo(cursor, modulo):
    """Mapeia relatórios/exportações para dados reais do banco, sem repetir a mesma consulta para todos os setores."""
    modulo = (modulo or 'gerencial').strip().lower()
    consultas = {
        'financeiro': ("""
            SELECT tipo, descricao, categoria, valor, vencimento, data_pagamento, status, origem, created_at
            FROM financeiro_lancamentos
            ORDER BY COALESCE(vencimento, created_at) DESC, id DESC
            LIMIT 500
        """, ()),
        'contas-pagar': ("""
            SELECT descricao, categoria, valor, vencimento, data_pagamento, status, origem, created_at
            FROM financeiro_lancamentos
            WHERE tipo='pagar'
            ORDER BY COALESCE(vencimento, created_at) DESC, id DESC
            LIMIT 500
        """, ()),
        'contas-receber': ("""
            SELECT descricao, categoria, valor, vencimento, data_pagamento, status, origem, created_at
            FROM financeiro_lancamentos
            WHERE tipo='receber'
            ORDER BY COALESCE(vencimento, created_at) DESC, id DESC
            LIMIT 500
        """, ()),
        'estoque': ("""
            SELECT nome, categoria, unidade, estoque_atual, estoque_minimo, custo_unitario, ativo
            FROM ingredientes
            ORDER BY nome
            LIMIT 500
        """, ()),
        'produtos': ("""
            SELECT nome, categoria, preco_venda, custo_total, lucro_estimado, margem_percentual, ativo
            FROM produtos_finais
            ORDER BY nome
            LIMIT 500
        """, ()),
        'producao': ("""
            SELECT produto_final_nome, receita_nome, lote, quantidade_produzida, saldo_atual, data_producao, data_validade, status_qualidade
            FROM lotes_producao
            ORDER BY data_producao DESC, id DESC
            LIMIT 500
        """, ()),
        'compras': ("""
            SELECT numero, fornecedor, status, total, previsao_entrega, criado_em, recebido_em
            FROM compras_pedidos
            ORDER BY id DESC
            LIMIT 500
        """, ()),
        'qualidade': ("""
            SELECT tipo, origem, lote, op_codigo, item, status, observacao, data_registro
            FROM qualidade_registros
            ORDER BY data_registro DESC, id DESC
            LIMIT 500
        """, ()),
        'clientes': ("""
            SELECT nome, documento, telefone, email, cidade, uf, total_compras, ultima_compra, ativo
            FROM clientes
            ORDER BY nome
            LIMIT 500
        """, ()),
        'fornecedores': ("""
            SELECT nome, documento, telefone, email, cidade, uf, categoria, prazo_pagamento, ativo
            FROM fornecedores
            ORDER BY nome
            LIMIT 500
        """, ()),
        'cmv': ("""
            SELECT nome, custo_base, embalagem, custo_fixo_unitario, custo_variavel_percentual,
                   margem_desejada_percentual, preco_praticado, cmv_real, preco_minimo,
                   preco_sugerido, lucro_real, margem_real_percentual, markup, status
            FROM produtos_finais
            ORDER BY nome
            LIMIT 500
        """, ()),
    }
    sql, params = consultas.get(modulo, consultas.get('financeiro'))
    tabela_por_modulo = {
        'financeiro': 'financeiro_lancamentos', 'contas-pagar': 'financeiro_lancamentos', 'contas-receber': 'financeiro_lancamentos',
        'estoque': 'ingredientes', 'produtos': 'produtos_finais', 'producao': 'lotes_producao', 'compras': 'compras_pedidos',
        'qualidade': 'qualidade_registros', 'clientes': 'clientes', 'fornecedores': 'fornecedores', 'cmv': 'produtos_finais'
    }
    tabela = tabela_por_modulo.get(modulo, 'financeiro_lancamentos')
    if not _exportacao_tabela_existe(cursor, tabela):
        return []
    try:
        cursor.execute(sql, params)
        return [dict(linha) for linha in cursor.fetchall()]
    except Exception:
        return []


@app.route('/api/exportar_modulo/<modulo>')
def api_exportar_modulo(modulo):
    """Exporta dados reais por módulo em CSV para uso em Excel, sem misturar páginas/submenus."""
    criar_tabelas()
    formato = (request.args.get('formato') or 'csv').lower()
    conn = conectar_banco()
    cursor = conn.cursor()
    linhas = _exportacao_consulta_modulo(cursor, modulo)
    conn.close()

    if formato == 'json':
        return jsonify({'status': 'sucesso', 'modulo': modulo, 'total': len(linhas), 'dados': linhas})

    output = StringIO()
    if linhas:
        campos = list(linhas[0].keys())
        writer = csv.DictWriter(output, fieldnames=campos, delimiter=';')
        writer.writeheader()
        for linha in linhas:
            writer.writerow({campo: linha.get(campo, '') for campo in campos})
    else:
        output.write('mensagem\nNenhum dado encontrado para este módulo\n')

    nome_arquivo = f"saude_nutri_{(modulo or 'relatorio').replace('-', '_')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename={nome_arquivo}'}
    )

@app.route('/api/configuracoes_sistema', methods=['GET'])
def api_configuracoes_sistema_get():
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        usuario = cursor.execute("""
            SELECT usuario, email, perfil, status, ultimo_acesso, updated_at
            FROM usuarios_sistema
            ORDER BY CASE WHEN usuario='administrador' THEN 0 ELSE 1 END, id ASC
            LIMIT 1
        """).fetchone()
        perfil = cursor.execute("SELECT razao_social, nome_fantasia, documento, email, telefone FROM perfil_empresa WHERE id=1").fetchone()
        total_usuarios = cursor.execute("SELECT COUNT(*) AS total FROM usuarios_sistema").fetchone()['total'] or 0
        integracoes_config = cursor.execute("SELECT COUNT(*) AS total FROM integracoes_config WHERE status='Configurado'").fetchone()['total'] or 0
        return jsonify({
            'status': 'sucesso',
            'acesso': dict(usuario) if usuario else {},
            'perfil_empresa': dict(perfil) if perfil else {},
            'visual': {
                'logo_url': _config_get(cursor, 'logo_url'),
                'favicon_url': _config_get(cursor, 'favicon_url'),
            },
            'resumo': {
                'usuarios': total_usuarios,
                'integracoes_configuradas': integracoes_config,
                'perfil_preenchido': bool(perfil and (perfil['documento'] or perfil['nome_fantasia'])),
                'identidade_visual': bool(_config_get(cursor, 'logo_url') or _config_get(cursor, 'favicon_url')),
            }
        })
    finally:
        conn.close()


@app.route('/api/configuracoes_sistema', methods=['POST'])
def api_configuracoes_sistema_post():
    criar_tabelas()
    dados = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    usuario = (dados.get('usuario') or 'administrador').strip() or 'administrador'
    email = (dados.get('email') or '').strip()
    senha = (dados.get('senha') or '').strip()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        existente = cursor.execute("SELECT id FROM usuarios_sistema WHERE usuario=?", (usuario,)).fetchone()
        senha_hash = generate_password_hash(senha) if senha else None
        if existente:
            if senha_hash:
                cursor.execute("""
                    UPDATE usuarios_sistema SET email=?, senha_hash=?, updated_at=? WHERE usuario=?
                """, (email, senha_hash, agora_brasilia(), usuario))
            else:
                cursor.execute("UPDATE usuarios_sistema SET email=?, updated_at=? WHERE usuario=?", (email, agora_brasilia(), usuario))
        else:
            cursor.execute("""
                INSERT INTO usuarios_sistema (usuario, email, senha_hash, perfil, status, created_at, updated_at)
                VALUES (?, ?, ?, 'Administrador', 'Ativo', ?, ?)
            """, (usuario, email, senha_hash, agora_brasilia(), agora_brasilia()))

        logo_url = _salvar_upload_config(request.files.get('logo') if request.files else None, 'logo')
        favicon_url = _salvar_upload_config(request.files.get('favicon') if request.files else None, 'favicon')
        if logo_url:
            _config_set(cursor, 'logo_url', logo_url, 'arquivo')
        if favicon_url:
            _config_set(cursor, 'favicon_url', favicon_url, 'arquivo')

        conn.commit()
        return jsonify({'status': 'sucesso', 'mensagem': 'Configurações salvas no banco de dados.'})
    finally:
        conn.close()




def _usuario_sistema_dict(row):
    if not row:
        return {}
    dados = dict(row)
    dados.pop('senha_hash', None)
    return dados


@app.route('/api/usuarios_sistema', methods=['GET'])
def api_usuarios_sistema_listar():
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        rows = cursor.execute("""
            SELECT id, usuario, email, perfil, status, permissoes, observacao, ultimo_acesso, created_at, updated_at
            FROM usuarios_sistema
            ORDER BY CASE WHEN usuario='administrador' THEN 0 ELSE 1 END, usuario COLLATE NOCASE
        """).fetchall()
        return jsonify({'status': 'sucesso', 'usuarios': [_usuario_sistema_dict(r) for r in rows]})
    finally:
        conn.close()


@app.route('/api/usuarios_sistema', methods=['POST'])
def api_usuarios_sistema_salvar():
    criar_tabelas()
    dados = request.get_json(silent=True) or request.form.to_dict() or {}
    usuario = (dados.get('usuario') or '').strip()
    email = (dados.get('email') or '').strip()
    perfil = (dados.get('perfil') or 'Operador').strip() or 'Operador'
    status = (dados.get('status') or 'Ativo').strip() or 'Ativo'
    permissoes = (dados.get('permissoes') or '').strip() or ('todos' if perfil.lower() == 'administrador' else '')
    observacao = (dados.get('observacao') or '').strip()
    senha = (dados.get('senha') or '').strip()
    usuario_id = dados.get('id')
    if not usuario:
        return jsonify({'status': 'erro', 'mensagem': 'Informe o usuário.'}), 400
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        senha_hash = generate_password_hash(senha) if senha else None
        if usuario_id:
            existente = cursor.execute('SELECT id, usuario FROM usuarios_sistema WHERE id=?', (usuario_id,)).fetchone()
            if not existente:
                return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado.'}), 404
            conflito = cursor.execute('SELECT id FROM usuarios_sistema WHERE usuario=? AND id<>?', (usuario, usuario_id)).fetchone()
            if conflito:
                return jsonify({'status': 'erro', 'mensagem': 'Já existe outro usuário com este nome.'}), 400
            if senha_hash:
                cursor.execute("""
                    UPDATE usuarios_sistema
                    SET usuario=?, email=?, perfil=?, status=?, permissoes=?, observacao=?, senha_hash=?, updated_at=?
                    WHERE id=?
                """, (usuario, email, perfil, status, permissoes, observacao, senha_hash, agora_brasilia(), usuario_id))
            else:
                cursor.execute("""
                    UPDATE usuarios_sistema
                    SET usuario=?, email=?, perfil=?, status=?, permissoes=?, observacao=?, updated_at=?
                    WHERE id=?
                """, (usuario, email, perfil, status, permissoes, observacao, agora_brasilia(), usuario_id))
        else:
            cursor.execute("""
                INSERT INTO usuarios_sistema (usuario, email, senha_hash, perfil, status, permissoes, observacao, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (usuario, email, senha_hash, perfil, status, permissoes, observacao, agora_brasilia(), agora_brasilia()))
        conn.commit()
        return jsonify({'status': 'sucesso', 'mensagem': 'Usuário salvo com sucesso.'})
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify({'status': 'erro', 'mensagem': 'Usuário já cadastrado.'}), 400
    finally:
        conn.close()


@app.route('/api/usuarios_sistema/<int:usuario_id>/status', methods=['POST'])
def api_usuarios_sistema_status(usuario_id):
    criar_tabelas()
    dados = request.get_json(silent=True) or request.form.to_dict() or {}
    novo_status = (dados.get('status') or '').strip() or 'Inativo'
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        row = cursor.execute('SELECT usuario FROM usuarios_sistema WHERE id=?', (usuario_id,)).fetchone()
        if not row:
            return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado.'}), 404
        if row['usuario'] == 'administrador' and novo_status != 'Ativo':
            return jsonify({'status': 'erro', 'mensagem': 'O administrador principal não pode ser inativado.'}), 400
        cursor.execute('UPDATE usuarios_sistema SET status=?, updated_at=? WHERE id=?', (novo_status, agora_brasilia(), usuario_id))
        conn.commit()
        return jsonify({'status': 'sucesso', 'mensagem': 'Status atualizado.'})
    finally:
        conn.close()


@app.route('/api/login_sistema', methods=['POST'])
def api_login_sistema():
    """Validação de login para uso futuro sem bloquear o ERP nesta etapa incremental."""
    criar_tabelas()
    dados = request.get_json(silent=True) or request.form.to_dict() or {}
    usuario = (dados.get('usuario') or '').strip()
    senha = (dados.get('senha') or '').strip()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        row = cursor.execute('SELECT * FROM usuarios_sistema WHERE usuario=? AND status="Ativo"', (usuario,)).fetchone()
        if not row:
            return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado ou inativo.'}), 401
        senha_hash = row['senha_hash'] if 'senha_hash' in row.keys() else None
        if senha_hash and not check_password_hash(senha_hash, senha):
            return jsonify({'status': 'erro', 'mensagem': 'Senha inválida.'}), 401
        cursor.execute('UPDATE usuarios_sistema SET ultimo_acesso=?, updated_at=? WHERE id=?', (agora_brasilia(), agora_brasilia(), row['id']))
        conn.commit()
        return jsonify({'status': 'sucesso', 'usuario': _usuario_sistema_dict(row)})
    finally:
        conn.close()


@app.route('/api/backup_erp')
def api_backup_erp():
    """Exporta uma cópia JSON simples do banco atual para auditoria/segurança.
    Não altera dados existentes.
    """
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        tabelas = [r['name'] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        dados = {}
        totais = {}
        for tabela in tabelas:
            try:
                linhas = cursor.execute(f'SELECT * FROM "{tabela}"').fetchall()
                dados[tabela] = [dict(l) for l in linhas]
                totais[tabela] = len(linhas)
            except Exception as exc:
                dados[tabela] = {'erro': str(exc)}
                totais[tabela] = 0
        payload = {
            'sistema': 'Wevvo ERP/PDV',
            'gerado_em': agora_brasilia(),
            'banco': os.path.basename(BANCO),
            'totais': totais,
            'dados': dados,
        }
        nome = 'saude_nutri_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.json'
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            mimetype='application/json; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={nome}'}
        )
    finally:
        conn.close()


@app.route('/api/diagnostico_integridade')
def api_diagnostico_integridade():
    """Resumo rápido de integridade para verificar módulos sem dados antes de novas etapas."""
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        tabelas_chave = ['ingredientes','receitas','produtos_acabados','lotes','compras','contas_pagar','contas_receber','movimentacoes_estoque','qualidade_registros']
        resumo = []
        for tabela in tabelas_chave:
            existe = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
            total = 0
            if existe:
                try:
                    total = cursor.execute(f'SELECT COUNT(*) AS total FROM "{tabela}"').fetchone()['total'] or 0
                except Exception:
                    total = 0
            resumo.append({'tabela': tabela, 'existe': bool(existe), 'registros': total})
        pendencias = [r for r in resumo if (not r['existe']) or r['registros'] == 0]
        return jsonify({'status':'sucesso','gerado_em':agora_brasilia(),'resumo':resumo,'pendencias':pendencias})
    finally:
        conn.close()


@app.route('/api/validacao_erp')
def api_validacao_erp():
    """Validação técnica geral: confere tabelas, rotas principais e vínculos críticos sem alterar dados."""
    criar_tabelas()
    conn = conectar_banco(); cursor = conn.cursor()
    try:
        obrigatorias = [
            'ingredientes','receitas','receita_itens','receita_ingredientes',
            'produtos_finais','produtos_acabados','estoque_produto_acabado','lotes',
            'producoes','producao','compras_pedidos','compras','financeiro_lancamentos',
            'contas_pagar','contas_receber','clientes','fornecedores',
            'qualidade_checklists','qualidade_registros','movimentacoes_estoque','usuarios_sistema',
            'erp_schema_mapa','erp_auditoria_banco'
        ]
        tabelas = []
        for nome in obrigatorias:
            existe = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone()
            registros = 0
            colunas = []
            if existe:
                try:
                    registros = cursor.execute(f'SELECT COUNT(*) AS total FROM "{nome}"').fetchone()['total'] or 0
                    colunas = [r['name'] for r in cursor.execute(f'PRAGMA table_info("{nome}")').fetchall()]
                except Exception:
                    pass
            tabelas.append({'tabela': nome, 'existe': bool(existe), 'registros': registros, 'colunas': len(colunas)})

        vinculos = []
        def checar(nome, sql):
            try:
                total = cursor.execute(sql).fetchone()['total'] or 0
                vinculos.append({'verificacao': nome, 'status': 'ok' if total == 0 else 'atencao', 'pendencias': total})
            except Exception as e:
                vinculos.append({'verificacao': nome, 'status': 'erro', 'pendencias': 0, 'mensagem': str(e)})

        checar('Receitas com ingrediente inexistente', """
            SELECT COUNT(*) AS total FROM receita_ingredientes ri
            LEFT JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE i.id IS NULL
        """)
        checar('Produtos acabados sem receita vinculada', """
            SELECT COUNT(*) AS total FROM produtos_acabados p
            LEFT JOIN receitas r ON r.id = p.receita_id
            WHERE p.receita_id IS NOT NULL AND r.id IS NULL
        """)
        checar('Lotes sem produto acabado', """
            SELECT COUNT(*) AS total FROM lotes l
            LEFT JOIN produtos_acabados p ON p.id = l.produto_final_id
            WHERE l.produto_final_id IS NOT NULL AND p.id IS NULL
        """)
        checar('Financeiro automático sem origem', """
            SELECT COUNT(*) AS total FROM financeiro_lancamentos
            WHERE origem IS NULL OR TRIM(origem) = ''
        """)

        rotas_criticas = [
            '/', '/dashboard', '/financeiro_status', '/api/diagnostico_integridade',
            '/api/backup_erp', '/api/validacao_erp'
        ]
        rotas_existentes = set(str(r.rule) for r in app.url_map.iter_rules())
        rotas = [{'rota': r, 'existe': r in rotas_existentes} for r in rotas_criticas]

        alertas = []
        alertas.extend([f"Tabela ausente: {t['tabela']}" for t in tabelas if not t['existe']])
        alertas.extend([f"Vínculo com pendência: {v['verificacao']} ({v.get('pendencias',0)})" for v in vinculos if v['status'] != 'ok'])
        alertas.extend([f"Rota crítica ausente: {r['rota']}" for r in rotas if not r['existe']])

        return jsonify({
            'status': 'sucesso' if not alertas else 'atencao',
            'gerado_em': agora_brasilia(),
            'tabelas': tabelas,
            'vinculos': vinculos,
            'rotas': rotas,
            'alertas': alertas,
            'resumo': {
                'tabelas_ok': sum(1 for t in tabelas if t['existe']),
                'tabelas_total': len(tabelas),
                'vinculos_ok': sum(1 for v in vinculos if v['status'] == 'ok'),
                'vinculos_total': len(vinculos),
                'rotas_ok': sum(1 for r in rotas if r['existe']),
                'rotas_total': len(rotas),
            }
        })
    finally:
        conn.close()


# Etapa funcional: submódulos de Estoque com dados próprios e sem reaproveitar páginas genéricas.
def _safe_float_erp(valor, padrao=0.0):
    try:
        if valor is None:
            return float(padrao)
        if isinstance(valor, (int, float)):
            return float(valor)
        texto = str(valor).strip().replace('R$', '').replace(' ', '')
        if ',' in texto and '.' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        elif ',' in texto:
            texto = texto.replace(',', '.')
        return float(texto or padrao)
    except Exception:
        return float(padrao)


def _safe_lista_erp(cursor, sql, params=()):
    try:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []


def _safe_valor_erp(cursor, sql, params=(), padrao=0):
    try:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        if not row:
            return padrao
        d = dict(row)
        return next(iter(d.values())) if d else padrao
    except Exception:
        return padrao


def _linhas_estoque_padrao(lista, col1='nome', col2='status', col3='valor', limite=30):
    linhas = []
    for item in (lista or [])[:limite]:
        linhas.append([
            item.get(col1) or item.get('produto_nome') or item.get('ingrediente_nome') or item.get('descricao') or item.get('nome') or '-',
            item.get(col2) or item.get('tipo') or item.get('lote') or item.get('status') or '-',
            item.get(col3) if item.get(col3) is not None else item.get('saldo_atual') if item.get('saldo_atual') is not None else item.get('quantidade') if item.get('quantidade') is not None else '-'
        ])
    return linhas


def _estoque_submodulo_payload(cursor, modulo):
    """Payload específico por submenu de estoque: lançamentos, conferência, depósitos e rastreabilidade."""
    modulo = (modulo or '').strip().lower()

    ingredientes = _safe_lista_erp(cursor, """
        SELECT id, nome, unidade,
               COALESCE(estoque_atual, 0) AS estoque_atual,
               COALESCE(estoque_minimo, 0) AS estoque_minimo,
               COALESCE(preco_kg, 0) AS custo_unitario,
               CASE WHEN COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0) THEN 'Baixo' ELSE 'OK' END AS status
        FROM ingredientes
        ORDER BY nome COLLATE NOCASE ASC
        LIMIT 500
    """)
    produtos = _safe_lista_erp(cursor, """
        SELECT id, nome,
               COALESCE(estoque_atual, 0) AS estoque_atual,
               COALESCE(estoque_minimo, 0) AS estoque_minimo,
               COALESCE(preco_venda, 0) AS preco_venda,
               CASE WHEN COALESCE(estoque_atual,0) <= COALESCE(estoque_minimo,0) THEN 'Baixo' ELSE 'OK' END AS status
        FROM produtos_finais
        WHERE COALESCE(ativo, 1) = 1
        ORDER BY nome COLLATE NOCASE ASC
        LIMIT 500
    """)
    movimentos = _safe_lista_erp(cursor, """
        SELECT id, ingrediente_nome, tipo, quantidade, unidade, observacao, created_at
        FROM movimentacoes_estoque
        ORDER BY id DESC
        LIMIT 200
    """)
    lotes = _safe_lista_erp(cursor, """
        SELECT id, produto_nome, lote, codigo_lote, validade, saldo_atual, quantidade, status, created_at
        FROM lotes_producao
        ORDER BY COALESCE(validade, created_at) ASC, id DESC
        LIMIT 200
    """)
    if not lotes:
        lotes = _safe_lista_erp(cursor, """
            SELECT id, produto_nome, lote, validade, saldo AS saldo_atual, quantidade, status, created_at
            FROM lotes
            ORDER BY COALESCE(validade, created_at) ASC, id DESC
            LIMIT 200
        """)

    estoque_baixo_ing = [i for i in ingredientes if _safe_float_erp(i.get('estoque_atual')) <= _safe_float_erp(i.get('estoque_minimo'))]
    estoque_baixo_prod = [p for p in produtos if _safe_float_erp(p.get('estoque_atual')) <= _safe_float_erp(p.get('estoque_minimo'))]
    valor_ingredientes = sum(_safe_float_erp(i.get('estoque_atual')) * _safe_float_erp(i.get('custo_unitario')) for i in ingredientes)
    valor_produtos = sum(_safe_float_erp(p.get('estoque_atual')) * _safe_float_erp(p.get('preco_venda')) for p in produtos)
    hoje = hoje_brasilia()
    lotes_vencendo = [l for l in lotes if (l.get('validade') or '9999-99-99') <= hoje and _safe_float_erp(l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade')) > 0]

    if modulo == 'lancamentos-estoque':
        return {
            'titulo': 'Lançamentos de estoque',
            'resumo': {'kpi1': len(ingredientes), 'kpi2': len(estoque_baixo_ing), 'kpi3': len(movimentos), 'kpi4': round(valor_ingredientes + valor_produtos, 2)},
            'linhas1': [[m.get('ingrediente_nome') or '-', m.get('tipo') or '-', f"{m.get('quantidade') or 0} {m.get('unidade') or ''}".strip()] for m in movimentos[:30]],
            'linhas2': [[i.get('nome') or '-', i.get('status') or '-', f"{i.get('estoque_atual') or 0} / mín. {i.get('estoque_minimo') or 0}"] for i in ingredientes[:30]],
        }
    if modulo == 'conferencia-estoque':
        pendencias = estoque_baixo_ing + estoque_baixo_prod
        return {
            'titulo': 'Conferência de estoque',
            'resumo': {'kpi1': len(pendencias), 'kpi2': len(ingredientes), 'kpi3': len(produtos), 'kpi4': len(lotes)},
            'linhas1': [[x.get('nome') or '-', x.get('status') or 'Verificar', f"{x.get('estoque_atual') or 0} / mín. {x.get('estoque_minimo') or 0}"] for x in pendencias[:30]],
            'linhas2': [[l.get('produto_nome') or '-', l.get('lote') or l.get('codigo_lote') or '-', l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade') or 0] for l in lotes[:30]],
        }
    if modulo == 'depositos':
        return {
            'titulo': 'Depósitos',
            'resumo': {'kpi1': 1, 'kpi2': len(ingredientes), 'kpi3': len(produtos), 'kpi4': round(valor_ingredientes + valor_produtos, 2)},
            'linhas1': [['Depósito principal', 'Ativo', f"R$ {round(valor_ingredientes + valor_produtos, 2):.2f}"], ['Ingredientes', 'Saldo físico', len(ingredientes)], ['Produtos acabados', 'Saldo por lote', len(produtos)]],
            'linhas2': [[l.get('produto_nome') or '-', l.get('lote') or l.get('codigo_lote') or '-', l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade') or 0] for l in lotes[:30]],
        }
    if modulo in ('lotes-rastreabilidade', 'qualidade-validades'):
        return {
            'titulo': 'Lotes e rastreabilidade',
            'resumo': {'kpi1': len(lotes), 'kpi2': len(lotes_vencendo), 'kpi3': sum(_safe_float_erp(l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade')) for l in lotes), 'kpi4': len({l.get('produto_nome') for l in lotes if l.get('produto_nome')})},
            'linhas1': [[l.get('produto_nome') or '-', l.get('lote') or l.get('codigo_lote') or '-', l.get('validade') or '-'] for l in lotes[:30]],
            'linhas2': [[l.get('produto_nome') or '-', 'Saldo', l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade') or 0] for l in lotes_vencendo[:30]],
        }
    return {
        'titulo': 'Estoque',
        'resumo': {'kpi1': len(ingredientes), 'kpi2': len(estoque_baixo_ing), 'kpi3': len(movimentos), 'kpi4': round(valor_ingredientes + valor_produtos, 2)},
        'linhas1': [[i.get('nome') or '-', i.get('status') or '-', f"{i.get('estoque_atual') or 0} / mín. {i.get('estoque_minimo') or 0}"] for i in ingredientes[:30]],
        'linhas2': [[l.get('produto_nome') or '-', l.get('lote') or l.get('codigo_lote') or '-', l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade') or 0] for l in lotes[:30]],
    }


@app.route('/api/estoque_submodulo/<modulo>')
def api_estoque_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        payload = _estoque_submodulo_payload(cursor, modulo)
        payload['status'] = 'sucesso'
        payload['modulo'] = modulo
        return jsonify(payload)
    finally:
        conn.close()



def _producao_submodulo_payload(cursor, modulo):
    """Payload específico por submenu de produção/PCP: OPs, apontamentos, lotes, capacidade e consumo."""
    modulo = (modulo or '').strip().lower()

    ordens = _safe_lista_erp(cursor, """
        SELECT id, codigo, receita_nome, produto_final_nome, quantidade_planejada, quantidade_produzida,
               data_planejada, turno, responsavel, prioridade, status, lote, created_at, updated_at
        FROM pcp_ordens_producao
        ORDER BY
            CASE status
                WHEN 'Planejada' THEN 1 WHEN 'Liberada' THEN 2 WHEN 'Em produção' THEN 3
                WHEN 'Concluída' THEN 4 WHEN 'Cancelada' THEN 5 ELSE 9 END,
            COALESCE(data_planejada, created_at) ASC, id DESC
        LIMIT 300
    """)
    producoes = _safe_lista_erp(cursor, """
        SELECT id, receita_nome, produto_final_nome, quantidade, custo_total, custo_unitario,
               lote, data_fabricacao, data_validade, created_at
        FROM producoes
        ORDER BY id DESC
        LIMIT 300
    """)
    lotes = _safe_lista_erp(cursor, """
        SELECT id, produto_nome, lote, codigo_lote, quantidade, saldo_atual, validade, status, created_at
        FROM lotes_producao
        ORDER BY id DESC
        LIMIT 300
    """)
    if not lotes:
        lotes = _safe_lista_erp(cursor, """
            SELECT id, produto_nome, lote, quantidade, saldo AS saldo_atual, validade, status, created_at
            FROM lotes
            ORDER BY id DESC
            LIMIT 300
        """)
    consumos = _safe_lista_erp(cursor, """
        SELECT id, ingrediente_nome, tipo, quantidade, unidade, observacao, created_at
        FROM movimentacoes_estoque
        WHERE LOWER(COALESCE(tipo,'')) LIKE '%produção%' OR LOWER(COALESCE(observacao,'')) LIKE '%produção%'
        ORDER BY id DESC
        LIMIT 300
    """)

    abertas = [o for o in ordens if (o.get('status') or '') in ('Planejada','Liberada','Em produção')]
    concluidas = [o for o in ordens if (o.get('status') or '') == 'Concluída']
    canceladas = [o for o in ordens if (o.get('status') or '') == 'Cancelada']
    qtd_planejada = sum(_safe_float_erp(o.get('quantidade_planejada')) for o in abertas)
    qtd_produzida = sum(_safe_float_erp(p.get('quantidade')) for p in producoes)
    custo_produzido = sum(_safe_float_erp(p.get('custo_total')) for p in producoes)
    saldo_lotes = sum(_safe_float_erp(l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade')) for l in lotes)
    hoje = hoje_brasilia()
    producoes_hoje = [p for p in producoes if (p.get('created_at') or '').startswith(hoje) or (p.get('data_fabricacao') or '') == hoje]

    if modulo in ('ordens-producao', 'producao', 'pcp'):
        return {
            'titulo': 'Ordens de produção',
            'resumo': {'kpi1': len(ordens), 'kpi2': len(abertas), 'kpi3': len(concluidas), 'kpi4': len(lotes)},
            'linhas1': [[o.get('codigo') or f"OP-{o.get('id')}", o.get('status') or '-', f"{o.get('quantidade_planejada') or 0} - {o.get('receita_nome') or '-'}"] for o in ordens[:30]],
            'linhas2': [[p.get('receita_nome') or p.get('produto_final_nome') or '-', p.get('lote') or '-', f"{p.get('quantidade') or 0} | R$ {_safe_float_erp(p.get('custo_total')):.2f}"] for p in producoes[:30]],
        }
    if modulo in ('apontamentos-producao', 'producoes-realizadas'):
        return {
            'titulo': 'Apontamentos de produção',
            'resumo': {'kpi1': len(producoes), 'kpi2': len(producoes_hoje), 'kpi3': round(qtd_produzida, 3), 'kpi4': round(custo_produzido, 2)},
            'linhas1': [[p.get('receita_nome') or '-', p.get('lote') or '-', f"{p.get('quantidade') or 0} un"] for p in producoes[:30]],
            'linhas2': [[p.get('produto_final_nome') or p.get('receita_nome') or '-', 'Custo unitário', f"R$ {_safe_float_erp(p.get('custo_unitario')):.2f}"] for p in producoes[:30]],
        }
    if modulo in ('consumo-insumos', 'baixa-insumos'):
        return {
            'titulo': 'Consumo de insumos',
            'resumo': {'kpi1': len(consumos), 'kpi2': round(sum(_safe_float_erp(c.get('quantidade')) for c in consumos), 3), 'kpi3': len({c.get('ingrediente_nome') for c in consumos if c.get('ingrediente_nome')}), 'kpi4': len(producoes)},
            'linhas1': [[c.get('ingrediente_nome') or '-', c.get('tipo') or 'Baixa produção', f"{c.get('quantidade') or 0} {c.get('unidade') or ''}".strip()] for c in consumos[:30]],
            'linhas2': [[c.get('ingrediente_nome') or '-', c.get('created_at') or '-', c.get('observacao') or '-'] for c in consumos[:30]],
        }
    if modulo in ('lotes-producao', 'lotes-rastreabilidade'):
        return {
            'titulo': 'Lotes de produção',
            'resumo': {'kpi1': len(lotes), 'kpi2': round(saldo_lotes, 3), 'kpi3': len([l for l in lotes if (l.get('status') or '').lower() not in ('finalizado','zerado','encerrado')]), 'kpi4': len({l.get('produto_nome') for l in lotes if l.get('produto_nome')})},
            'linhas1': [[l.get('produto_nome') or '-', l.get('lote') or l.get('codigo_lote') or '-', l.get('validade') or '-'] for l in lotes[:30]],
            'linhas2': [[l.get('produto_nome') or '-', l.get('status') or 'Ativo', l.get('saldo_atual') if l.get('saldo_atual') is not None else l.get('quantidade') or 0] for l in lotes[:30]],
        }
    if modulo in ('capacidade-producao', 'planejamento-producao'):
        return {
            'titulo': 'Capacidade de produção',
            'resumo': {'kpi1': len(abertas), 'kpi2': round(qtd_planejada, 3), 'kpi3': len(canceladas), 'kpi4': len(producoes_hoje)},
            'linhas1': [[o.get('codigo') or f"OP-{o.get('id')}", o.get('data_planejada') or '-', f"{o.get('turno') or '-'} | {o.get('responsavel') or '-'}"] for o in abertas[:30]],
            'linhas2': [[o.get('receita_nome') or '-', o.get('prioridade') or 'Normal', o.get('status') or '-'] for o in ordens[:30]],
        }
    return {
        'titulo': 'Produção',
        'resumo': {'kpi1': len(ordens), 'kpi2': len(abertas), 'kpi3': len(producoes), 'kpi4': len(lotes)},
        'linhas1': [[o.get('codigo') or f"OP-{o.get('id')}", o.get('status') or '-', o.get('receita_nome') or '-'] for o in ordens[:30]],
        'linhas2': [[p.get('receita_nome') or '-', p.get('lote') or '-', p.get('quantidade') or 0] for p in producoes[:30]],
    }


@app.route('/api/producao_submodulo/<modulo>')
def api_producao_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        payload = _producao_submodulo_payload(cursor, modulo)
        payload['status'] = 'sucesso'
        payload['modulo'] = modulo
        return jsonify(payload)
    finally:
        conn.close()


# ============================================================
# Etapa seguinte: submódulos de Vendas com dados próprios
# ============================================================
def _vendas_submodulo_payload(cursor, modulo):
    """Retorna dados específicos para cada submenu de Vendas/Logística, sem reaproveitar página genérica."""
    modulo = (modulo or 'pedidos-venda').strip().lower()

    def tabela_existe(nome):
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,))
            return cursor.fetchone() is not None
        except Exception:
            return False

    def lista(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
        except Exception:
            return []

    def unico(sql, params=(), padrao=0):
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return padrao
            if isinstance(row, dict):
                return list(row.values())[0]
            return row[0]
        except Exception:
            return padrao

    vendas = []
    if tabela_existe('vendas_erp'):
        vendas = lista("""
            SELECT id, origem, produto_final_nome, cliente_nome, lote, quantidade,
                   valor_unitario, valor_total, cmv_total, lucro_estimado,
                   forma_pagamento, status_financeiro, observacao, created_at
            FROM vendas_erp
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 200
        """)
    total_vendas = len(vendas)
    faturamento = sum(float(v.get('valor_total') or 0) for v in vendas)
    cmv = sum(float(v.get('cmv_total') or 0) for v in vendas)
    lucro = sum(float(v.get('lucro_estimado') or 0) for v in vendas)
    pendentes = len([v for v in vendas if str(v.get('status_financeiro') or '').lower() in ('pendente','a receber','aberto')])
    recebidas = len([v for v in vendas if str(v.get('status_financeiro') or '').lower() in ('recebido','pago')])

    produtos = []
    if tabela_existe('produtos_finais'):
        produtos = lista("""
            SELECT id, nome, categoria, preco_praticado, preco_sugerido, margem_real_percentual, status
            FROM produtos_finais
            ORDER BY nome
            LIMIT 200
        """)

    clientes = []
    if tabela_existe('clientes'):
        clientes = lista("""
            SELECT id, nome, telefone, email, cidade, uf, total_compras, ultima_compra, ativo
            FROM clientes
            ORDER BY COALESCE(ultima_compra,'') DESC, nome
            LIMIT 200
        """)

    if modulo == 'pedidos-venda':
        return {
            'titulo': 'Pedidos de venda',
            'resumo': {'kpi1': total_vendas, 'kpi2': round(faturamento,2), 'kpi3': round(faturamento/total_vendas,2) if total_vendas else 0, 'kpi4': pendentes},
            'linhas1': [[f"Venda #{v.get('id')}", v.get('produto_final_nome') or '-', f"Qtd {v.get('quantidade') or 0} | R$ {float(v.get('valor_total') or 0):.2f}"] for v in vendas[:40]],
            'linhas2': [[v.get('cliente_nome') or 'Cliente não informado', v.get('status_financeiro') or 'Recebido', v.get('created_at') or '-'] for v in vendas[:40]],
        }
    if modulo == 'gestao-anuncios':
        publicados = [p for p in produtos if float(p.get('preco_praticado') or p.get('preco_sugerido') or 0) > 0]
        sem_preco = [p for p in produtos if float(p.get('preco_praticado') or p.get('preco_sugerido') or 0) <= 0]
        return {
            'titulo': 'Gestão de anúncios',
            'resumo': {'kpi1': len(produtos), 'kpi2': len(publicados), 'kpi3': len(sem_preco), 'kpi4': len({p.get('categoria') for p in produtos if p.get('categoria')})},
            'linhas1': [[p.get('nome') or '-', p.get('categoria') or 'Sem categoria', f"R$ {float(p.get('preco_praticado') or p.get('preco_sugerido') or 0):.2f}"] for p in publicados[:40]],
            'linhas2': [[p.get('nome') or '-', p.get('status') or 'Sem preço', 'Revisar preço/anúncio'] for p in sem_preco[:40]],
        }
    if modulo == 'notas-fiscais-saida':
        emitidas = [v for v in vendas if 'nf' in str(v.get('origem') or '').lower()]
        return {
            'titulo': 'Notas fiscais de saída',
            'resumo': {'kpi1': len(emitidas), 'kpi2': len(emitidas), 'kpi3': max(total_vendas-len(emitidas),0), 'kpi4': round(sum(float(v.get('valor_total') or 0) for v in emitidas),2)},
            'linhas1': [[f"NF saída venda #{v.get('id')}", v.get('cliente_nome') or '-', f"R$ {float(v.get('valor_total') or 0):.2f}"] for v in emitidas[:40]],
            'linhas2': [[f"Venda #{v.get('id')}", 'Pendente emissão', v.get('produto_final_nome') or '-'] for v in vendas if v not in emitidas][:40],
        }
    if modulo == 'nfce':
        caixa = [v for v in vendas if str(v.get('origem') or '').lower() in ('balcao','frente de caixa','pdv','erp')]
        return {
            'titulo': 'NFC-e / vendas balcão',
            'resumo': {'kpi1': len(caixa), 'kpi2': round(sum(float(v.get('valor_total') or 0) for v in caixa),2), 'kpi3': 0, 'kpi4': recebidas},
            'linhas1': [[f"Cupom venda #{v.get('id')}", v.get('produto_final_nome') or '-', f"R$ {float(v.get('valor_total') or 0):.2f}"] for v in caixa[:40]],
            'linhas2': [[v.get('forma_pagamento') or 'Forma não informada', v.get('status_financeiro') or 'Recebido', v.get('created_at') or '-'] for v in caixa[:40]],
        }
    if modulo == 'frente-caixa':
        entradas = sum(float(v.get('valor_total') or 0) for v in vendas if str(v.get('status_financeiro') or '').lower() in ('recebido','pago',''))
        return {
            'titulo': 'Frente de caixa',
            'resumo': {'kpi1': total_vendas, 'kpi2': round(entradas,2), 'kpi3': round(faturamento-entradas,2), 'kpi4': round(entradas,2)},
            'linhas1': [[f"Venda #{v.get('id')}", v.get('forma_pagamento') or 'Pagamento', f"R$ {float(v.get('valor_total') or 0):.2f}"] for v in vendas[:40]],
            'linhas2': [[v.get('produto_final_nome') or '-', 'Lucro estimado', f"R$ {float(v.get('lucro_estimado') or 0):.2f}"] for v in vendas[:40]],
        }
    if modulo == 'importar-pedidos':
        canais = {}
        for v in vendas:
            canais[v.get('origem') or 'ERP'] = canais.get(v.get('origem') or 'ERP', 0) + 1
        return {
            'titulo': 'Importar pedidos',
            'resumo': {'kpi1': total_vendas, 'kpi2': pendentes, 'kpi3': 0, 'kpi4': len(canais)},
            'linhas1': [[canal, 'Pedidos importados', qtd] for canal, qtd in sorted(canais.items())],
            'linhas2': [[f"Venda #{v.get('id')}", v.get('origem') or 'ERP', v.get('created_at') or '-'] for v in vendas[:40]],
        }
    if modulo == 'propostas-comerciais':
        return {
            'titulo': 'Propostas comerciais',
            'resumo': {'kpi1': len(clientes), 'kpi2': len([c for c in clientes if c.get('ativo') in (1, '1', True)]), 'kpi3': total_vendas, 'kpi4': round(faturamento,2)},
            'linhas1': [[c.get('nome') or '-', c.get('telefone') or c.get('email') or '-', c.get('cidade') or '-'] for c in clientes[:40]],
            'linhas2': [[p.get('nome') or '-', 'Preço sugerido', f"R$ {float(p.get('preco_sugerido') or p.get('preco_praticado') or 0):.2f}"] for p in produtos[:40]],
        }
    if modulo in ('objetos-postagem','melhor-envio'):
        return {
            'titulo': 'Logística de envio',
            'resumo': {'kpi1': total_vendas, 'kpi2': pendentes, 'kpi3': recebidas, 'kpi4': 0},
            'linhas1': [[f"Venda #{v.get('id')}", v.get('cliente_nome') or '-', v.get('produto_final_nome') or '-'] for v in vendas[:40]],
            'linhas2': [[v.get('lote') or '-', 'Separar / postar', v.get('created_at') or '-'] for v in vendas[:40]],
        }
    if modulo == 'logistica-reversa':
        devolucoes = [v for v in vendas if 'devol' in str(v.get('observacao') or '').lower()]
        return {
            'titulo': 'Logística reversa',
            'resumo': {'kpi1': len(devolucoes), 'kpi2': len([v for v in devolucoes if str(v.get('status_financeiro') or '').lower() not in ('concluido','cancelado')]), 'kpi3': len(devolucoes), 'kpi4': round(sum(float(v.get('valor_total') or 0) for v in devolucoes),2)},
            'linhas1': [[f"Venda #{v.get('id')}", v.get('cliente_nome') or '-', v.get('observacao') or 'Devolução'] for v in devolucoes[:40]],
            'linhas2': [[v.get('produto_final_nome') or '-', v.get('lote') or '-', f"R$ {float(v.get('valor_total') or 0):.2f}"] for v in devolucoes[:40]],
        }
    if modulo == 'checkout-pedidos':
        return {
            'titulo': 'Checkout de pedidos',
            'resumo': {'kpi1': total_vendas, 'kpi2': pendentes, 'kpi3': recebidas, 'kpi4': 0},
            'linhas1': [[f"Pedido #{v.get('id')}", v.get('produto_final_nome') or '-', f"Qtd {v.get('quantidade') or 0}"] for v in vendas[:40]],
            'linhas2': [[v.get('lote') or '-', 'Conferir separação', v.get('status_financeiro') or '-'] for v in vendas[:40]],
        }
    if modulo == 'impressao-etiquetas-auto':
        return {
            'titulo': 'Impressão de etiquetas',
            'resumo': {'kpi1': total_vendas, 'kpi2': total_vendas, 'kpi3': 0, 'kpi4': 0},
            'linhas1': [[v.get('produto_final_nome') or '-', v.get('lote') or '-', f"Qtd {v.get('quantidade') or 0}"] for v in vendas[:40]],
            'linhas2': [[f"Etiqueta venda #{v.get('id')}", 'Produto / lote / validade', v.get('created_at') or '-'] for v in vendas[:40]],
        }
    return {
        'titulo': 'Vendas',
        'resumo': {'kpi1': total_vendas, 'kpi2': round(faturamento,2), 'kpi3': round(lucro,2), 'kpi4': round(cmv,2)},
        'linhas1': [[f"Venda #{v.get('id')}", v.get('produto_final_nome') or '-', f"R$ {float(v.get('valor_total') or 0):.2f}"] for v in vendas[:40]],
        'linhas2': [[v.get('cliente_nome') or '-', v.get('status_financeiro') or '-', v.get('created_at') or '-'] for v in vendas[:40]],
    }


@app.route('/api/vendas_submodulo/<modulo>')
def api_vendas_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        payload = _vendas_submodulo_payload(cursor, modulo)
        payload['status'] = 'sucesso'
        payload['modulo'] = modulo
        return jsonify(payload)
    finally:
        conn.close()


def _compras_submodulo_payload(cursor, modulo):
    """Dados reais e separados para cada submenu de compras."""
    modulo = (modulo or '').strip().lower()

    def tabela(nome):
        try:
            return _erp_tabela_existe(cursor, nome)
        except Exception:
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,))
                return cursor.fetchone() is not None
            except Exception:
                return False

    def rows(sql, params=()):
        try:
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
        except Exception:
            return []

    def one(sql, params=()):
        try:
            cursor.execute(sql, params)
            r = cursor.fetchone()
            return dict(r) if r else {}
        except Exception:
            return {}

    def money(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    solicitacoes = rows("SELECT * FROM compras_solicitacoes ORDER BY id DESC LIMIT 200") if tabela('compras_solicitacoes') else []
    cotacoes = rows("SELECT * FROM compras_cotacoes ORDER BY id DESC LIMIT 200") if tabela('compras_cotacoes') else []
    pedidos = rows("SELECT * FROM compras_pedidos ORDER BY id DESC LIMIT 200") if tabela('compras_pedidos') else []
    fornecedores = rows("SELECT * FROM fornecedores ORDER BY id DESC LIMIT 200") if tabela('fornecedores') else []
    ingredientes = rows("SELECT * FROM ingredientes ORDER BY nome LIMIT 500") if tabela('ingredientes') else []

    abertos = [p for p in pedidos if str(p.get('status') or '').lower() not in ('recebido','cancelado','concluído','concluida','concluída')]
    recebidos = [p for p in pedidos if str(p.get('status') or '').lower() in ('recebido','concluído','concluida','concluída')]
    valor_pedidos = sum(money(p.get('valor_total')) for p in pedidos)
    valor_aberto = sum(money(p.get('valor_total')) for p in abertos)

    def linha_pedido(p):
        return [p.get('ingrediente_nome') or p.get('descricao') or f"Pedido #{p.get('id')}", p.get('fornecedor_nome') or p.get('fornecedor') or '-', f"R$ {money(p.get('valor_total')):.2f}"]

    def linha_solicitacao(x):
        return [x.get('ingrediente_nome') or x.get('descricao') or f"Solicitação #{x.get('id')}", x.get('status') or '-', f"Qtd {x.get('quantidade_solicitada') or x.get('quantidade') or 0}"]

    def linha_cotacao(x):
        return [x.get('fornecedor_nome') or x.get('fornecedor') or '-', x.get('ingrediente_nome') or f"Cotação #{x.get('id')}", f"R$ {money(x.get('preco_total')):.2f}"]

    def linha_fornecedor(f):
        return [f.get('nome') or f.get('razao_social') or f"Fornecedor #{f.get('id')}", f.get('cidade') or f.get('telefone') or '-', f.get('email') or f.get('documento') or '-']

    def sugestoes_minimo():
        lista = []
        for ing in ingredientes:
            atual = money(ing.get('estoque_atual'))
            minimo = money(ing.get('estoque_minimo'))
            if minimo and atual <= minimo:
                falta = max(minimo - atual, 0)
                lista.append([ing.get('nome') or '-', f"Atual {atual:g} / mínimo {minimo:g}", f"Comprar {falta:g} {ing.get('unidade') or ''}".strip()])
        return lista[:50]

    if modulo in ('compras','compras-dashboard'):
        return {
            'titulo':'Compras',
            'resumo':{'kpi1':len(pedidos),'kpi2':len(abertos),'kpi3':len(recebidos),'kpi4':round(valor_pedidos,2)},
            'linhas1':[linha_pedido(p) for p in pedidos[:30]],
            'linhas2':sugestoes_minimo() or [linha_solicitacao(s) for s in solicitacoes[:30]],
        }
    if modulo in ('solicitacoes-compra','sugestao-compras'):
        sugest = sugestoes_minimo()
        return {
            'titulo':'Sugestões e solicitações de compra',
            'resumo':{'kpi1':len(sugest),'kpi2':len(solicitacoes),'kpi3':len([s for s in solicitacoes if str(s.get('status') or '').lower() in ('aberta','pendente')]),'kpi4':len(pedidos)},
            'linhas1':sugest or [['Sem item abaixo do mínimo','Estoque OK','-']],
            'linhas2':[linha_solicitacao(s) for s in solicitacoes[:40]],
        }
    if modulo == 'cotacoes-compra':
        selecionadas = len([c for c in cotacoes if str(c.get('selecionada') or '0') in ('1','true','sim')])
        return {
            'titulo':'Cotações de compra',
            'resumo':{'kpi1':len(cotacoes),'kpi2':selecionadas,'kpi3':len(solicitacoes),'kpi4':round(sum(money(c.get('preco_total')) for c in cotacoes),2)},
            'linhas1':[linha_cotacao(c) for c in cotacoes[:40]],
            'linhas2':[linha_solicitacao(s) for s in solicitacoes[:40]],
        }
    if modulo == 'pedidos-compra':
        return {
            'titulo':'Pedidos de compra',
            'resumo':{'kpi1':len(pedidos),'kpi2':len(abertos),'kpi3':len(recebidos),'kpi4':round(valor_aberto,2)},
            'linhas1':[linha_pedido(p) for p in pedidos[:50]],
            'linhas2':[[p.get('status') or '-', p.get('data_pedido') or p.get('created_at') or '-', p.get('data_recebimento') or '-'] for p in pedidos[:50]],
        }
    if modulo in ('notas-fiscais-entrada','checkin-recebimentos'):
        return {
            'titulo':'Recebimentos e notas de entrada',
            'resumo':{'kpi1':len(recebidos),'kpi2':len(abertos),'kpi3':round(sum(money(p.get('valor_total')) for p in recebidos),2),'kpi4':len(pedidos)},
            'linhas1':[linha_pedido(p) for p in recebidos[:40]],
            'linhas2':[linha_pedido(p) for p in abertos[:40]],
        }
    if modulo == 'fornecedores-compras':
        return {
            'titulo':'Fornecedores em compras',
            'resumo':{'kpi1':len(fornecedores),'kpi2':len(pedidos),'kpi3':round(valor_pedidos,2),'kpi4':len(cotacoes)},
            'linhas1':[linha_fornecedor(f) for f in fornecedores[:50]],
            'linhas2':[linha_pedido(p) for p in pedidos[:50]],
        }
    return {
        'titulo':'Compras',
        'resumo':{'kpi1':len(pedidos),'kpi2':len(solicitacoes),'kpi3':len(cotacoes),'kpi4':round(valor_pedidos,2)},
        'linhas1':[linha_pedido(p) for p in pedidos[:30]],
        'linhas2':[linha_solicitacao(s) for s in solicitacoes[:30]],
    }


@app.route('/api/compras_submodulo/<modulo>')
def api_compras_submodulo(modulo):
    criar_tabelas()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        try:
            erp_sincronizar_compras_estoque_financeiro(cursor)
            conn.commit()
        except Exception:
            pass
        payload = _compras_submodulo_payload(cursor, modulo)
        payload['status'] = 'sucesso'
        payload['modulo'] = modulo
        return jsonify(payload)
    finally:
        conn.close()




# ============================================================
# Motor central do ERP
# ============================================================
@app.route('/api/erp/motor/sincronizar', methods=['POST', 'GET'])
def api_erp_motor_sincronizar():
    """Executa a sincronização central sem apagar dados.

    Atualiza saldos de produtos acabados por lote, recalcula CMV dos produtos
    finais e registra alertas de estoque mínimo. É seguro para rodar várias
    vezes e serve como base incremental para ligar os módulos do ERP.
    """
    try:
        resultado = executar_motor_central(BANCO)
        codigo = 200 if resultado.get('status') == 'OK' else 500
        return jsonify(resultado), codigo
    except Exception as e:
        return jsonify({'status': 'ERRO', 'resumo': str(e), 'detalhes': {}}), 500

# ============================================================
# Auditoria técnica do código (estabilização)
# ============================================================
def auditoria_tecnica_codigo():
    """Retorna um diagnóstico técnico leve sem alterar dados do banco."""
    import ast
    from collections import Counter

    resultado = {
        'status': 'sucesso',
        'arquivo_principal': 'app.py',
        'rotas_total': 0,
        'funcoes_total': 0,
        'funcoes_duplicadas': [],
        'rotas_duplicadas': [],
        'observacoes': []
    }

    try:
        caminho = os.path.abspath(__file__)
        with open(caminho, 'r', encoding='utf-8') as f:
            codigo = f.read()
        arvore = ast.parse(codigo)
        funcoes = []
        rotas = []
        for no in ast.walk(arvore):
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcoes.append(no.name)
                for dec in no.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == 'route':
                        rota = None
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            rota = dec.args[0].value
                        metodos = []
                        for kw in dec.keywords:
                            if kw.arg == 'methods':
                                try:
                                    metodos = ast.literal_eval(kw.value)
                                except Exception:
                                    metodos = []
                        rotas.append({'rota': rota, 'funcao': no.name, 'metodos': metodos or ['GET']})

        resultado['funcoes_total'] = len(funcoes)
        resultado['rotas_total'] = len(rotas)
        cont_funcoes = Counter(funcoes)
        resultado['funcoes_duplicadas'] = [nome for nome, qtd in cont_funcoes.items() if qtd > 1]

        chave_rotas = Counter((r['rota'], tuple(sorted(r['metodos']))) for r in rotas)
        resultado['rotas_duplicadas'] = [
            {'rota': rota, 'metodos': list(metodos), 'quantidade': qtd}
            for (rota, metodos), qtd in chave_rotas.items()
            if qtd > 1
        ]

        if resultado['funcoes_duplicadas']:
            resultado['observacoes'].append('Existem funções auxiliares duplicadas. Não impedem o sistema de iniciar, mas devem ser consolidadas na refatoração.')
        if resultado['rotas_duplicadas']:
            resultado['observacoes'].append('Existem rotas com mesmo caminho e mesmo método. Revisar antes de novas funcionalidades.')
        if not resultado['rotas_duplicadas']:
            resultado['observacoes'].append('Não foram encontradas rotas duplicadas com o mesmo método HTTP.')
    except Exception as e:
        resultado['status'] = 'erro'
        resultado['mensagem'] = str(e)
    return resultado


@app.route('/api/auditoria_tecnica_codigo')
def api_auditoria_tecnica_codigo():
    return jsonify(auditoria_tecnica_codigo())


if __name__ == "__main__":
    app.run(debug=True)
