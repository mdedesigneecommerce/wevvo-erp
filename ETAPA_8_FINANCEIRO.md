# Etapa 8 – Financeiro completo

Implementação incremental do módulo Financeiro, preservando os módulos já existentes.

## Incluído

- Novo módulo visual: Financeiro.
- Contas a pagar e contas a receber.
- Lançamentos manuais de receita e despesa.
- Resumo financeiro com receitas, despesas, resultado, margem líquida, contas a pagar, contas a receber e saldo previsto.
- Leitura por categoria e por status.
- Integração interna com o Motor de Automação ERP: vendas automáticas geram receita e CMV automaticamente no financeiro.

## Segurança da alteração

- Nenhum módulo existente foi removido.
- As rotas antigas foram preservadas.
- O financeiro usa tabela própria (`financeiro_lancamentos`) e sincroniza com `vendas_erp` sem duplicar lançamentos automáticos.
