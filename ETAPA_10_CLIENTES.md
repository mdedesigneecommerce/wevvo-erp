# Etapa 10 – CRM e Clientes

Implementado módulo de Clientes/CRM preservando os módulos existentes.

## Incluído

- Novo menu **Clientes**.
- Cadastro de clientes PF/PJ.
- CPF/CNPJ, contatos, WhatsApp, e-mail, endereço, condição de pagamento e limite de crédito.
- Indicadores de clientes cadastrados, faturamento por clientes, ticket médio e clientes sem compra há 60 dias.
- Ranking Top Clientes.
- Curva ABC por faturamento.
- Sinalização de cliente VIP.
- Registro de compra/venda vinculada ao cliente.
- Integração automática com financeiro para contas a receber/receita.
- Histórico de compras e interações.
- Geração de mensagem de WhatsApp pronta para atendimento.

## Arquivos alterados

- app.py
- templates/index.html

## Observação

A etapa é incremental. Não removeu rotas nem módulos existentes. As novas tabelas são criadas automaticamente ao iniciar o sistema.
