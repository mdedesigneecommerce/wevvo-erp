# Sprint 11 — Ingredientes Consulta em Blueprint

## Objetivo

Migrar as primeiras rotas de consulta do módulo de Ingredientes para `routes/ingredientes_routes.py`, preservando os mesmos endpoints usados pelo frontend.

## Escopo

Rotas migradas para Blueprint:

- `/ingredientes_precos`
- `/ingredientes_admin`
- `/ingrediente_admin/<int:ingrediente_id>`

A Sprint foi limitada a rotas de leitura para reduzir risco. Rotas de gravação, reajuste, exclusão, recalculo de receitas, estoque e CMV continuam no `app.py` até a próxima Sprint.

## Arquivos alterados

- `app.py`
- `routes/ingredientes_routes.py`
- `docs/SPRINT_11_INGREDIENTES_CONSULTA_BLUEPRINT.md`
- `utils/validar_sprint_11.py`

## Como testar

1. Executar `python utils/validar_sprint_11.py`.
2. Executar `python app.py`.
3. Abrir o sistema no navegador.
4. Testar listagem de ingredientes.
5. Testar busca de ingrediente.
6. Abrir detalhe de ingrediente na interface.
7. Conferir que receitas, estoque, CMV e produto final continuam funcionando.

## Mensagem de commit sugerida

```text
refactor: migrar consultas de ingredientes para blueprint
```
