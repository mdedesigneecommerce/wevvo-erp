# Sprint R01 — Migração real das APIs do Dashboard para Blueprint

## Objetivo

Reduzir o `app.py` de forma segura, migrando as APIs consolidadas do Dashboard para o Blueprint `routes/dashboard_routes.py`, sem alterar os caminhos HTTP usados pelo frontend.

## Arquivos alterados

- `app.py`
- `routes/dashboard_routes.py`
- `docs/SPRINT_R01_DASHBOARD_API_BLUEPRINT.md`
- `utils/validar_sprint_R01.py`

## O que foi feito

- Removidas do `app.py` as rotas:
  - `/api/dashboard_executivo_integrado`
  - `/api/dashboard_inteligente_erp`
- Recriadas as mesmas rotas dentro de `dashboard_bp`.
- Mantido o mesmo contrato JSON baseado em `gerar_dashboard_executivo(BANCO)`.
- Removido import desnecessário de `gerar_dashboard_executivo` do `app.py`.
- Criado validador automático para garantir que as rotas foram migradas sem duplicidade.

## Como testar

```powershell
python utils/validar_sprint_R01.py
python app.py
```

Depois, no navegador ou frontend, validar:

- `/api/dashboard_executivo_integrado`
- `/api/dashboard_inteligente_erp`
- telas de Dashboard existentes

## Mensagem de commit sugerida

```bash
git add .
git commit -m "refactor: migrar APIs do dashboard para blueprint"
```
