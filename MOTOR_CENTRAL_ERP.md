# Motor central do ERP — etapa inicial

Esta etapa criou um serviço separado em `services/erp_motor.py` para começar a retirar regras de negócio do `app.py` sem quebrar o sistema atual.

## O que foi implementado

- Tabela `erp_motor_execucoes` para registrar cada execução do motor.
- Tabela `erp_motor_alertas` para alertas automáticos, inicialmente de estoque mínimo.
- Endpoint `/api/erp/motor/sincronizar`.
- Recalculo central dos produtos finais em `cmv_precificacao`.
- Atualização do estoque atual do produto acabado pela soma dos lotes em `estoque_produto_acabado`.
- Registro de alertas de estoque mínimo para produtos acabados.

## O que não foi feito nesta etapa

- Não foram removidas funções existentes.
- Não foram apagadas tabelas.
- Não foi feita refatoração completa do `app.py`.
- Não foram alteradas regras fiscais ou integrações externas.

## Próxima etapa recomendada

Ligar este motor aos eventos reais:

1. recebimento de compra;
2. conclusão de produção;
3. venda/faturamento;
4. baixa financeira.

Assim o ERP passa a recalcular automaticamente sem depender de ação manual.
