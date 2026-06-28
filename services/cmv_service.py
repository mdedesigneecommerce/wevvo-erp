"""Serviço central de CMV e precificação do Saúde e Nutri ERP.

Esta etapa inicia a retirada de regras de negócio do app.py sem quebrar
compatibilidade. As funções aqui são puras: não acessam banco e podem ser
reutilizadas por telas, rotas, motor central e relatórios.
"""

from __future__ import annotations

from typing import Any, Dict


def normalizar_decimal(valor: Any, padrao: float = 0.0) -> float:
    """Converte valores em número decimal preservando ponto decimal.

    Aceita formatos comuns no projeto:
    - 3.00 -> 3.0
    - 3,00 -> 3.0
    - 1.234,56 -> 1234.56
    - R$ 1.234,56 -> 1234.56
    """
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


def parse_float_br(valor: Any, padrao: float = 0.0) -> float:
    """Alias de compatibilidade para telas/rotas antigas."""
    return normalizar_decimal(valor, padrao)


def calcular_precificacao_cmv(
    custo_base: Any,
    embalagem: Any = 0,
    fixo: Any = 0,
    variavel: Any = 0,
    margem: Any = 30,
    preco: Any = 0,
) -> Dict[str, float | str]:
    """Cálculo oficial de CMV, preço mínimo, preço sugerido e margem.

    Este é o ponto único de cálculo para evitar divergência entre a tela de CMV,
    o motor central, relatórios e futuras automações.
    """
    custo_base = normalizar_decimal(custo_base, 0)
    embalagem = normalizar_decimal(embalagem, 0)
    fixo = normalizar_decimal(fixo, 0)
    variavel = max(0.0, min(99.0, normalizar_decimal(variavel, 0)))
    margem = max(0.0, min(99.0, normalizar_decimal(margem, 30)))
    preco = normalizar_decimal(preco, 0)

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


# Nome curto usado pelo motor central.
calcular_cmv = calcular_precificacao_cmv
