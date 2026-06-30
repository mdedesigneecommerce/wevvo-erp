# Sprint 10 — Busca e Sugestão Nutricional em Blueprint

## Objetivo

Migrar rotas de busca e sugestão nutricional para um Blueprint dedicado, reduzindo o `app.py` sem alterar os contratos usados pelo frontend.

## Rotas migradas

- `GET /buscar`
- `GET /sugerir_nutriente_ingrediente`

## Arquivos alterados

- `app.py`
- `routes/busca_routes.py`
- `docs/SPRINT_10_BUSCA_BLUEPRINT.md`
- `utils/validar_sprint_10.py`

## Como testar

1. Executar `python utils/validar_sprint_10.py`.
2. Executar `python app.py`.
3. No sistema, testar busca de ingredientes na ficha técnica.
4. Testar sugestão nutricional ao cadastrar ou editar ingrediente.

## Mensagem de commit sugerida

```text
refactor: migrar busca nutricional para blueprint
```
