# Auditoria Real e Estabilização 01 — Wevvo ERP/PDV

## Objetivo

Corrigir problemas encontrados por teste de comportamento real, não apenas por presença de arquivos.

## Problemas confirmados

1. **Login ainda não é segurança real**
   - Existe usuário administrador sem `senha_hash` no banco atual.
   - O login deve ser tratado como módulo em desenvolvimento, não como proteção de rede.

2. **Estoque mínimo zero era contado como baixo em alguns endpoints**
   - A regra antiga usava `estoque_atual <= estoque_minimo`.
   - Com `estoque_minimo = 0`, ingredientes zerados eram marcados como `Baixo`.
   - A regra correta exige `estoque_minimo > 0`.

3. **Rastreabilidade escondia lotes sem produto final vinculado**
   - O banco possui lotes em `estoque_produto_acabado`.
   - Consultas com `INNER JOIN produtos_finais` ocultavam lotes órfãos.
   - A consulta agora usa `LEFT JOIN` e mostra o status `Órfão` quando aplicável.

4. **`pytest` não estava listado nas dependências**
   - Incluído `pytest>=8.0.0` no `requirements.txt`.

## Arquivos alterados

- `app.py`
- `routes/estoque_routes.py`
- `requirements.txt`
- `utils/auditoria_real_wevvo.py`
- `tests/test_auditoria_estoque_regras.py`
- `docs/AUDITORIA_REAL_ESTABILIZACAO_01.md`

## Como testar

```powershell
python utils/auditoria_real_wevvo.py
python -m py_compile app.py routes/estoque_routes.py utils/auditoria_real_wevvo.py
python app.py
```

Depois, no navegador, testar:

- `http://127.0.0.1:5000/api/estoque_submodulo/lotes-rastreabilidade`
- `http://127.0.0.1:5000/estoque_ingredientes`
- `http://127.0.0.1:5000/estoque_produto_acabado`

## Critério de aceite

- Ingredientes com `estoque_minimo = 0` não aparecem como estoque baixo.
- Lotes com saldo aparecem mesmo quando o produto final vinculado não existe mais.
- O script `auditoria_real_wevvo.py` finaliza com sucesso.
- O ERP inicia sem erro.

## Commit sugerido

```text
fix: corrigir auditoria real de estoque e rastreabilidade
```
