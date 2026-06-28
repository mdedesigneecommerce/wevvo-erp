# Sprint 1 — Refatoração segura do CMV

## Objetivo
Iniciar a modularização real do projeto sem remover funcionalidades existentes.

## Alterações aplicadas

### Arquivo criado
- `services/cmv_service.py`

### Arquivos alterados
- `app.py`
- `services/erp_motor.py`

## O que foi centralizado
- Conversão decimal brasileira (`3,00`, `3.00`, `1.234,56`, `R$ 1.234,56`).
- Cálculo oficial de CMV.
- Preço mínimo.
- Preço sugerido.
- Lucro real.
- Margem real.
- Markup.
- Status de precificação.

## Compatibilidade preservada
As funções antigas continuam existindo em `app.py` e em `services/erp_motor.py`, mas agora apenas encaminham para o serviço central.

Isso reduz risco de quebrar rotas/telas existentes e permite refatoração gradual.

## Validação executada
- `python -m py_compile app.py services/*.py`
- `python validar_projeto.py`

Resultado: sem erro crítico de compilação.

## Observações da auditoria atual
O validador ainda aponta funções internas duplicadas no `app.py`:

- `valor_unico`
- `lista`
- `cfg_precificacao`
- `contar`
- `tabela_existe`

Elas não foram removidas nesta sprint para evitar quebra. A próxima sprint deve tratar essas duplicidades com segurança.
