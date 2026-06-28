# Auditoria técnica inicial — Wevvo ERP/PDV

Esta etapa não adiciona novos módulos. Ela estabiliza a forma de validar o projeto antes das próximas alterações.

## O que foi verificado

- `app.py` compila sem erro de sintaxe.
- O projeto possui `app.py`, `templates/index.html`, `services/core.py` e `services/automacoes.py`.
- O `app.py` ainda concentra a maior parte da lógica do ERP.
- A estrutura `models/`, `routes/` e `services/` existe, mas ainda precisa ser usada de verdade na refatoração.

## O que foi acrescentado

- `validar_projeto.py`: script local para validar sintaxe, rotas e duplicidades técnicas sem iniciar o Flask.
- Endpoint `/api/auditoria_tecnica_codigo`: diagnóstico leve do código pelo próprio sistema.

## Resultado desta etapa

- Nenhuma regra de negócio existente foi removida.
- Nenhuma rota existente foi removida.
- Nenhuma tabela do banco foi alterada.
- A base agora tem uma validação mínima antes de novas entregas.

## Próxima etapa recomendada

Refatorar gradualmente o `app.py`, começando por funções auxiliares e serviços centrais:

1. Conversão de valores (`parse_float_br`, datas, moeda).
2. Cálculo oficial de CMV, margem, markup e lucro.
3. Serviços de compras, estoque, produção e financeiro.
4. Rotas separadas por módulo.
