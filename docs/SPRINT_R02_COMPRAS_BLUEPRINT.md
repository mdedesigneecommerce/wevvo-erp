# Sprint R02 — Migração real do módulo Compras para Blueprint

## Objetivo

Migrar as rotas operacionais do módulo Compras do `app.py` para `routes/compras_routes.py`, preservando as regras existentes e sem alterar a interface ou os contratos das APIs.

## Arquivos alterados

- `app.py`
- `routes/compras_routes.py`
- `docs/SPRINT_R02_COMPRAS_BLUEPRINT.md`
- `utils/validar_sprint_R02.py`

## Rotas migradas

- `/compras_status`
- `/compras_criar_solicitacao`
- `/compras_registrar_cotacao`
- `/compras_gerar_pedido`
- `/compras_receber_pedido`
- `/compras_sincronizar_erp`

## Estratégia técnica

Foi adotado registro tardio do Blueprint de Compras no final do `app.py`. Isso permite injetar funções auxiliares ainda existentes no monólito, evitando import circular e preservando compatibilidade durante a refatoração gradual.

## Como testar

1. Executar `python utils/validar_sprint_R02.py`.
2. Executar `python app.py`.
3. Abrir `http://127.0.0.1:5000/`.
4. Testar no sistema as telas que dependem de compras.
5. Conferir no terminal se não houve erro de rota duplicada ou importação.

## Mensagem de commit sugerida

```bash
git add .
git commit -m "refactor: migrar compras para blueprint"
```
