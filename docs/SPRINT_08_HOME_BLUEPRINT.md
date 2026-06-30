# Sprint 08 — Rotas principais em Blueprint

## Objetivo

Migrar as rotas principais da aplicação para um Blueprint dedicado, iniciando a separação das telas base do `app.py`.

## Arquivos alterados

- `app.py`
- `routes/home_routes.py`
- `docs/SPRINT_08_HOME_BLUEPRINT.md`
- `utils/validar_sprint_08.py`

## Alterações realizadas

- Criado o Blueprint `home_bp`.
- Migrada a rota `/` para `routes/home_routes.py`.
- Migrada a rota `/receitas_salvas` para `routes/home_routes.py`.
- Mantidos os endpoints, retornos JSON e compatibilidade com o frontend atual.
- Mantida a atualização automática dos custos das receitas antes da listagem.

## Como testar

1. Executar `python utils/validar_sprint_08.py`.
2. Executar `python app.py`.
3. Abrir a tela inicial do sistema.
4. Conferir se as receitas aparecem normalmente.
5. Validar se combos e receitas continuam carregando no frontend.

## Commit sugerido

```text
refactor: migrar rotas principais para blueprint
```
