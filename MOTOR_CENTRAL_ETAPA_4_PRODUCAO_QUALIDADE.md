# Motor Central ERP — Etapa 4: Produção, Lotes e Qualidade

## O que foi implementado

Foi adicionada ao `services/erp_motor.py` a rotina `sincronizar_producao_lotes_qualidade(cursor)`.

Ela complementa produções já cadastradas e mantém o vínculo entre:

- Produção
- Produto acabado
- Estoque de produto acabado
- Lote
- Qualidade
- Alertas do motor central

## Regras aplicadas

- Não remove dados existentes.
- Não duplica lotes já vinculados à mesma produção.
- Não duplica checklists de qualidade para o mesmo lote.
- Se a produção tiver produto acabado vinculado, garante o registro em `estoque_produto_acabado`.
- Se a produção não tiver produto acabado vinculado, registra alerta em `erp_motor_alertas`.
- Para produções antigas sem qualidade, cria automaticamente:
  - Touca
  - Luva
  - Uniforme
  - Bancada limpa
  - Fabricação a 60 °C
  - Ultracongelamento a -18 °C

## Validação

- `app.py` compila sem erro.
- `services/erp_motor.py` compila sem erro.
- O motor central foi executado em uma cópia do banco real para validar a rotina sem alterar o banco original durante o teste.
