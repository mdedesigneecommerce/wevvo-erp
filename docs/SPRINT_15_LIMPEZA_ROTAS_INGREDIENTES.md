# Sprint 15 — Limpeza de Rotas Migradas de Ingredientes

## Objetivo

Concluir a etapa segura de migração do módulo de Ingredientes para Blueprint, removendo do `app.py` os decoradores `@app.route` que já foram assumidos por `routes/ingredientes_routes.py`.

## Importante

Nesta Sprint as funções antigas permanecem no `app.py` como camada de compatibilidade temporária, pois algumas rotas do Blueprint ainda delegam a regra de negócio para as funções originais. O que foi removido foi apenas a exposição direta dessas rotas pelo `app.py`, evitando duplicidade no registro de URLs.

## Arquivos alterados

- `app.py`
- `docs/SPRINT_15_LIMPEZA_ROTAS_INGREDIENTES.md`
- `utils/validar_sprint_15.py`

## Rotas afetadas

As rotas abaixo ficam ativas via `ingredientes_bp`:

- `/ingredientes_precos`
- `/ingredientes_admin`
- `/ingrediente_admin/<int:ingrediente_id>`
- `/salvar_ingrediente_admin`
- `/excluir_ingrediente/<int:ingrediente_id>`
- `/atualizar_preco_ingrediente`
- `/reajustar_preco_ingrediente`
- `/cadastrar_ingrediente`

## Como testar

1. Executar `python utils/validar_sprint_15.py`.
2. Executar `python app.py`.
3. Abrir o sistema normalmente.
4. Testar cadastro/listagem de ingredientes.
5. Testar atualização de preço de ingrediente.
6. Testar cadastro rápido de ingrediente em receita.

## Mensagem de commit sugerida

```bash
git commit -m "refactor: remover duplicidade de rotas migradas de ingredientes"
```
