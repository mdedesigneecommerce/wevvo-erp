# Motor Central ERP — Etapa 3

## Implementado nesta etapa

Arquivo alterado:
- `services/erp_motor.py`

## Integrações adicionadas

### 1. Compras → Financeiro
O motor central agora cria/atualiza automaticamente lançamentos em `financeiro_lancamentos` para pedidos da tabela `compras_pedidos`.

Regra aplicada:
- `compras_pedidos` com valor maior que zero geram lançamento `tipo = despesa`.
- `origem = Compra`.
- `origem_id = id do pedido`.
- Não duplica lançamentos se o motor for executado várias vezes.
- Atualiza `compras_pedidos.financeiro_id` quando a coluna existe.

### 2. Vendas → Financeiro
O motor central agora cria/atualiza automaticamente lançamentos em `financeiro_lancamentos` para registros da tabela `vendas_erp`.

Regra aplicada:
- `vendas_erp` com valor maior que zero geram lançamento `tipo = receita`.
- `origem = Venda`.
- `origem_id = id da venda`.
- Não duplica lançamentos se o motor for executado várias vezes.

### 3. Movimentações → Estoque de ingredientes
O motor central passa a recalcular `ingredientes.estoque_atual` com base em `movimentacoes_estoque`.

Regra aplicada:
- `entrada`, `compra`, `ajuste entrada` somam.
- `saida`, `saída`, `consumo`, `produção`, `producao`, `perda`, `ajuste saída` subtraem.

## Validação feita

- `app.py` compila sem erro.
- `services/erp_motor.py` compila sem erro.
- O motor central foi executado em uma cópia temporária do banco real e retornou status `OK`.

## Observação

Nenhuma tabela, rota ou função existente foi removida.
