#!/usr/bin/env python3
"""Validação técnica local do Wevvo ERP/PDV.
Executa sem iniciar o servidor Flask e sem alterar o banco de dados.
"""
import ast
import pathlib
import py_compile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent
APP = ROOT / "app.py"
INDEX = ROOT / "templates" / "index.html"


def main():
    erros = []
    print("== Validação técnica Wevvo ERP/PDV ==")

    try:
        py_compile.compile(str(APP), doraise=True)
        print("OK  app.py compila sem erro de sintaxe")
    except Exception as exc:
        erros.append(f"app.py não compila: {exc}")
        print("ERRO app.py não compila")

    codigo = APP.read_text(encoding="utf-8")
    arvore = ast.parse(codigo)
    funcoes = []
    rotas = []
    for no in ast.walk(arvore):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcoes.append((no.name, no.lineno))
            for dec in no.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route":
                    rota = None
                    if dec.args and isinstance(dec.args[0], ast.Constant):
                        rota = dec.args[0].value
                    metodos = ["GET"]
                    for kw in dec.keywords:
                        if kw.arg == "methods":
                            try:
                                metodos = ast.literal_eval(kw.value)
                            except Exception:
                                metodos = ["?" ]
                    rotas.append((rota, tuple(sorted(metodos)), no.name, no.lineno))

    print(f"OK  funções encontradas: {len(funcoes)}")
    print(f"OK  rotas Flask encontradas: {len(rotas)}")

    dup_funcoes = {nome: [linha for n, linha in funcoes if n == nome]
                   for nome, qtd in Counter(n for n, _ in funcoes).items() if qtd > 1}
    if dup_funcoes:
        print("AVISO funções duplicadas:")
        for nome, linhas in dup_funcoes.items():
            print(f"  - {nome}: linhas {linhas}")
    else:
        print("OK  sem funções duplicadas")

    dup_rotas = {chave: [(f, l) for rota, met, f, l in rotas if (rota, met) == chave]
                 for chave, qtd in Counter((r, m) for r, m, _, _ in rotas).items() if qtd > 1}
    if dup_rotas:
        erros.append("há rotas duplicadas com mesmo caminho e método")
        print("ERRO rotas duplicadas com mesmo método:")
        for (rota, metodos), refs in dup_rotas.items():
            print(f"  - {rota} {list(metodos)}: {refs}")
    else:
        print("OK  sem rotas duplicadas com o mesmo método")

    if INDEX.exists():
        html = INDEX.read_text(encoding="utf-8")
        print(f"OK  index.html encontrado ({len(html.splitlines())} linhas)")
    else:
        erros.append("templates/index.html não encontrado")
        print("ERRO templates/index.html não encontrado")

    if erros:
        print("\nResultado: atenção necessária")
        for erro in erros:
            print(f"- {erro}")
        raise SystemExit(1)

    print("\nResultado: validação técnica concluída sem erros críticos")


if __name__ == "__main__":
    main()
