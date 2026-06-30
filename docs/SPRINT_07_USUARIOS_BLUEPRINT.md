# Sprint 07 — Usuários e Login em Blueprint

## Objetivo

Migrar as rotas de usuários e login para `routes/usuarios_routes.py`, reduzindo o tamanho do `app.py` sem alterar os endpoints usados pela interface.

## Arquivos alterados

- `app.py`
- `routes/usuarios_routes.py`
- `docs/SPRINT_07_USUARIOS_BLUEPRINT.md`
- `utils/validar_sprint_07.py`

## Endpoints preservados

- `GET /api/usuarios_sistema`
- `POST /api/usuarios_sistema`
- `POST /api/usuarios_sistema/<usuario_id>/status`
- `POST /api/login_sistema`

## Como testar

1. Executar `python utils/validar_sprint_07.py`.
2. Executar `python app.py`.
3. Abrir o sistema normalmente.
4. Validar a área de configurações/usuários.
5. Testar listagem de usuários e login quando disponível na interface.

## Commit sugerido

```bash
git add .
git commit -m "refactor: migrar usuários e login para blueprint"
```
