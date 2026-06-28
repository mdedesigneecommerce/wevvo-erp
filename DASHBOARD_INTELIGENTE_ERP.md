# Dashboard inteligente do ERP

## Alteração desta etapa

Foi criado o serviço `services/dashboard_service.py` para centralizar os indicadores executivos do Wevvo ERP/PDV usando dados reais do banco.

## Endpoints

- `/api/dashboard_executivo_integrado`
- `/api/dashboard_inteligente_erp`

Ambos passam a retornar os indicadores consolidados pelo serviço central.

## Indicadores consolidados

- Financeiro: contas a receber abertas, contas a pagar abertas, vencidos e saldo previsto.
- Vendas: faturamento do mês, faturamento total, lucro estimado e pedidos do mês.
- Compras: compras pendentes, valor pendente e valor recebido no mês.
- Estoque: ingredientes cadastrados, itens críticos, produtos cadastrados e saldo de produto acabado.
- Produção: produções do mês, quantidade produzida e ordens abertas.
- Qualidade: não conformidades abertas e do mês.
- CMV: margem média, lucro estimado, itens com prejuízo e itens sem preço.
- Alertas: alertas abertos e críticos do motor central.

## Critério de segurança

A alteração é incremental: não remove tabelas, não apaga dados e não altera rotas existentes. As consultas são defensivas para evitar quebra em bancos com estruturas parcialmente antigas.
