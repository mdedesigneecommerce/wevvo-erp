"""Rotas de Configurações do Wevvo ERP/PDV.

Sprint 06: migração do perfil da empresa para Blueprint.
Mantém os mesmos endpoints usados pela interface atual:
- GET /api/perfil_empresa
- POST /api/perfil_empresa
"""

import sqlite3

from flask import Blueprint, jsonify, request

from config import BANCO


configuracoes_bp = Blueprint("configuracoes", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


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


@configuracoes_bp.route("/api/perfil_empresa", methods=["GET"])
def api_obter_perfil_empresa():
    conn = conectar_banco()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM perfil_empresa WHERE id = 1")
    perfil = montar_perfil_empresa_dict(cursor.fetchone())
    conn.close()
    return jsonify({"ok": True, "perfil": perfil})


@configuracoes_bp.route("/api/perfil_empresa", methods=["POST"])
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
