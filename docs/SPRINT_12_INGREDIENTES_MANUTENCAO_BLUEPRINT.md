# Sprint 12 — Ingredientes Manutenção Blueprint

## Objetivo

Migrar para o Blueprint de ingredientes os endpoints de manutenção/cadastro de ingredientes, mantendo a regra de negócio original intacta para reduzir risco.

## Arquivos alterados

- `routes/ingredientes_routes.py`
- `docs/SPRINT_12_INGREDIENTES_MANUTENCAO_BLUEPRINT.md`
- `utils/validar_sprint_12.py`

## Estratégia técnica

Nesta sprint, os endpoints de escrita passam a existir no Blueprint `ingredientes_bp`, mas ainda delegam para as funções consolidadas no `app.py`.

Essa abordagem evita quebrar integrações existentes e prepara a extração definitiva da regra de negócio para `services/ingredientes_service.py` em etapa futura.

## Endpoints cobertos

- `POST /salvar_ingrediente_admin`
- `DELETE /excluir_ingrediente/<int:ingrediente_id>`
- `POST /atualizar_preco_ingrediente`
- `POST /reajustar_preco_ingrediente`
- `POST /cadastrar_ingrediente`

## Como testar

1. Executar `python utils/validar_sprint_12.py`.
2. Executar `python app.py`.
3. Abrir o ERP no navegador.
4. Testar cadastro, edição, exclusão e reajuste de preço de ingredientes.
5. Confirmar que receitas que utilizam ingredientes continuam recalculando corretamente.

## Mensagem de commit sugerida

```bash
git commit -m "refactor: migrar manutenção de ingredientes para blueprint"
```
