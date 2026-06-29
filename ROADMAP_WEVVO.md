# ROADMAP WEVVO ERP/PDV

## Visão do Projeto

O Wevvo ERP/PDV é um sistema completo para Food Service, integrando gestão operacional, produção, estoque, vendas, financeiro, precificação, rastreabilidade e relatórios.

O objetivo é construir um ERP moderno, modular e escalável, com interface inspirada em soluções como Bling, porém adaptada à realidade de restaurantes, confeitarias, cozinhas industriais, delivery, mercados internos e pequenos negócios alimentícios.

---

## Objetivos Gerais

* Criar um ERP/PDV completo para Food Service.
* Manter todos os módulos integrados.
* Preservar vínculos existentes entre receitas, ingredientes, estoque, produtos acabados, vendas e financeiro.
* Refatorar gradualmente o `app.py`.
* Migrar o sistema para uma arquitetura organizada com `routes`, `services`, `models` e `templates`.
* Trabalhar sempre com Git e GitHub.
* Evitar troca de arquivos ZIP.
* Documentar cada Sprint.

---

## Arquitetura Atual

Estrutura inicial baseada em Flask:

```text
wevvo-erp/
├── app.py
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   ├── js/
│   └── img/
├── data/
├── requirements.txt
└── README.md
```

Atualmente, parte importante da lógica ainda está concentrada em `app.py`.

---

## Arquitetura Desejada

Refatoração gradual:

```text
wevvo-erp/
├── app.py
├── routes/
│   ├── dashboard_routes.py
│   ├── cadastro_routes.py
│   ├── estoque_routes.py
│   ├── receitas_routes.py
│   ├── producao_routes.py
│   ├── vendas_routes.py
│   ├── financeiro_routes.py
│   └── relatorios_routes.py
├── services/
│   ├── precificacao_service.py
│   ├── estoque_service.py
│   ├── vendas_service.py
│   ├── financeiro_service.py
│   └── rastreabilidade_service.py
├── models/
│   ├── ingrediente.py
│   ├── receita.py
│   ├── produto.py
│   ├── venda.py
│   └── movimentacao.py
├── templates/
├── static/
├── data/
├── docs/
└── tests/
```

---

## Módulos Principais

### Dashboard

Visão geral do sistema, indicadores operacionais, financeiros e produtivos.

### Cadastros

Clientes, fornecedores, ingredientes, produtos, receitas, combos e configurações.

### Estoque

Entradas, saídas, movimentações, saldo, custo médio, alertas e rastreabilidade.

### Receitas e Produção

Ficha técnica, custo de receita, rendimento, produção, produto acabado e vínculo com estoque.

### CMV e Precificação

Custo real, markup percentual, margem, lucro real, preço sugerido e análise profissional.

### PDV / Vendas

Registro de vendas, baixa automática de estoque, produto acabado, formas de pagamento e histórico.

### Financeiro

Receitas, despesas, lucro, fluxo de caixa, contas e integração com vendas.

### Relatórios

Relatórios por módulo, exportações, análise gerencial e indicadores estratégicos.

---

## Rebranding Definitivo

Nome oficial do sistema:

**Wevvo ERP/PDV**

Aplicar gradualmente em:

* Título do sistema.
* Cabeçalho.
* Menu.
* Rodapé.
* README.
* Documentação.
* Nome visual da interface.
* Mensagens internas.
* Identidade de arquivos futuros.

Evitar nomes antigos ou genéricos como:

* Sistema Food Service
* ERP Alimentar
* Controle Nutricional
* Projeto Tabela Nutricional

---

## Diretrizes de Desenvolvimento

* Nunca quebrar integrações existentes.
* Sempre testar módulos relacionados antes de concluir uma alteração.
* Implementar mudanças pequenas e rastreáveis.
* Criar branch por Sprint.
* Usar commits claros.
* Documentar objetivo, arquivos alterados, testes e mensagem de commit.
* Refatorar sem reescrever tudo de uma vez.
* Priorizar estabilidade antes de novas telas.

---

# Sprint 01 — Roadmap, Arquitetura e Rebranding Inicial

## Objetivo

Criar a documentação oficial do projeto, registrar a arquitetura atual e desejada, e iniciar o rebranding definitivo para **Wevvo ERP/PDV**.

## Arquivos Alterados

* `ROADMAP_WEVVO.md`
* `README.md` se existir
* `app.py` somente se houver nomes antigos visíveis
* `templates/index.html` somente se houver nomes antigos visíveis

## Como Testar

1. Rodar o sistema localmente.
2. Conferir se o sistema continua abrindo normalmente.
3. Verificar se o nome visual aparece como **Wevvo ERP/PDV**.
4. Confirmar que menus, módulos e integrações existentes continuam funcionando.
5. Conferir se o arquivo `ROADMAP_WEVVO.md` aparece no GitHub após o push.

## Mensagem de Commit Sugerida

```text
docs: criar roadmap inicial e iniciar rebranding Wevvo ERP/PDV
```

---

## Próximas Sprints Previstas

### Sprint 02 — Organização Inicial do Projeto

Criar estrutura `routes`, `services`, `models` e `docs` sem alterar regras de negócio.

### Sprint 03 — Refatoração Segura do Dashboard

Separar rotas e serviços do Dashboard mantendo indicadores atuais.

### Sprint 04 — Estoque e Rastreabilidade

Revisar movimentações, vínculos e rastreamento de produtos.

### Sprint 05 — Receitas, Produção e Produto Acabado

Garantir integração entre ficha técnica, produção e estoque final.

### Sprint 06 — CMV e Precificação Profissional

Aprimorar markup, margem, lucro real, preço sugerido e relatórios.

### Sprint 07 — PDV e Vendas

Fortalecer baixa automática, formas de pagamento e integração financeira.

### Sprint 08 — Financeiro Integrado

Consolidar receitas, despesas, lucro, fluxo de caixa e análise gerencial.

### Sprint 09 — Relatórios Gerenciais

Criar relatórios por módulo e visão executiva do negócio.

### Sprint 10 — Padronização Visual

Finalizar interface moderna estilo Bling adaptada ao Food Service.
