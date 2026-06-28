# Etapa - Serviço Financeiro Central

Alteração incremental aplicada sobre a versão real do projeto.

## Criado
- `services/financeiro_service.py`

## Integrado
- Novo endpoint: `/api/financeiro_integrado`

## Objetivo
Centralizar a leitura financeira em um serviço próprio, preparando a migração dos submenus financeiros para uma fonte única, sem apagar rotas antigas e sem quebrar telas existentes.

## O que o serviço entrega
- saldo realizado
- saldo previsto
- contas a pagar em aberto
- contas a receber em aberto
- valores vencidos
- entradas realizadas
- saídas realizadas
- agrupamento por categoria
- agrupamento por forma de pagamento

## Validação
- `app.py` compilado sem erro
- `services/financeiro_service.py` compilado sem erro
- serviço executado sobre `sistema.db` com status `sucesso`
