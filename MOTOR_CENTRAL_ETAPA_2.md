# Motor Central do ERP - Etapa 2

Alteração incremental realizada sobre a base atual.

## O que foi ajustado

- O motor central agora recalcula CMV também das receitas, não apenas dos produtos finais.
- As configurações já digitadas pelo usuário em CMV/Precificação são preservadas:
  - embalagem unitária;
  - custo fixo unitário;
  - custo variável percentual;
  - margem desejada;
  - preço praticado.
- Quando a receita não tem custo salvo, o motor tenta recalcular pela ficha técnica (`receita_itens`).
- O endpoint `/api/erp/motor/sincronizar` passa a retornar separadamente:
  - produtos com saldo atualizado;
  - receitas com CMV recalculado;
  - produtos com CMV recalculado;
  - alertas de estoque criados.

## Validação técnica

- `app.py` compilado com sucesso.
- `services/erp_motor.py` compilado com sucesso.
- Nenhuma tabela foi removida.
- Nenhuma função existente foi apagada.
