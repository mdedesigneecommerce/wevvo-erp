# Consolidação inicial do banco de dados — Wevvo ERP/PDV

Etapa aplicada de forma incremental, sem excluir tabelas nem dados existentes.

## Arquivo alterado
- `app.py`

## Arquivo criado
- `CONSOLIDACAO_BANCO.md`

## O que foi consolidado

### 1. Função central de consolidação
Foi criada a função `consolidar_banco_erp(cursor)`, chamada ao final de `criar_tabelas()`.

Ela roda sempre que o sistema inicializa o banco e apenas adiciona estruturas faltantes.

### 2. Mapa oficial das tabelas do ERP
Criada a tabela `erp_schema_mapa`, que registra qual tabela real atende cada módulo lógico:

- Cadastros → ingredientes, receitas, produtos finais
- Estoque → movimentações e lotes
- Produção → PCP e produções concluídas
- Compras → pedidos de compra
- Vendas → vendas ERP
- Financeiro → lançamentos financeiros
- Qualidade → checklists
- Configurações → usuários

### 3. Auditoria do banco
Criada a tabela `erp_auditoria_banco`, para registrar execuções de consolidação e eventuais alertas.

### 4. Views de compatibilidade
Foram criadas views para evitar quebra entre nomes antigos/novos usados por telas, rotas e validações:

- `receita_ingredientes` → origem real: `receita_itens`
- `produtos_acabados` → origem real: `produtos_finais`
- `producao` → origem real: `producoes`
- `lotes` → origem real: `estoque_produto_acabado`
- `compras` → origem real: `compras_pedidos`
- `contas_pagar` → origem real: `financeiro_lancamentos` filtrado por despesa
- `contas_receber` → origem real: `financeiro_lancamentos` filtrado por receita
- `qualidade_registros` → origem real: `qualidade_checklists` + `qualidade_temperaturas`

### 5. Índices de apoio
Foram adicionados índices seguros para melhorar consultas integradas em receitas, estoque, produção, vendas, compras, financeiro e CMV.

## Validação realizada
- `app.py` compila sem erro de sintaxe.
- `validar_projeto.py` executa sem erros críticos.
- Nenhuma tabela existente foi removida.
- Nenhum dado foi apagado.

## Próxima etapa recomendada
Consolidar o motor central de cálculo usando esse mapa de tabelas, começando por:

1. CMV oficial
2. Compras → Estoque → Financeiro
3. Vendas → Estoque → Financeiro
4. Produção → Lotes → Qualidade
