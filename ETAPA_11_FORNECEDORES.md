# Etapa 11 – Fornecedores

Implementação incremental do módulo de Fornecedores, preservando os módulos existentes.

## Incluído

- Novo menu/módulo **Fornecedores**.
- Cadastro completo de fornecedor com:
  - PF/PJ;
  - CPF/CNPJ;
  - contato;
  - telefone;
  - WhatsApp;
  - e-mail;
  - CEP;
  - endereço automático;
  - número;
  - complemento;
  - bairro;
  - cidade;
  - UF;
  - prazo padrão;
  - avaliação;
  - condição de pagamento;
  - produtos fornecidos;
  - observações;
  - status.
- Busca automática de CEP via ViaCEP no front-end, seguindo o mesmo padrão do cadastro de clientes.
- Vinculação de fornecedor com ingrediente.
- Histórico de preços por fornecedor e ingrediente.
- Ranking/listagem de fornecedores.
- Melhores preços por ingrediente.
- Integração com Compras Profissionais:
  - cotações podem usar fornecedor cadastrado;
  - pedidos podem receber fornecedor_id;
  - histórico de preços é alimentado por cotação vinculada.

## Arquivos alterados

- app.py
- templates/index.html
