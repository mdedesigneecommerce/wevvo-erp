# Sprint 2 — Serviço central de Estoque

## O que foi feito

- Criado `services/estoque_service.py`.
- O endpoint `/api/estoque_submodulo/<modulo>` agora usa o serviço central.
- A função antiga `_estoque_submodulo_payload` foi mantida no `app.py` por compatibilidade, sem remoção de funcionalidades.
- Os submenus de estoque passam a buscar dados por contexto:
  - lançamentos de estoque;
  - conferência de estoque;
  - depósitos;
  - lotes e rastreabilidade;
  - qualidade e validades.

## Validação

- `app.py` compilado sem erro.
- `services/estoque_service.py` compilado sem erro.
- Serviço testado diretamente usando o banco `sistema.db`.

## Observação

Esta sprint não altera estrutura de tabelas e não remove dados. É uma refatoração segura para começar a retirar regras do `app.py`.
