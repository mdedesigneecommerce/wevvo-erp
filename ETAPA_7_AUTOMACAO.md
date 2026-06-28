# Etapa 7 – Motor de Automação do ERP

Implementado de forma incremental e segura.

## O que foi adicionado

- Módulo visual: **Automação ERP**.
- Tabelas internas:
  - `automacoes_eventos`
  - `vendas_erp`
- Serviço novo:
  - `services/automacoes.py`
- Rotas novas:
  - `/automacoes_status`
  - `/automacoes_executar`

## Ações disponíveis

- Registrar venda automática:
  - baixa saldo do lote de produto acabado;
  - atualiza estoque do produto final;
  - cria movimentação em produto acabado;
  - registra venda em `vendas_erp`;
  - calcula CMV e lucro estimado quando houver custo de produção.

- Processar compras automáticas:
  - identifica ingredientes abaixo do mínimo;
  - usa `quantidade_compra` quando cadastrada;
  - gera entrada automática no estoque;
  - registra movimentação e evento de automação.

- Recalcular CMV das receitas:
  - reaproveita o recálculo já existente do sistema;
  - registra histórico da ação.

## Observação

Nenhum módulo existente foi removido. As mudanças foram feitas como camada nova de automação e rastreabilidade.
