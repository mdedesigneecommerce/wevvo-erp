"""Rotas de busca e sugestão nutricional do Wevvo ERP/PDV.

Sprint 10: migração real das rotas de busca para Blueprint.
Mantém os mesmos caminhos HTTP usados pelo frontend:
- /buscar
- /sugerir_nutriente_ingrediente
"""

import re
import sqlite3
from difflib import SequenceMatcher

from flask import Blueprint, jsonify, request

from config import BANCO
from services.core import normalizar_busca


busca_bp = Blueprint("busca", __name__)


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


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


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
        return {"base": base, "valores": [kcal, carb, acucar, 0, prot, gord, sat, trans, fibra, sodio]}

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


@busca_bp.route("/buscar")
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


@busca_bp.route("/sugerir_nutriente_ingrediente")
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
