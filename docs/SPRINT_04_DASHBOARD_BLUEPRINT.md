# Sprint 04 — Dashboard em Blueprint

## Objetivo

Migrar as rotas do Dashboard para `routes/dashboard_routes.py`, iniciando a redução real do `app.py` sem alterar contratos HTTP, nomes das rotas ou respostas JSON usadas pelo frontend.

## Arquivos alterados

- `app.py`
- `routes/dashboard_routes.py`
- `docs/SPRINT_04_DASHBOARD_BLUEPRINT.md`
- `utils/validar_sprint_04.py`

## Rotas migradas

- `/dashboard_gerencial`
- `/dashboard_gerencial_fase3`
- `/dashboard_financeiro`
- `/dashboard_inteligencia`

## Decisão técnica

O módulo Dashboard foi escolhido por ser uma área de leitura gerencial, com baixo risco de alterar dados. A migração mantém os mesmos endpoints e usa `Blueprint("dashboard", __name__)` sem prefixo, preservando compatibilidade com o `index.html` atual.

## Como testar

1. Executar `python utils/validar_sprint_04.py`.
2. Executar `python app.py`.
3. Abrir o sistema no navegador.
4. Conferir se a tela principal abre normalmente.
5. Acessar os painéis Dashboard, Financeiro e Inteligência.
6. Confirmar que não há erro 404 nas chamadas do Dashboard.

## Mensagem de commit sugerida

```text
refactor: migrar rotas de dashboard para blueprint
```
