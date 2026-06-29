"""Pacote de rotas do Wevvo ERP/PDV.

Nesta fase os Blueprints existem como fundação arquitetural.
As rotas antigas permanecem no app.py para preservar 100% da compatibilidade.
A migração será feita módulo por módulo nas próximas Sprints.
"""

from .api_routes import api_bp
from .compras_routes import compras_bp
from .dashboard_routes import dashboard_bp
from .estoque_routes import estoque_bp
from .financeiro_routes import financeiro_bp
from .producao_routes import producao_bp
from .qualidade_routes import qualidade_bp

BLUEPRINTS = (
    dashboard_bp,
    estoque_bp,
    financeiro_bp,
    compras_bp,
    producao_bp,
    qualidade_bp,
    api_bp,
)

__all__ = [
    "api_bp",
    "compras_bp",
    "dashboard_bp",
    "estoque_bp",
    "financeiro_bp",
    "producao_bp",
    "qualidade_bp",
    "BLUEPRINTS",
]
