# Sprint 05 — Categorias em Blueprint

## Objetivo

Migrar o módulo de Categorias do `app.py` para `routes/categorias_routes.py`, mantendo os mesmos endpoints e contratos usados pelo frontend.

## Arquivos alterados

- `app.py`
- `routes/categorias_routes.py`
- `docs/SPRINT_05_CATEGORIAS_BLUEPRINT.md`
- `utils/validar_sprint_05.py`

## Rotas migradas

- `GET /categorias_admin`
- `GET /categorias_select`
- `POST /salvar_categoria`
- `DELETE /excluir_categoria/<categoria_id>`
- `POST /vincular_receita_categoria`
- `GET /imprimir_categoria/<categoria_id>`

## Decisão técnica

As funções auxiliares específicas de categorias foram mantidas no próprio módulo de rotas para evitar dependência circular com `app.py`. O objetivo é preservar o funcionamento atual e preparar a extração futura para `services/categorias_service.py`.

## Como testar

1. Executar `python utils/validar_sprint_05.py`.
2. Executar `python app.py`.
3. Abrir o sistema no navegador.
4. Testar a listagem de categorias.
5. Criar ou editar uma categoria.
6. Vincular uma receita a uma categoria.
7. Testar a impressão de categoria.
8. Confirmar que receitas, ingredientes e busca continuam funcionando.

## Mensagem de commit sugerida

```bash
git commit -m "refactor: migrar rotas de categorias para blueprint"
```
