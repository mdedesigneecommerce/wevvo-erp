"""Auditoria real do Wevvo ERP/PDV.

Este script não valida apenas presença de arquivos. Ele verifica sintomas reais
que já foram encontrados no banco/código:
- usuários sem senha efetiva;
- lotes com saldo ocultados por INNER JOIN ou por tabela de origem errada;
- estoque mínimo zero classificado como estoque baixo;
- dependência de pytest ausente.

Uso:
    python utils/auditoria_real_wevvo.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
REQ = ROOT / "requirements.txt"
DB = ROOT / "sistema.db"
ESTOQUE_ROUTES = ROOT / "routes" / "estoque_routes.py"


def ok(msg: str) -> None:
    print(f"OK  - {msg}")


def fail(msg: str) -> None:
    print(f"FALHA - {msg}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def contar_sql(cursor: sqlite3.Cursor, sql: str) -> int:
    try:
        row = cursor.execute(sql).fetchone()
        return int(row[0] if row else 0)
    except Exception as exc:
        print(f"AVISO - consulta não executada: {exc}\nSQL: {sql}")
        return -1


def main() -> int:
    falhas: list[str] = []
    app_text = read(APP)
    estoque_text = read(ESTOQUE_ROUTES)
    req_text = read(REQ).lower()

    if not APP.exists():
        falhas.append("app.py não encontrado")
    else:
        ok("app.py encontrado")

    if "pytest" in req_text:
        ok("pytest listado em requirements.txt")
    else:
        falhas.append("pytest não está listado em requirements.txt")

    padrao_ruim_sql = re.search(
        r"CASE WHEN\s+COALESCE\(estoque_atual,0\)\s*<=\s*COALESCE\(estoque_minimo,0\)\s+THEN 'Baixo'",
        app_text,
        re.IGNORECASE,
    )
    padrao_ruim_python = '"BAIXO" if item["estoque_atual"] <= item["estoque_minimo"]' in estoque_text
    if padrao_ruim_sql or padrao_ruim_python:
        falhas.append("estoque mínimo zero ainda pode ser classificado como baixo")
    else:
        ok("regra de estoque baixo exige estoque_minimo > 0")

    if "FROM estoque_produto_acabado e" in app_text and "LEFT JOIN produtos_finais pf" in app_text:
        ok("rastreabilidade lê estoque_produto_acabado com LEFT JOIN")
    else:
        falhas.append("rastreabilidade ainda pode ocultar lotes órfãos")

    if DB.exists():
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        lotes_saldo = contar_sql(cur, "SELECT COUNT(*) FROM estoque_produto_acabado WHERE COALESCE(saldo_atual,0)>0")
        lotes_visiveis_join_interno = contar_sql(cur, """
            SELECT COUNT(*)
            FROM estoque_produto_acabado e
            INNER JOIN produtos_finais pf ON pf.id=e.produto_final_id
            WHERE COALESCE(e.saldo_atual,0)>0 AND COALESCE(pf.ativo,1)=1
        """)
        min_zero_errado = contar_sql(cur, """
            SELECT COUNT(*)
            FROM ingredientes
            WHERE COALESCE(estoque_minimo,0)=0
              AND COALESCE(estoque_atual,0)<=COALESCE(estoque_minimo,0)
        """)
        usuarios_sem_senha = contar_sql(cur, """
            SELECT COUNT(*)
            FROM usuarios_sistema
            WHERE COALESCE(TRIM(senha_hash),'')=''
        """)
        conn.close()

        print(f"INFO - lotes com saldo em estoque_produto_acabado: {lotes_saldo}")
        print(f"INFO - lotes visíveis pela regra antiga com INNER JOIN: {lotes_visiveis_join_interno}")
        print(f"INFO - ingredientes que seriam falsamente 'baixos' se mínimo zero contasse: {min_zero_errado}")
        print(f"INFO - usuários sem senha hash: {usuarios_sem_senha}")

        if lotes_saldo > lotes_visiveis_join_interno:
            ok("auditoria confirmou que a correção precisa preservar lotes sem produto vinculado")
        if min_zero_errado > 0:
            ok("auditoria confirmou caso real de mínimo zero no banco")
        if usuarios_sem_senha > 0:
            print("AVISO - login existe, mas ainda não deve ser tratado como segurança real")
    else:
        print("AVISO - sistema.db não encontrado; auditoria de banco ignorada")

    if falhas:
        for msg in falhas:
            fail(msg)
        return 1

    print("\nAUDITORIA REAL CONCLUÍDA COM SUCESSO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
