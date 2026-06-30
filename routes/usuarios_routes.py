"""Rotas de usuários e login do Wevvo ERP/PDV.

Sprint 07: migração das rotas de usuários para Blueprint.
Mantém os mesmos endpoints usados pela interface atual:
- GET /api/usuarios_sistema
- POST /api/usuarios_sistema
- POST /api/usuarios_sistema/<usuario_id>/status
- POST /api/login_sistema
"""

import sqlite3

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from config import BANCO
from services.core import agora_brasilia, coluna_existe


usuarios_bp = Blueprint("usuarios", __name__)


def conectar_banco():
    conn = sqlite3.connect(BANCO)
    conn.row_factory = sqlite3.Row
    return conn


def garantir_tabelas_usuarios(cursor):
    """Garante a estrutura mínima de usuários sem depender do app.py.

    A criação completa do banco continua acontecendo na inicialização do ERP.
    Esta proteção mantém os endpoints robustos em execuções isoladas.
    """
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
    try:
        if not coluna_existe(cursor, 'usuarios_sistema', 'permissoes'):
            cursor.execute("ALTER TABLE usuarios_sistema ADD COLUMN permissoes TEXT DEFAULT 'todos'")
        if not coluna_existe(cursor, 'usuarios_sistema', 'observacao'):
            cursor.execute("ALTER TABLE usuarios_sistema ADD COLUMN observacao TEXT")
    except Exception:
        pass
    cursor.execute("""
        INSERT INTO usuarios_sistema (usuario, email, perfil, status, created_at, updated_at)
        SELECT 'administrador', '', 'Administrador', 'Ativo', ?, ?
        WHERE NOT EXISTS (SELECT 1 FROM usuarios_sistema WHERE usuario='administrador')
    """, (agora_brasilia(), agora_brasilia()))


def usuario_sistema_dict(row):
    if not row:
        return {}
    dados = dict(row)
    dados.pop('senha_hash', None)
    return dados


@usuarios_bp.route('/api/usuarios_sistema', methods=['GET'])
def api_usuarios_sistema_listar():
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        garantir_tabelas_usuarios(cursor)
        conn.commit()
        rows = cursor.execute("""
            SELECT id, usuario, email, perfil, status, permissoes, observacao, ultimo_acesso, created_at, updated_at
            FROM usuarios_sistema
            ORDER BY CASE WHEN usuario='administrador' THEN 0 ELSE 1 END, usuario COLLATE NOCASE
        """).fetchall()
        return jsonify({'status': 'sucesso', 'usuarios': [usuario_sistema_dict(r) for r in rows]})
    finally:
        conn.close()


@usuarios_bp.route('/api/usuarios_sistema', methods=['POST'])
def api_usuarios_sistema_salvar():
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

    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        garantir_tabelas_usuarios(cursor)
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


@usuarios_bp.route('/api/usuarios_sistema/<int:usuario_id>/status', methods=['POST'])
def api_usuarios_sistema_status(usuario_id):
    dados = request.get_json(silent=True) or request.form.to_dict() or {}
    novo_status = (dados.get('status') or '').strip() or 'Inativo'
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        garantir_tabelas_usuarios(cursor)
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


@usuarios_bp.route('/api/login_sistema', methods=['POST'])
def api_login_sistema():
    """Validação de login para uso futuro sem bloquear o ERP nesta etapa incremental."""
    dados = request.get_json(silent=True) or request.form.to_dict() or {}
    usuario = (dados.get('usuario') or '').strip()
    senha = (dados.get('senha') or '').strip()
    conn = conectar_banco()
    cursor = conn.cursor()
    try:
        garantir_tabelas_usuarios(cursor)
        row = cursor.execute('SELECT * FROM usuarios_sistema WHERE usuario=? AND status="Ativo"', (usuario,)).fetchone()
        if not row:
            return jsonify({'status': 'erro', 'mensagem': 'Usuário não encontrado ou inativo.'}), 401
        senha_hash = row['senha_hash'] if 'senha_hash' in row.keys() else None
        if senha_hash and not check_password_hash(senha_hash, senha):
            return jsonify({'status': 'erro', 'mensagem': 'Senha inválida.'}), 401
        cursor.execute('UPDATE usuarios_sistema SET ultimo_acesso=?, updated_at=? WHERE id=?', (agora_brasilia(), agora_brasilia(), row['id']))
        conn.commit()
        return jsonify({'status': 'sucesso', 'usuario': usuario_sistema_dict(row)})
    finally:
        conn.close()
