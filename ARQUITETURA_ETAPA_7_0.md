# Etapa 7.0 — Refatoração de Arquitetura

Esta etapa reorganiza internamente o projeto sem alterar a interface do usuário nem o funcionamento dos módulos existentes.

## Alterações aplicadas

- Criação de `config.py` para centralizar constantes do sistema.
- Criação de `services/core.py` para funções utilitárias compartilhadas.
- Criação da estrutura de pastas para evolução modular: `routes`, `services`, `models`, `database`, `static` e `backups`.
- `app.py` continua registrando as rotas atuais para preservar todos os vínculos existentes.

## Próximo passo seguro

Migrar rotas gradualmente para Blueprints, um módulo por vez, validando cada módulo antes de remover qualquer código do `app.py`.
