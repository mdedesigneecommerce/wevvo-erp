# Funções utilitárias compartilhadas do Wevvo ERP/PDV.
# Extraídas do app.py na Etapa 7.0 para reduzir acoplamento e preparar a arquitetura modular.

import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo


def agora_brasilia():
    return datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M:%S")


def data_brasilia_obj():
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def data_brasil(data):
    return data.strftime("%d/%m/%Y")


def normalizar_busca(texto):
    texto = str(texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in texto)


def normalizar_float(valor, padrao=0.0):
    """Aceita número vindo do HTML com ponto ou vírgula e evita zerar campos por erro de conversão."""
    if valor is None:
        return padrao

    if isinstance(valor, str):
        valor = valor.strip().replace(',', '.')
        if valor == '':
            return padrao

    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = cursor.fetchall()
    return any(c["name"] == coluna for c in colunas)
