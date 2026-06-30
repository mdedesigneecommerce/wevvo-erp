# Sprint 09 — Validação de Arquitetura e Proteção do Repositório

## Objetivo

Criar uma camada de validação técnica antes de continuar a extração de rotas do `app.py`, garantindo que o projeto permaneça estável durante a modularização do Wevvo ERP/PDV.

## Arquivos alterados

- `.gitignore`
- `docs/SPRINT_09_VALIDACAO_ARQUITETURA.md`
- `utils/validar_sprint_09.py`

## Decisão técnica

Nesta Sprint não foi movida regra de negócio. O foco foi criar proteção para o repositório e validação automática da arquitetura antes das próximas extrações reais de módulos.

Isso evita que arquivos locais, banco SQLite, ambiente virtual, cache Python e relatórios temporários entrem no GitHub.

## Como testar

Execute na raiz do projeto:

```powershell
python utils/validar_sprint_09.py
python app.py
```

A validação verifica:

- existência dos arquivos principais;
- existência das pastas de arquitetura modular;
- existência dos Blueprints já preparados;
- compilação sintática do `app.py` e dos arquivos de rotas;
- ausência de rotas duplicadas no `app.py`.

## Mensagem de commit sugerida

```bash
git add .
git commit -m "chore: adicionar validação técnica da arquitetura modular"
```
