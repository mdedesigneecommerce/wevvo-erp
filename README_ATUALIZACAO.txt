Atualização v3 - Wevvo

Correção aplicada:
- O módulo Produto Acabado agora sincroniza produtos finais que possuem estoque informado diretamente no cadastro.
- Se o Produto Final tiver estoque inicial e ainda não tiver lote, o sistema cria automaticamente um lote interno de ajuste para aparecer em Produto Acabado.
- Se o estoque do Produto Final for aumentado manualmente, o sistema cria um lote de ajuste com a diferença.
- Se o estoque do Produto Final for reduzido manualmente, o sistema baixa a diferença dos lotes mais recentes.

Observação:
- Produções registradas pelo módulo Produção continuam gerando lote normal, baixando ingredientes e alimentando Produto Acabado automaticamente.
- O lote automático começa com AJ e serve apenas para estoque inicial/ajuste manual.

Como atualizar:
1. Feche o servidor Flask.
2. Faça backup da pasta atual, principalmente do sistema.db.
3. Substitua app.py e templates/index.html pelos arquivos deste pacote.
4. Mantenha o seu sistema.db atual na pasta do projeto.
5. Execute: python app.py
