# Etapa 9 – Compras Profissionais

Implementação incremental preservando os módulos existentes.

## Incluído

- Módulo visual **Compras** no menu lateral.
- Lista automática de compras baseada em ingredientes abaixo do estoque mínimo.
- Criação de solicitação de compra manual ou automática.
- Registro de cotação por fornecedor.
- Geração de pedido de compra.
- Recebimento do pedido com entrada automática no estoque.
- Atualização de custo do ingrediente quando o pedido possui valor.
- Lançamento financeiro automático ao gerar pedido de compra.
- Baixa/atualização do lançamento financeiro quando o pedido é recebido.

## Vínculos preservados

- Ingredientes
- Estoque
- Movimentações de estoque
- Financeiro
- Dashboard/indicadores existentes
- Motor de automação e integrações já criados

## Arquivos alterados

- app.py
- templates/index.html

## Novas rotas internas

- GET /compras_status
- POST /compras_criar_solicitacao
- POST /compras_registrar_cotacao
- POST /compras_gerar_pedido
- POST /compras_receber_pedido
