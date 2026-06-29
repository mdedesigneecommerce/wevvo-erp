# Sprint 03 — Fundação segura de Blueprints

## Objetivo

Preparar a arquitetura modular do Wevvo ERP/PDV para migração gradual do `app.py`, sem alterar regras de negócio e sem mover rotas nesta etapa.

## Decisão técnica

O `app.py` possui mais de 10 mil linhas e concentra rotas, regras de negócio, consultas SQL e integrações. Por segurança, esta Sprint cria a fundação modular, mas mantém todas as rotas atuais no `app.py`.

A migração real será feita módulo por módulo, somente quando cada arquivo estiver completo e testável.

## Arquivos alterados

- `.gitignore`
- `routes/__init__.py`
- `routes/api_routes.py`
- `routes/compras_routes.py`
- `routes/dashboard_routes.py`
- `routes/estoque_routes.py`
- `routes/financeiro_routes.py`
- `routes/producao_routes.py`
- `routes/qualidade_routes.py`
- `utils/validar_sprint_03.py`
- `tests/test_sprint_03_blueprints.py`
- `docs/SPRINT_03_FUNDACAO_BLUEPRINTS.md`

## O que foi preservado

- Nenhuma rota foi removida do `app.py`.
- Nenhuma regra de negócio foi alterada.
- Nenhum vínculo entre estoque, receitas, produção, compras, financeiro, qualidade e CMV foi alterado.
- O sistema continua inicializando pelo `app.py` atual.

## Como testar

Na raiz do projeto, executar:

```powershell
python utils/validar_sprint_03.py
```

Depois rodar o sistema normalmente:

```powershell
python app.py
```

Validar manualmente:

1. Tela inicial abre.
2. Dashboard gerencial abre.
3. Dashboard financeiro abre.
4. Estoque abre.
5. Compras abre.
6. Financeiro abre.
7. Qualidade abre.

## Mensagem de commit sugerida

```text
chore: consolidar fundação de blueprints da arquitetura modular
```

## Próxima Sprint

A próxima Sprint deve migrar apenas um módulo pequeno e isolado para validar o padrão completo. A sugestão é iniciar por um módulo de consulta/API com baixo risco antes de migrar Dashboard, Financeiro ou Estoque.
