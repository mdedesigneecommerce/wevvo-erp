# Sprint 06 — Configurações em Blueprint

## Objetivo

Migrar as rotas de perfil da empresa para um Blueprint próprio, reduzindo o `app.py` sem alterar contratos usados pelo frontend.

## Arquivos alterados

- `app.py`
- `routes/configuracoes_routes.py`
- `docs/SPRINT_06_CONFIGURACOES_BLUEPRINT.md`
- `utils/validar_sprint_06.py`

## Rotas migradas

- `GET /api/perfil_empresa`
- `POST /api/perfil_empresa`

## Como testar

1. Executar `python utils/validar_sprint_06.py`.
2. Executar `python app.py`.
3. Abrir o sistema no navegador.
4. Acessar a área de configurações/perfil da empresa.
5. Confirmar que o carregamento e salvamento do perfil continuam funcionando.

## Mensagem de commit sugerida

```bash
git commit -m "refactor: migrar perfil da empresa para blueprint"
```
