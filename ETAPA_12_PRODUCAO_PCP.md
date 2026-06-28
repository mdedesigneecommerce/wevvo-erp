# Etapa 12 – Produção Industrial / PCP

Implementado no módulo Produção:

- Ordem de Produção (OP) com código automático.
- Planejamento por ficha técnica, quantidade, data, turno, responsável, prioridade e capacidade/hora.
- Indicadores de PCP: OPs abertas, concluídas, quantidade planejada e horas previstas.
- Controle de status: Planejada, Liberada, Em produção, Concluída e Cancelada.
- Concluir/Produzir OP usando a produção existente do ERP.
- Ao concluir uma OP, o sistema registra produção, baixa ingredientes, gera lote e atualiza estoque conforme vínculos já existentes.

Arquivos alterados:

- app.py
- templates/index.html
- ETAPA_12_PRODUCAO_PCP.md
