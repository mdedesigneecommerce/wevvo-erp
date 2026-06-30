# Sprint 13 — Estoque: consultas em Blueprint

## Objetivo

Migrar endpoints de leitura do módulo de Estoque para `routes/estoque_routes.py`, preservando as URLs existentes e mantendo as regras de movimentação no `app.py` até a próxima etapa.

## Arquivos alterados

- `app.py`
- `routes/estoque_routes.py`
- `docs/SPRINT_13_ESTOQUE_CONSULTA_BLUEPRINT.md`
- `utils/validar_sprint_13.py`

## Rotas preservadas

- `GET /estoque_ingredientes`
- `GET /movimentacoes_estoque`
- `GET /estoque_produto_acabado`
- `GET /movimentacoes_produto_acabado`

## Como testar

1. Executar `python utils/validar_sprint_13.py`.
2. Executar `python app.py`.
3. Abrir o sistema no navegador.
4. Conferir o módulo de Estoque.
5. Conferir listagem de ingredientes, movimentações e produto acabado.
6. Confirmar que entradas, saídas, produção e vendas continuam funcionando pelas rotas antigas ainda mantidas no `app.py`.

## Mensagem de commit sugerida

```text
refactor: migrar consultas de estoque para blueprint
```
