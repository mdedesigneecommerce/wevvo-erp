# Sprint 14 — Estoque Manutenção em Blueprint

## Objetivo

Migrar rotas de manutenção de estoque para `routes/estoque_routes.py`, reduzindo o `app.py` e mantendo os endpoints públicos existentes sem alteração.

## Rotas migradas

- `POST /movimentar_estoque`
- `POST /atualizar_estoque_minimo`
- `POST /movimentar_produto_final_estoque`
- `POST /atualizar_produto_final_estoque_minimo`

Também foram removidas do `app.py` as rotas de consulta de estoque que já estavam cobertas pelo blueprint, evitando duplicidade de responsabilidades.

## Arquivos alterados

- `app.py`
- `routes/estoque_routes.py`
- `docs/SPRINT_14_ESTOQUE_MANUTENCAO_BLUEPRINT.md`
- `utils/validar_sprint_14.py`

## Como testar

1. Executar `python utils/validar_sprint_14.py`.
2. Executar `python app.py`.
3. Abrir o sistema normalmente.
4. Testar estoque de ingredientes:
   - listar ingredientes;
   - registrar entrada;
   - registrar saída;
   - ajustar estoque mínimo.
5. Testar estoque de produto final:
   - listar lotes;
   - movimentar saldo do produto final;
   - ajustar estoque mínimo do produto final.

## Mensagem de commit sugerida

```text
refactor: migrar manutenção de estoque para blueprint
```
