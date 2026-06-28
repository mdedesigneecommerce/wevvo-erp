# Etapa 13.1 – Automação do Controle de Qualidade

Atualização incremental do módulo Qualidade.

## Alterações aplicadas

- Checklist padrão fixo:
  - Touca
  - Luva
  - Uniforme
  - Bancada limpa
- Itens do checklist aparecem marcados por padrão.
- Ao desmarcar qualquer item, o sistema exige observação e abre uma Não Conformidade automaticamente.
- Temperaturas padrão fixas:
  - Fabricação a 60º
  - Ultracongelamento a -18º em até 1 hora
- Ao registrar produção ou concluir OP, o ERP cria automaticamente registros de qualidade conformes.
- O registro automático inclui checklist padrão, temperatura de fabricação, temperatura de ultracongelamento e auditoria.

## Arquivos alterados

- app.py
- templates/index.html

## Observação

Nenhum módulo existente foi removido. As alterações foram feitas de forma incremental, mantendo vínculos com Produção, PCP, Lotes e Qualidade.
